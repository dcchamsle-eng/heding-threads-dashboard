# -*- coding: utf-8 -*-
"""
parse_job.py (v3 — 검증 + 신규/변경/마감 상태추적)

역할이 커졌다:
  1) discover.py가 찾은 '헤딩' 검색 후보 URL 각각에 대해 상세페이지를 열어
     본문에 '헤딩써치'가 실제로 등장하는지 확인한다. 확인 안 되면 저장하지
     않는다 (다른 업체 공고나 우연한 키워드 매칭을 걸러내는 필터).
  2) 매일 discover 결과에 나타나는 posting_id마다 last_seen_at을 갱신한다.
  3) 처음 보는 공고 → event=new
  4) 이미 알고 있는 공고인데 7일 이상 재확인을 안 했다면 다시 상세페이지를
     열어 핵심 필드가 바뀌었는지 확인 → 바뀌었으면 event=changed
     (바뀌지 않았다면 매일 재파싱하지 않음 — 부하 절감)
  5) 검색 결과에서 사라진 공고는 바로 마감 처리하지 않는다.
     missed_count를 늘리다가 2회 연속 미노출이면 상세페이지를 직접 열어
     재확인하고, 그때도 실제로 종료됐으면 event=closed로 남긴다.
     (페이지가 여전히 살아있으면 오탐으로 보고 missed_count를 리셋한다.)

상세페이지는 SSR이라 requests + BeautifulSoup만으로 처리한다.
discover.py(검색 목록 발견)만 Playwright를 계속 사용한다.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOBS_PATH = DATA_DIR / "jobs.json"
HISTORY_PATH = DATA_DIR / "job_history.json"
DISCOVERED_PATH = DATA_DIR / "discovered_urls.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
REQUEST_DELAY_SEC = 1.0
RECHECK_INTERVAL_DAYS = 7  # 진행중 공고 재확인 주기
MISS_THRESHOLD = 2  # 이만큼 연속 미노출되면 마감 여부 재확인
HEADHUNTING_COMPANY = "헤딩써치"

CLOSED_MARKERS = ["채용이 마감", "지원이 마감", "마감된 공고", "종료된 공고", "모집이 마감"]


# ── 유틸 ──────────────────────────────────────────────
def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")


def days_since(iso_str: str) -> float:
    if not iso_str:
        return 9999
    delta = datetime.now(timezone.utc) - parse_iso(iso_str)
    return delta.total_seconds() / 86400


def posting_id_from_url(url: str) -> str:
    parts = url.rstrip("/").split("/")
    return parts[-1].split("?")[0]


def text_or_none(soup: BeautifulSoup, selector: str) -> str | None:
    el = soup.select_one(selector)
    if not el:
        return None
    t = el.get_text(separator="\n", strip=True)
    return t or None


def content_hash(fields: dict) -> str:
    """핵심 필드만 묶어 변경 감지용 해시를 만든다."""
    import hashlib

    keys = [
        "title", "main_duties", "requirements", "preferred",
        "career", "location", "salary", "deadline",
    ]
    blob = "|".join(str(fields.get(k) or "") for k in keys)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ── 상세페이지 fetch ──────────────────────────────────
def fetch_detail(url: str) -> dict:
    """상세페이지를 열어 필드를 파싱한다.

    반환값은 항상 {"fetch_status": "ok" | "not_found" | "error", ...} 형태다.
    "페이지가 확인상 없어졌다(not_found)"와 "페이지를 못 열었다(error)"를
    엄격히 구분한다 — 이 둘을 같은 None으로 취급하면, GitHub Actions
    실행 순간 리멤버가 잠깐 응답을 안 해도 정상 공고가 마감으로
    오판될 수 있기 때문이다.

    - "ok":        200 응답 + 정상 파싱됨. 필드들과 _full_text 포함.
    - "not_found": 404 응답. 공고가 실제로 삭제됐을 가능성이 높은
                   '마감 후보' 신호로만 쓴다.
    - "error":     타임아웃/연결 실패/429/5xx 등 일시적 문제. 마감 여부를
                   판단할 근거가 아니므로, 호출부는 상태를 바꾸지 않고
                   다음 실행에서 다시 시도해야 한다.

    ⚠ DOM 셀렉터(data-testid 등)는 초안입니다. 실제 페이지 구조 확인 후
    맞게 고쳐야 합니다. og:title/og:description은 상대적으로 안정적이라
    우선 사용하고, 나머지는 DOM 셀렉터를 보조로 둡니다.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
    except requests.RequestException as e:
        print(f"  ! 네트워크 오류(다음 실행에 재시도) {url}: {e}")
        return {"fetch_status": "error"}

    if resp.status_code == 404:
        return {"fetch_status": "not_found"}

    if resp.status_code == 429 or resp.status_code >= 500:
        print(f"  ! 일시적 오류(HTTP {resp.status_code}, 다음 실행에 재시도) {url}")
        return {"fetch_status": "error"}

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        # 404/429/5xx 외의 다른 4xx는 확정 삭제로 단정하지 않고 재시도로 취급
        print(f"  ! HTTP 오류(다음 실행에 재시도) {url}: {e}")
        return {"fetch_status": "error"}

    soup = BeautifulSoup(resp.text, "html.parser")
    full_text = soup.get_text(separator=" ", strip=True)

    og_title = soup.select_one("meta[property='og:title']")
    og_desc = soup.select_one("meta[property='og:description']")

    fields = {
        "fetch_status": "ok",
        "title": (og_title.get("content").strip() if og_title else text_or_none(soup, "h1")),
        "company_desc": (og_desc.get("content").strip() if og_desc else None),
        "main_duties": text_or_none(soup, "[data-testid='main-duties']")
        or text_or_none(soup, "section:has(h2:-soup-contains('주요업무'))"),
        "requirements": text_or_none(soup, "[data-testid='requirements']")
        or text_or_none(soup, "section:has(h2:-soup-contains('자격요건'))"),
        "preferred": text_or_none(soup, "[data-testid='preferred']")
        or text_or_none(soup, "section:has(h2:-soup-contains('우대사항'))"),
        "career": text_or_none(soup, "[data-testid='career-level']"),
        "location": text_or_none(soup, "[data-testid='location']"),
        "salary": text_or_none(soup, "[data-testid='salary']"),
        "deadline": text_or_none(soup, "[data-testid='deadline']"),
    }
    fields["_full_text"] = full_text
    return fields


