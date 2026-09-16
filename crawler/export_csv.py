# -*- coding: utf-8 -*-
"""
export_csv.py
data/jobs.json → data/jobs_sheet.csv 로 변환한다.

이 CSV는 GitHub raw URL을 통해 구글시트의 '수집원본' 탭에서
=IMPORTDATA("https://raw.githubusercontent.com/{user}/{repo}/main/data/jobs_sheet.csv")
로 바로 당겨쓸 수 있다. Google Cloud OAuth·서비스계정 설정이 필요 없다.

행 순서 / 매칭 규칙 (중요):
- jobs.json은 Python dict라 '최초 발견 순서'가 삽입 순서로 그대로 유지되고,
  이 파일도 그 순서를 그대로 CSV 행 순서로 옮긴다.
- 다만 구글시트 공고DB 탭에서는 행 번호가 아니라 **posting_id를 키로 한
  XLOOKUP/INDEX-MATCH**로 값을 가져오는 것을 전제로 한다. 이렇게 하면
  향후 CSV 행 순서가 어떤 이유로든 바뀌어도, 사람이 입력해둔 카드후보·
  우선순위·훅·헷 한줄평 등이 엉뚱한 공고에 붙는 사고가 나지 않는다.
  (posting_id는 A열에 고정, 공고DB 탭 수식 예시는 README 참고)
"""
import csv
import json
from pathlib import Path

from score_jobs import score_job

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOBS_PATH = DATA_DIR / "jobs.json"
CSV_PATH = DATA_DIR / "jobs_sheet.csv"

# 구글시트 '수집원본' 탭에 들어갈 컬럼. 여기까지는 크롤러가 채우고,
# 익명화·훅·CTA·헷코멘트·카드후보 여부 등은 공고DB 탭에서 사람이 작성한다.
COLUMNS = [
    "posting_id",       # 시트 매칭 키 (XLOOKUP 기준열)
    "status",           # active | closed
    "headhunting_company",
    "url",
    "title",
    "company_desc",
    "main_duties",
    "requirements",
    "preferred",
    "career",
    "location",
    "salary",
    "deadline",
    "first_seen_at",
    "last_seen_at",
    "last_fetched_at",
    "score",
]


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def main():
    jobs = load_json(JOBS_PATH, {})

    if not jobs:
        print("[export_csv] jobs.json이 비어 있습니다. discover→parse_job 먼저 실행하세요.")
        with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(COLUMNS)
        return

    # jobs.values()는 삽입 순서(= 최초 발견 순서)를 그대로 따른다.
    rows = []
    for job in jobs.values():
        row = {k: job.get(k, "") for k in COLUMNS if k != "score"}
        row["score"] = score_job(job)
        rows.append(row)

    active_n = sum(1 for j in jobs.values() if j.get("status") == "active")
    closed_n = sum(1 for j in jobs.values() if j.get("status") == "closed")

    # utf-8-sig: 엑셀/구글시트에서 한글이 깨지지 않도록 BOM 포함
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"[export_csv] {len(rows)}행 저장 → {CSV_PATH} (진행중 {active_n} / 마감 {closed_n})")


if __name__ == "__main__":
    main()
