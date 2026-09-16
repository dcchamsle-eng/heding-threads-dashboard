# -*- coding: utf-8 -*-
"""
score_jobs.py
공고를 '카드뉴스로 만들기 좋은 정도'로 점수화하는 순수 함수 모음.
export_csv.py가 이 점수를 CSV의 참고 컬럼으로 넣어준다.

이 점수는 자동으로 카드를 선정하는 기준이 아니라, 구글시트 공고DB 탭에서
사람이 카드후보를 판단할 때 참고하는 신호일 뿐이다. 최종 선정은
공고DB의 '카드후보' 체크(TRUE/FALSE)로 사람이 직접 한다.
"""
from datetime import datetime, timezone

RARE_KEYWORDS = ["FAE", "ASIC", "PM 리더", "팀장", "Leader", "Head of", "임원", "본부장"]
HOT_INDUSTRIES = ["AI", "반도체", "2차전지", "바이오", "이커머스", "뷰티"]
RECENCY_WINDOW_DAYS = 7  # 이 기간 이내 new인 공고만 신규성 점수


def _parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")


def is_recent(job: dict) -> bool:
    """first_seen_at 기준 7일 이내인지. 과거에 한 번 new였다는 이유만으로
    계속 신규 점수를 주지 않도록 first_seen_at을 기준점으로 고정한다."""
    first_seen = job.get("first_seen_at")
    if not first_seen:
        return False
    try:
        delta = datetime.now(timezone.utc) - _parse_iso(first_seen)
    except ValueError:
        return False
    return delta.total_seconds() / 86400 <= RECENCY_WINDOW_DAYS


def score_job(job: dict) -> int:
    score = 0
    text_blob = " ".join(
        str(job.get(k, "")) for k in ["title", "main_duties", "requirements", "career"]
    )

    if is_recent(job):
        score += 30
    if any(kw in text_blob for kw in RARE_KEYWORDS):
        score += 25
    if any(kw in text_blob for kw in HOT_INDUSTRIES):
        score += 15

    filled = sum(1 for k in ["salary", "career", "location"] if job.get(k))
    score += filled * 5

    duties_len = len(job.get("main_duties") or "")
    if duties_len > 40:
        score += 10

    return score