def is_verified_headhunting_company(full_text: str) -> bool:
    """상세페이지 본문에 '헤딩써치'가 실제로 등장하는지 확인한다.
    검색어 '헤딩'이 우연히 포함된 무관한 공고를 걸러내기 위한 필터다."""
    return HEADHUNTING_COMPANY in full_text


def is_marked_closed(full_text: str) -> bool:
    return any(marker in full_text for marker in CLOSED_MARKERS)


# ── 메인 로직 ─────────────────────────────────────────
def main():
    discovered = load_json(DISCOVERED_PATH, {"urls": []})
    jobs = load_json(JOBS_PATH, {})
    history = load_json(HISTORY_PATH, [])

    now = now_iso()
    candidates = {posting_id_from_url(u): u for u in discovered["urls"]}

    new_count = changed_count = closed_count = rejected_count = 0
    error_count = 0  # 네트워크 오류로 이번 실행에 판단 보류된 건수

    # 1) 오늘 검색에서 발견된 후보들 처리
    for pid, url in candidates.items():
        if pid not in jobs:
            # 신규 후보 → 상세 열어서 헤딩써치인지 검증
            detail = fetch_detail(url)
            time.sleep(REQUEST_DELAY_SEC)

            if detail["fetch_status"] == "error":
                error_count += 1
                continue  # 일시적 오류, 다음 실행에 재시도 (거부도 저장도 안 함)
            if detail["fetch_status"] == "not_found":
                print(f"  x 상세페이지 없음(404), 제외: {url}")
                continue  # 확정 삭제 — 애초에 저장 안 하므로 closed 처리 불필요
            if not is_verified_headhunting_company(detail["_full_text"]):
                print(f"  x 헤딩써치 아님, 제외: {url}")
                rejected_count += 1
                continue

            job = {
                "posting_id": pid,
                "url": url,
                "headhunting_company": HEADHUNTING_COMPANY,
                **{k: v for k, v in detail.items() if k not in ("_full_text", "fetch_status")},
                "status": "active",
                "first_seen_at": now,
                "last_seen_at": now,
                "last_fetched_at": now,
                "missed_count": 0,
                "content_hash": content_hash(detail),
            }
            jobs[pid] = job
            history.append({"posting_id": pid, "event": "new", "at": now})
            new_count += 1
            print(f"  + new: {job.get('title')}")

        else:
            # 기존 공고 → 오늘 다시 보였으니 last_seen_at 갱신, miss 리셋
            job = jobs[pid]
            job["last_seen_at"] = now
            job["missed_count"] = 0

            just_reopened = False
            if job["status"] == "closed":
                job["status"] = "active"
                just_reopened = True
                history.append({"posting_id": pid, "event": "reopened", "at": now})
                print(f"  ~ reopened: {job.get('title')}")

            # 재확인 주기(7일)가 지났거나, 방금 재오픈됐다면 즉시 다시 파싱한다.
            # 재오픈은 7일 조건을 기다리지 않는다 — 재등록 과정에서 JD가
            # 바뀌었을 가능성이 있어 낡은 내용으로 카드가 나가면 안 되기 때문.
            needs_recheck = just_reopened or days_since(job.get("last_fetched_at")) >= RECHECK_INTERVAL_DAYS
            if needs_recheck:
                detail = fetch_detail(url)
                time.sleep(REQUEST_DELAY_SEC)

                if detail["fetch_status"] == "error":
                    error_count += 1
                    print(f"  ! 재확인 중 네트워크 오류, 다음 실행에 재시도: {job.get('title')}")
                    # last_fetched_at을 갱신하지 않는다 → 다음 실행에서 다시 조건 충족
                elif detail["fetch_status"] == "not_found":
                    # 검색엔 잡혔는데 상세는 404인 드문 케이스. 여기서 바로
                    # closed 처리하지 않고, 다음 '미노출 누적' 경로에 맡긴다.
                    print(f"  ! 검색엔 보였지만 상세 접근 불가(404): {job.get('title')}")
                else:  # ok
                    new_hash = content_hash(detail)
                    if new_hash != job.get("content_hash"):
                        for k, v in detail.items():
                            if k not in ("_full_text", "fetch_status"):
                                job[k] = v
                        job["content_hash"] = new_hash
                        history.append({"posting_id": pid, "event": "changed", "at": now})
                        changed_count += 1
                        print(f"  * changed: {job.get('title')}")
                    job["last_fetched_at"] = now
            jobs[pid] = job

    # 2) 오늘 검색에 안 보인, 기존 active 공고 처리 (마감 후보)
    candidate_ids = set(candidates.keys())
    for pid, job in jobs.items():
        if job.get("status") != "active" or pid in candidate_ids:
            continue

        job["missed_count"] = job.get("missed_count", 0) + 1

        if job["missed_count"] >= MISS_THRESHOLD:
            detail = fetch_detail(job["url"])
            time.sleep(REQUEST_DELAY_SEC)

            if detail["fetch_status"] == "not_found":
                # 확정 삭제 — 마감 처리
                job["status"] = "closed"
                history.append({"posting_id": pid, "event": "closed", "at": now})
                closed_count += 1
                print(f"  - closed (404): {job.get('title')}")
            elif detail["fetch_status"] == "ok":
                if is_marked_closed(detail.get("_full_text", "")):
                    job["status"] = "closed"
                    history.append({"posting_id": pid, "event": "closed", "at": now})
                    closed_count += 1
                    print(f"  - closed (마감 문구 확인): {job.get('title')}")
                else:
                    # 페이지는 살아있는데 검색에만 안 잡힌 경우 → 오탐으로 보고 리셋
                    job["missed_count"] = 0
                    job["last_fetched_at"] = now
            else:  # error — 마감 여부를 판단할 근거가 없다. 상태를 바꾸지 않는다.
                error_count += 1
                print(
                    f"  ! 마감 재확인 중 네트워크 오류, 판정 보류"
                    f"(다음 실행 재시도): {job.get('title')}"
                )
                # missed_count는 이번에 늘어난 채로 두되 status/last_fetched_at은
                # 건드리지 않는다 → 다음 실행에서 같은 조건으로 다시 재확인 시도
        jobs[pid] = job

    save_json(JOBS_PATH, jobs)
    save_json(HISTORY_PATH, history)

    print(
        f"[parse_job] 완료 — 신규 {new_count} / 변경 {changed_count} / "
        f"마감 {closed_count} / 헤딩써치 아니어서 제외 {rejected_count} / "
        f"네트워크 오류로 보류 {error_count} / 전체 추적중 {len(jobs)}건"
    )


if __name__ == "__main__":
    main()
