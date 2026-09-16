# -*- coding: utf-8 -*-
"""
discover.py
리멤버 채용공고 검색에서 '헤딩' 키워드로 노출되는 공고 URL만 수집한다.
검색 결과 '목록'은 클라이언트 사이드에서 동적으로 채워지므로 이 단계만
Playwright로 실제 브라우저를 띄워 검색을 실행한다.

⚠ 여기서 찾은 URL은 아직 '헤딩써치 공고인지 확정 안 된' 후보일 뿐이다.
   검색어 '헤딩'이 우연히 포함된 다른 업체 공고가 섞일 수 있으므로,
   실제 확정 검증은 parse_job.py가 상세페이지 본문에서 '헤딩써치'
   문자열을 확인한 뒤에 이루어진다 (discover.py는 후보 수집만 담당).

실행 환경: GitHub Actions (playwright install 필요, 인터넷 접근 필요)
로컬 실행: pip install playwright && playwright install chromium
"""
import json
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

SEARCH_URL = (
    "https://career.rememberapp.co.kr/job/postings"
    '?search=%7B%22includeAppliedJobPosting%22%3Afalse%2C'
    '%22leaderPosition%22%3Afalse%2C%22organizationType%22%3A%22all%22%2C'
    '%22applicationType%22%3A%22all%22%2C%22keywords%22%3A%5B%22헤딩%22%5D%7D'
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

POSTING_URL_RE = re.compile(r"/job/posting/\d+")


def discover_posting_urls(headless: bool = True, wait_ms: int = 4000) -> list[str]:
    urls: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(SEARCH_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(wait_ms)

        prev_count = -1
        for _ in range(10):
            hrefs = page.eval_on_selector_all(
                "a[href*='/job/posting/']", "els => els.map(e => e.href)"
            )
            for h in hrefs:
                if POSTING_URL_RE.search(h):
                    urls.add(h.split("?")[0])

            if len(urls) == prev_count:
                break
            prev_count = len(urls)

            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(1200)

        browser.close()

    return sorted(urls)


def main():
    urls = discover_posting_urls()
    out_path = DATA_DIR / "discovered_urls.json"
    payload = {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "count": len(urls),
        "urls": urls,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[discover] 후보 {len(urls)}건 수집 → {out_path} (헤딩써치 확정은 parse_job.py에서)")


if __name__ == "__main__":
    main()
