# 헤딩써치 공고 모니터링 크롤러 (v3 — 1차 운영 배포 기준)

리멤버에 헤딩써치가 등록한 공고를 자동 수집·검증·추적해, 인증 설정 없이
구글시트로 연결하는 파이프라인. 크롤러는 원본 데이터 정리와 신규/변경/마감
판정까지만 하고, 카드 후보 선정·문안 작성은 항상 사람이 시트에서 한다.

## ⚠️ 실행 전 먼저 할 일

**코드부터 돌리지 말고, 내부적으로 먼저 확인하세요:**
리멤버와 함께 헤딩써치가 출범했으니, 리멤버 측에 "헤딩써치 등록 공고를
API·CSV·피드로 받을 수 있는지" 먼저 물어보는 게 가장 안전합니다.
공식 데이터 경로가 있다면 이 크롤러는 필요 없습니다.

## 최종 자동화 흐름 (고정)

```
GitHub Actions 매일 실행
  → discover.py        (검색 후보 URL 수집, Playwright)
  → parse_job.py        (헤딩써치 검증 + 신규/변경/마감 판정, requests+bs4)
  → export_csv.py        (jobs_sheet.csv 생성, 점수 포함)
  → jobs_sheet.csv 커밋
  → Google Sheet 수집원본 탭 자동 갱신 (IMPORTDATA)
  → 공고DB 탭에서 posting_id 기준 XLOOKUP으로 자동 연결
  → 사람이 카드후보 체크
  → 카드제작큐로 자동 전달
  → 오늘의 매물 제작
```

## v2 → v3 변경점

| | v2 | v3 |
|---|---|---|
| 헤딩써치 검증 | 없음 (검색만 되면 저장) | **상세페이지 본문에 '헤딩써치' 등장 확인 후에만 저장** |
| 공고 상태 | 신규만 기록 | **신규(new) / 변경(changed) / 마감(closed) / 재오픈(reopened) 추적** |
| 재파싱 주기 | 신규만, 기존 공고 재확인 없음 | **진행중 공고는 7일마다 재확인**, 그 사이엔 재요청 안 함 |
| 마감 판정 | 없음 | **2회 연속 검색 미노출 → 상세페이지 재확인 → 실제 종료 시에만 closed** |
| 신규성 점수 | 과거 new 이력이 있으면 계속 +30점 | **first_seen_at 기준 7일 이내일 때만 +30점**, 이후 0점 |
| 시트 매칭 | 행 순서 고정에 의존 | **posting_id를 키로 XLOOKUP/INDEX-MATCH** (행 순서가 바뀌어도 안전) |

## 헤딩써치 검증 필터 (중요)

검색어 "헤딩"은 다른 업체명이나 무관한 문맥에 우연히 포함될 수 있습니다.
`parse_job.py`는 상세페이지를 연 뒤, 본문 전체 텍스트에 **"헤딩써치"**라는
문자열이 실제로 등장하는지 확인하고, 확인 안 되면 그 공고는 `jobs.json`에
아예 저장하지 않습니다 (`rejected_count`로 로그만 남김). 저장된 공고에는
`headhunting_company: "헤딩써치"` 필드가 항상 함께 들어갑니다.

## 신규 / 변경 / 마감 상태 추적

각 공고 레코드는 아래 상태 필드를 가집니다.

```json
{
  "status": "active",       // active | closed
  "first_seen_at": "...",    // 최초 발견 시각 (신규성 점수의 기준점)
  "last_seen_at": "...",     // 검색 결과에 가장 최근 나타난 시각
  "last_fetched_at": "...",  // 상세페이지를 가장 최근 실제로 연 시각
  "missed_count": 0,         // 검색 결과에 연속으로 안 보인 횟수
  "content_hash": "..."      // 핵심 필드 해시 (변경 감지용)
}
```

- **신규(new):** 처음 발견 + 헤딩써치 검증 통과
- **변경(changed):** 7일 재확인 주기가 돌아왔고, 핵심 필드 해시가 이전과 다름
- **마감(closed):** 검색 결과에서 2회 연속 사라진 뒤 상세페이지로 재확인해도
  실제로 접근 불가(404)이거나 "마감" 문구가 확인된 경우
- **재오픈(reopened):** closed 처리됐던 공고가 다시 검색에 나타난 경우

진행 중인 공고를 매일 재파싱하지 않고 7일 주기로만 재확인하기 때문에
서버 부하가 크게 줄어듭니다.

## 구글시트 연결 — posting_id 기준 매칭

1. **수집원본** 탭 A1:
   ```
   =IMPORTDATA("https://raw.githubusercontent.com/{GitHub계정}/{저장소명}/main/data/jobs_sheet.csv")
   ```
