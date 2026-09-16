from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_PATH = DATA_DIR / "discovered_urls.json"

KEYWORD = "헤딩"
SEARCH_URL = f"https://career.rememberapp.co.kr/job/search?keyword={quote(KEYWORD)}"

POSTING_RE = re.compile(r"/job/posting/\d+")


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            viewport={"width": 1440, "height": 1200}
        )

        print(f"[discover] 검색 URL: {SEARCH_URL}")

        page.goto(
            SEARCH_URL,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        page.wait_for_timeout(5000)

        print(f"[discover] 현재 URL: {page.url}")
        print(f"[discover] 페이지 제목: {page.title()}")

        total_links = page.locator("a").count()
        print(f"[discover] 전체 a 태그 수: {total_links}")

        posting_locator = page.locator('a[href*="/job/posting/"]')
        posting_count = posting_locator.count()

        print(
            f"[discover] /job/posting/ 링크 수: "
            f"{posting_count}"
        )

        for i in range(min(posting_count, 10)):
            href = posting_locator.nth(i).get_attribute("href")
            print(f"[discover] href[{i}]: {href}")

        # 스크롤하면서 동적 로딩 확인
        found_urls = set()
        no_growth_count = 0

        for round_no in range(10):
            hrefs = page.locator("a").evaluate_all(
                """
                els => els
                  .map(a => a.getAttribute('href'))
                  .filter(Boolean)
                """
            )

            before = len(found_urls)

            for href in hrefs:
                match = POSTING_RE.search(href)
                if not match:
                    continue

                path = match.group(0)
                found_urls.add(
                    f"https://career.rememberapp.co.kr{path}"
                )

            after = len(found_urls)

            print(
                f"[discover] 스크롤 {round_no + 1}: "
                f"후보 {after}건"
            )

            if after == before:
                no_growth_count += 1
            else:
                no_growth_count = 0

            if no_growth_count >= 3:
                print(
                    "[discover] 3회 연속 신규 링크 없음 → "
                    "스크롤 종료"
                )
                break

            page.evaluate(
                "window.scrollTo(0, document.body.scrollHeight)"
            )
            page.wait_for_timeout(2000)

        browser.close()

    result = {
        "collected_at": time.strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        ),
        "count": len(found_urls),
        "urls": sorted(found_urls),
    }

    OUTPUT_PATH.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"[discover] 후보 {len(found_urls)}건 수집 "
        f"→ {OUTPUT_PATH} "
        f"(헤딩써치 확정은 parse_job.py에서)"
    )


if __name__ == "__main__":
    main()