2. **공고DB** 탭에서는 행 번호가 아니라 **posting_id를 키**로 값을 가져옵니다.
   CSV(수집원본) 열 구조는 `export_csv.py`의 COLUMNS 순서 그대로입니다:
   A=posting_id, B=status, C=headhunting_company, D=url, E=title,
   F=company_desc, G=main_duties, H=requirements, I=preferred, J=career,
   K=location, L=salary, M=deadline, N=first_seen_at, O=last_seen_at,
   P=last_fetched_at, Q=score.

   공고DB 탭은 기존에 만들어둔 구조를 유지하고 **공고ID가 C열**에 있으므로,
   수식은 C열을 기준으로 작성합니다:
   ```
   =XLOOKUP($C2, 수집원본!$A:$A, 수집원본!E:E)   ' title
   =XLOOKUP($C2, 수집원본!$A:$A, 수집원본!G:G)   ' main_duties
   =XLOOKUP($C2, 수집원본!$A:$A, 수집원본!B:B)   ' status (active/closed)
   ```
   (공고DB의 키 열 위치가 다르면 `$C2` 부분만 실제 열로 바꾸면 됩니다.)
   이렇게 하면 다음 IMPORTDATA 갱신에서 수집원본의 행 순서가 바뀌어도
   (마감 공고가 섞이거나 정렬이 달라져도) 공고DB의 각 행은 항상 올바른
   posting_id의 값을 가져옵니다.
3. **공고DB**에서 사람이 직접 작성하는 컬럼: 공개여부, 카드노출명,
   익명화 근무지, 카드후보(TRUE/FALSE), 우선순위, 선정사유, 알짜포인트,
   타겟독자, 채널, 훅, 장점 3개, 헷 한줄평, CTA, 제작상태.
   이 컬럼들은 크롤러가 채우지 않습니다 — 비공개 기업명 추정, 미공개
   연봉 추정을 하지 않는다는 원칙 때문입니다.
4. **카드제작큐**는 공고DB에서 `카드후보=TRUE`인 행만 자동으로 모아
   보여주도록 설정합니다 (QUERY 또는 FILTER 함수 권장).

## 폴더 구조

```
crawler/
  discover.py      # 1단계: 검색 후보 URL 발견 (Playwright)
  parse_job.py       # 2단계: 헤딩써치 검증 + 신규/변경/마감 판정 (requests+bs4)
  score_jobs.py       # 점수 계산 함수 (export_csv.py가 참고 컬럼으로 사용)
  export_csv.py       # 3단계: 구글시트용 CSV 생성
data/
  discovered_urls.json  # 오늘 검색에서 발견된 후보 URL
  jobs.json               # 검증·추적 중인 공고 전체 (posting_id 기준)
  job_history.json        # new/changed/closed/reopened 이력
  jobs_sheet.csv           # 구글시트 IMPORTDATA용 (매 실행마다 갱신)
.github/workflows/
  crawl.yml           # 매일 자동 실행 (GitHub Actions)
```

## 로컬에서 테스트하기

```bash
pip install -r requirements.txt
playwright install chromium   # discover.py에서만 필요

python crawler/discover.py     # data/discovered_urls.json 생성
python crawler/parse_job.py    # 검증 + 신규/변경/마감 판정, jobs.json 갱신
python crawler/export_csv.py   # data/jobs_sheet.csv 생성
```

## GitHub Actions로 자동화하기

1. 이 폴더 전체를 `heding-threads-dashboard` 저장소(또는 새 저장소)에 커밋
2. Settings → Actions → General에서 "Read and write permissions" 켜두기
3. Actions 탭에서 "Run workflow" 버튼으로 첫 실행은 수동으로 한 번 해보기
4. 이후 매일 오전 8시(KST) 자동 실행됨

## ⚠️ DOM 셀렉터는 여전히 초안입니다

`parse_job.py`의 CSS 셀렉터(`[data-testid='...']` 등)는 실제 페이지 구조를
아직 확인 못 한 상태의 추정치입니다. `og:title`/`og:description` 메타태그는
우선 사용하도록 해뒀지만, 주요업무·자격요건·우대사항·근무지 등은 실제
페이지를 한 번 열어 개발자도구(F12)로 확인한 뒤 맞는 선택자로 교체해야
정상 작동합니다. (데스크탑에서 Claude for Chrome을 쓰면 실제 페이지를
같이 보면서 바로 고칠 수 있습니다.)

또한 "마감" 표시 문구(`CLOSED_MARKERS`)도 실제 사이트에서 쓰는 정확한
문구로 확인 후 보정하는 것을 권장합니다.

## 카드 제작 시 CI 안내

카드 구조·헷 캐릭터 운영법은 docs/concept.md 9번 "오늘의 매물"을 그대로 따르되,
**실제 로고·브랜드컬러·CI는 최신 헤딩써치 브랜드(블랙 배경 + 네온그린
`#D0FD17`, "Heding" 워드마크)를 적용**합니다. (docs/concept.md 2번 섹션에
전환 원칙 기록해둠.) 이 규칙은 v3에서도 변경 없습니다.

## 지켜야 할 원칙

- 공개된 텍스트만 그대로 옮깁니다. 비공개 기업명 추정, 미공개 연봉
  추정은 하지 않습니다.
- 검색·상세조회 모두 로그인 없이 공개된 페이지만 대상으로 합니다.
- 요청 간격을 두어 과도한 트래픽을 피합니다. 진행중 공고는 7일에
  한 번만 재확인해 부하를 최소화합니다.
- 리멤버 채용솔루션 이용약관상 별도 계약이 있다면 그 계약이 우선합니다.
