# 졸업작품 AI 서버 통합 인수인계

이 문서는 뉴스 투자영향·중요도 분석, 종목 뉴스 종합 요약과 runtime provider 설정의 통합 안내입니다. 기존 Backend 종목 요약은 AI Server를 통해 처리하도록 연결했습니다. sentiment/importance 분석 proxy와 badge 연결, provider 관리용 proxy/UI는 여전히 별도 팀 작업입니다.

## 구현 완료 기능

| 기능 | 상태 | 동작 |
| --- | --- | --- |
| 뉴스 투자영향 분석 | DONE | 기사별 sentiment를 `POSITIVE`, `NEUTRAL`, `NEGATIVE` 중 하나로 반환 |
| 중요도 분석 | DONE | 기사별 `score` (0~1)와 `LOW`, `MEDIUM`, `HIGH` label 반환 |
| OpenAI provider | DONE | 기본 모델 `gpt-6-luna`, reasoning effort `none` |
| Gemini provider | DONE | 기본 모델 `gemini-3.8-flash` |
| Runtime provider switch | DONE | 현재 프로세스에서 OpenAI/Gemini를 바꾸며 자동 fallback은 하지 않음 |
| 종목 뉴스 종합 요약 | DONE | 기존 Backend 요약 경로를 AI Server로 연결하고 기존 Frontend 응답 계약 유지 |

Sentiment는 기사 문구의 감정이 아니라 기사에 적힌 기업 또는 시장에 예상되는 투자 영향 방향입니다. POSITIVE는 실질적으로 유리한 영향, NEGATIVE는 실질적으로 불리한 영향, NEUTRAL은 영향이 불명확하거나 균형적이거나 정보가 부족한 경우를 뜻합니다.

Importance는 sentiment와 별도로 투자 판단에서 사건이 얼마나 중요한지를 나타냅니다. score는 0 이상 1 이하이며, 0.35 미만은 LOW, 0.35 이상 0.70 미만은 MEDIUM, 0.70 이상은 HIGH입니다. 이 경계는 AI Server schema의 기준입니다.

## AI Server API 계약

기본 주소는 로컬 실행 기준 `http://localhost:8001`입니다. 요청/응답은 JSON입니다.

### POST /analyze/news

기사 1~50건을 한 요청으로 분석합니다. `article_id`와 `title`은 필수이며, 나머지 필드는 선택입니다. 요청 안에서 `article_id`는 중복될 수 없습니다.

Request:

```json
{
  "articles": [
    {
      "article_id": "news-001",
      "title": "Example Corp reports higher quarterly revenue",
      "content": "Revenue increased compared with the same period last year.",
      "url": "https://example.com/news/news-001",
      "source": "Example News",
      "published_at": "2026-10-17T09:30:00Z",
      "symbol_code": "EXMP"
    }
  ]
}
```

- Required: `article_id`, `title` (각각 빈 문자열 불가)
- Optional: `content`, `url`, `source`, `published_at` (ISO 8601 datetime), `symbol_code`
- Batch: 최소 1건, 최대 50건
- 중복 `article_id` 또는 schema 검증 실패: HTTP 422

Response:

```json
{
  "articles": [
    {
      "article_id": "news-001",
      "sentiment": {
        "label": "POSITIVE",
        "confidence": 0.91
      },
      "importance": {
        "score": 0.82,
        "label": "HIGH"
      }
    }
  ]
}
```

응답 article은 입력 순서대로 반환됩니다. 각 입력마다 결과 하나가 있어야 하며, AI provider에 전달된 입력에 없는 사실을 추론하지 않도록 요청합니다.

현재 구현의 주요 오류 응답:

- HTTP 422: 요청 schema 또는 기사 조건 위반
- HTTP 503: 선택된 analyzer 설정을 사용할 수 없음
- HTTP 502, `{"detail":"News analysis failed"}`: provider 분석 요청 또는 응답 처리 실패
- HTTP 502, `{"detail":"News analyzer initialization failed"}`: analyzer 초기화 실패

### POST /summarize/stock-news — Backend 전용 종목 요약

기존 `GET /api/ai/news-summary/{symbol_code}`가 호출하는 내부 API입니다. 최근 기사 최대 5건의 제목·발췌문과 오늘 등락률을 종합합니다. 기사별 요약 `/summarize/news`는 아직 구현하지 않았으며 이 API와 역할이 다릅니다. AI Server는 뉴스 수집이나 URL 방문을 하지 않습니다.

Request 예시:

```json
{
  "symbol_code": "005930",
  "name": "예시 종목",
  "change_rate": 1.2,
  "articles": [
    {
      "title": "예시 기업 실적 발표",
      "summary": "기업이 매출 증가를 발표했다는 발췌문입니다.",
      "source": "예시 언론사",
      "date": "2026.10.18 09:00"
    }
  ]
}
```

- 필수: `symbol_code`, `name`, `articles` 1~5건과 각 `title`.
- 선택: `change_rate`는 유한한 수 또는 null(기본 null), `summary`는 문자열(기본 빈 문자열), `source`/`date`는 문자열 또는 null.
- 길이 제한: 종목 코드 32자, 종목명 200자, 기사 제목 2,000자, 발췌문 20,000자, 출처·날짜 각 200자.
- `date`는 수집된 표시 문자열이며 datetime 변환을 요구하지 않습니다. 발췌문을 전체 기사 본문으로 취급하지 않습니다.

Response 예시:

```json
{
  "headline": "오늘은 +1.2% 올랐고, 기사에서 같이 보이는 이슈는 실적입니다.",
  "summary": ["기업이 매출 증가를 발표했습니다.", "기사만으로 오늘 등락의 원인인지는 확인되지 않습니다."],
  "tags": ["실적"],
  "disclaimer": "모의투자 연습용 설명이며 투자 권유가 아닙니다.",
  "provider": "openai",
  "model": "gpt-6-luna"
}
```

`headline`/`disclaimer`는 비어 있지 않은 문자열, `summary`는 1~3개 문자열, `tags`는 0~3개 문자열입니다. 전체 입력에 대한 종합 결과 하나를 반환하므로 기사별 결과 ID 매핑은 없습니다. provider/model은 요청 시작 시 함께 선택한 실제 실행 정보입니다. 외부 provider 응답에는 이 메타데이터 생성을 맡기지 않습니다.

오류: 입력 검증 실패는 422, provider key 미설정은 503, 초기화·생성·출력 검증 실패는 안전한 502 응답입니다. provider 원본 오류나 key를 반환하지 않으며 다른 provider를 자동 호출하지 않습니다. 요약 호출은 OpenAI 재시도 0회, Gemini 전체 시도 1회로 설정하고 SDK 요청 제한시간은 25초입니다.

### 기존 Backend/Frontend 종목 요약 계약

```text
StockNewsSummary
  -> GET /api/ai/news-summary/{symbol_code} (Backend 인증 유지)
  -> GET /settings/provider (캐시 구분용, 외부 AI 호출 없음)
  -> POST /summarize/stock-news (캐시 miss 시)
  -> 요청 시점에 선택된 OpenAI 또는 Gemini
```

- Backend는 종목명·최근 뉴스 최대 5건·등락률을 기존 방식으로 준비하고 `AI_SERVER_URL`을 사용합니다. 설정 기본값은 `http://localhost:8001`입니다.
- 기존 GET route와 HTTP method, `symbol_code`, `name`, `change_rate`, `headline`, `summary[]`, `tags[]`, `disclaimer` 응답을 유지합니다. 내부 provider/model은 브라우저 응답에서 제외합니다. Frontend 파일 변경은 필요하지 않습니다.
- 뉴스가 없으면 기존처럼 HTTP 200과 빈 summary/tags, 표시할 뉴스가 없다는 안내를 반환하고 요약 생성은 호출하지 않습니다.
- 잘못된 AI_SERVER_URL, 연결 실패, timeout, non-2xx, 공백만 있는 문자열을 포함한 잘못된 JSON/schema는 기존처럼 HTTP 200의 일시 사용 불가 안내와 뉴스 제목 최대 3개로 처리합니다. provider 인증 401/403도 사용자 로그인 오류로 전달하지 않습니다. 원본 뉴스 API는 별도로 유지됩니다.
- Backend 제한시간은 provider 조회 5초, 요약 요청 30초입니다. 자동 재시도하지 않습니다.
- 180초 메모리 캐시는 종목 코드/provider/model별로 구분합니다. 생성 도중 전환되면 응답의 실제 provider/model로 저장합니다. 실패 결과와 빈 뉴스 목록은 캐시하지 않습니다. 뉴스 수집 실패도 빈 목록으로 반환될 수 있으므로, 빈 결과가 복구를 가리지 않도록 다음 요청에서 다시 확인합니다. 캐시 hit에서도 provider 조회는 수행합니다.
- 전환 이후 새 요청부터 적용하며, 이미 진행 중인 요청은 선택 당시 provider를 사용합니다. 이미 화면에 표시된 결과는 새 Backend 요청 전까지 자동으로 바뀌지 않습니다.
- Backend의 기존 Gemini 뉴스 요약 전용 생성·캐시 코드는 제거했습니다. 설문과 코칭은 여전히 Backend에서 Gemini를 직접 사용하므로 해당 설정·라이브러리는 유지합니다.
- 현재 메모리 기반 전환과 캐시는 AI Server single worker 환경을 전제로 합니다. 내부 endpoint는 인증이 없으므로 외부 직접 노출을 금지합니다.

### GET /settings/provider

현재 process의 provider와 해당 설정 모델명을 반환합니다. 이 요청은 외부 AI provider를 호출하지 않습니다.

기본 설정 예시 Response:

```json
{
  "provider": "openai",
  "model": "gpt-6-luna"
}
```

### PUT /settings/provider

허용 provider는 `openai`, `gemini`입니다. 대상 provider의 API key가 설정되어 있어야 합니다. 설정 확인만 하며 provider 변경 자체로 외부 API를 호출하지 않습니다.

Gemini 전환 Request:

```json
{
  "provider": "gemini"
}
```

기본 모델 설정 기준 Response:

```json
{
  "provider": "gemini",
  "model": "gemini-3.8-flash"
}
```

OpenAI로 되돌릴 때는 Request의 provider를 `openai`로 보내며, 기본 설정 Response는 `{"provider":"openai","model":"gpt-6-luna"}`입니다. 실제 응답 model은 서버의 모델 환경 설정에 따라 달라질 수 있습니다.

오류 동작:

- 지원하지 않는 provider: HTTP 422, `{"detail":"Unsupported provider"}`
- 대상 provider key가 없거나 공백: HTTP 503, `{"detail":"Selected provider is not configured"}`
- 두 실패 모두 기존 provider 값을 유지합니다.
- 오류 응답은 제출한 잘못된 provider 문자열이나 API key를 반환하지 않습니다.

Provider 값은 `openai` 또는 `gemini`이며 대소문자와 앞뒤 공백을 정규화합니다. 예를 들어 `OpenAI`와 `GEMINI`는 각각 canonical lowercase `openai`, `gemini`로 처리되고, 응답 provider도 항상 lowercase입니다. 그 외 값은 상태 변경 전에 HTTP 422로 거부됩니다.

## 데이터 흐름

뉴스 분석:

```text
Frontend
  -> Backend news API / authenticated proxy
  -> AI Server POST /analyze/news
  -> 현재 선택된 OpenAI 또는 Gemini
  -> Backend
  -> Frontend
```

Frontend는 기존 Backend 뉴스 API를 통해 뉴스 목록을 먼저 받아 표시합니다. Backend는 필요한 기사 필드를 AI Server request로 전달하고 분석 결과를 연결합니다. AI Server가 오류를 반환해도 Backend는 뉴스 원본 응답을 계속 제공하도록 설계해야 합니다.

Provider 변경:

```text
관리자 또는 시연 UI
  -> 인증된 Backend provider-management proxy
  -> AI Server GET/PUT /settings/provider
  -> Backend가 결과를 전달
  -> UI가 현재 provider/model과 변경 결과를 표시
```

Provider 설정 endpoint는 현재 AI Server에서 인증하지 않습니다. 관리 UI가 필요한 경우에도 Frontend에서 AI Server를 직접 호출하지 말고 Backend를 경유해야 합니다.

## Backend TEAM_REQUEST — 미구현

현재 Backend에는 이 뉴스 분석 AI Server를 호출하는 proxy가 없습니다. Backend route 이름은 아래와 같이 기존 `/api/news`, `/api/ai` prefix 및 AI router 구조에 맞춘 **권장안**이며 아직 구현된 경로가 아닙니다.

- 권장 분석 경로: `POST /api/ai/news-analysis`
- 기존 뉴스 목록 응답의 article ID, title, 본문 및 사용 가능한 선택 필드를 AI Server schema에 맞춰 전달합니다.
- 기존 Backend 인증 정책을 적용하고, 뉴스 본문 등 필요한 데이터만 전달합니다.
- AI Server 연결 오류, timeout, non-2xx 응답, 잘못된 JSON/schema 응답을 처리합니다. timeout 값은 기사 수와 AI 응답 시간을 고려해 정하고 설정 가능하게 관리합니다.
- AI 분석 실패가 기존 뉴스 조회 API의 실패로 이어지지 않도록 합니다. 뉴스 원본을 먼저 제공하고 분석 결과는 누락될 수 있는 별도 데이터로 취급하는 방식을 권장합니다.
- Backend 설정 `AI_SERVER_URL`을 배포 환경에 맞게 설정합니다. 현재 Backend config의 기본값은 `http://localhost:8001`입니다.
- Provider 상태/변경이 필요할 때만 관리용 proxy를 추가합니다. 예시 권장 경로는 `GET/PUT /api/ai/provider`이며, 쓰기 요청은 관리자 또는 시연 권한으로 제한합니다. 이 Backend 경로는 아직 없습니다.

## Frontend TEAM_REQUEST — 미구현

- 기존 Backend 뉴스 목록 화면과 API를 기준으로 작업합니다. Frontend에서 AI Server 주소나 API key를 사용하지 않습니다.
- 뉴스 목록을 먼저 표시하고 AI 분석 응답을 기다리는 동안 목록 전체를 막지 않습니다.
- Sentiment badge 표시: POSITIVE=호재, NEUTRAL=중립, NEGATIVE=악재
- Importance badge 표시: LOW=낮음, MEDIUM=보통, HIGH=높음
- Confidence는 기본 사용자 UI에서 표시하지 않는 것을 권장합니다.
- AI 분석 실패 또는 결과 없음: 관련 badge만 생략하고 기존 뉴스 제목과 본문은 계속 표시합니다.
- 분석 중 loading 상태와 실패 상태를 구분해 표시합니다. 실패 시 재시도 UX를 추가할 수 있지만, 반복 요청 정책은 Backend/API 사용량을 고려해 정합니다.

Provider 선택 UI는 일반 사용자 기능으로 공개하기보다 관리/시연용 기능으로 구분하는 것을 권장합니다. UI를 추가한다면 인증된 Backend proxy만 호출하고, 현재 provider/model 표시, 변경 성공/실패 feedback, key 미설정 오류를 제공해야 합니다. 이 UI도 아직 구현되지 않았습니다.

## 실행 및 확인

환경변수 이름과 예시 값은 [ai_server/.env.example](../.env.example)에 있습니다. 실제 key 값은 이 문서에 기록하지 마세요.

- `AI_PROVIDER`: 시작 provider, 기본 `openai`
- `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`
- `GEMINI_API_KEY`, `GEMINI_MODEL`

설치, 로컬 env 파일 준비, 서버 실행 명령은 [AI Server README의 설치·실행·테스트 절](../README.md#설치-실행-테스트)을 따릅니다. README 명령은 `ai_server` 디렉터리에서 실행합니다.

별도 `/health` route는 구현되어 있지 않습니다. 서버가 실행된 뒤 `http://localhost:8001/docs`에서 FastAPI 문서 화면에 접근할 수 있는지 확인하고, 인증된 Backend 경로를 통해 뉴스 분석 요청이 정상 처리되는지 확인하세요. `GET /settings/provider`는 상태 확인용으로 사용할 수 있지만 외부 공개 주소에 노출하지 마세요.

## 운영 및 보안

- API key는 AI Server 실행 환경에만 설정하고 Backend 응답이나 Frontend로 전달하지 않습니다.
- 실제 `.env` 파일과 credential을 Git에 commit하지 않습니다. 예시 파일에는 빈 key 값만 둡니다.
- `GET/PUT /settings/provider`는 현재 인증이 없습니다. 인터넷이나 외부 사용자 네트워크에 직접 노출하지 말고, 신뢰된 내부 경로에서만 접근하도록 방화벽 또는 배포 네트워크로 제한합니다.
- Provider 선택은 process memory에만 저장됩니다. 서버 재시작 시 `AI_PROVIDER` 환경변수 값으로 초기화됩니다.
- Multi-worker 배포에서는 worker마다 provider 상태가 달라질 수 있습니다. 현재 졸업작품 및 local demo에서는 single worker 운용을 권장합니다.
- Provider 오류가 나도 다른 provider를 자동 호출하지 않습니다. Backend와 Frontend는 실패를 처리하고 뉴스 원본을 유지해야 합니다.
- Provider 변경 API는 key의 존재 여부만 확인하며, 실제 인증 성공이나 provider 사용 가능 여부를 미리 검증하지 않습니다. 실제 생성 요청은 이후 뉴스 분석 또는 종목 요약 시점에 provider에 전달됩니다.

## 검증 상태

- 기존 Provider 전환 기능 검증: **72 passed**
- 종목 뉴스 요약 통합 후 AI Server 전체 회귀: **107 passed**
- Backend 관련 테스트: **20 passed** (`test_news_summary.py`, `test_news.py`). Backend 전체 테스트를 실행한 결과는 아닙니다.
- 통합 구현 검증은 fake/mock과 SDK MockTransport를 사용했으며 외부 네트워크를 차단했습니다. 실제 provider API 호출: **0**.
- 최종 코드 검토에서 공백 응답, 잘못된 AI_SERVER_URL, 빈 뉴스 캐시 경계를 보완했습니다. 실제 DB 연결과 Backend 전체 기동·브라우저 E2E는 개발 DB 환경 확인 대기 상태입니다.
- 해당 변경의 `git diff --check`: **PASS**
- Provider 전환 기능의 테스트/구현 과정에서 live provider API 호출: **0**
- 기존 검증: GPT-6 Luna endpoint smoke test에서 HTTP 200 및 예시 분석 응답을 확인했습니다.
- 기존 synthetic benchmark: 12개 fixture에서 Luna none은 sentiment 12/12, importance 10/12, joint 10/12, 약 4.83초, 추정 비용 약 $0.0002903을 기록했습니다. 작은 synthetic 표본 결과이며 실제 뉴스에 대한 최종 정확도나 운영 성능을 의미하지 않습니다.
- 기존 Gemini 3.8 Flash low 요청은 HTTP 503을 반환해 해당 설정의 품질 비교를 완료하지 못했습니다. 이것은 이전 검증 기록이며 현재 provider switch 테스트에서는 API를 호출하지 않았습니다.

## 구현 완료 / 남은 작업

| 영역 | 항목 | 상태 | 담당 작업 |
| --- | --- | --- | --- |
| AI Server | sentiment 분석 | DONE | 구현 및 테스트 완료 |
| AI Server | importance 분석 | DONE | 구현 및 테스트 완료 |
| AI Server | runtime provider switch | DONE | 뉴스 분석과 종목 요약에 적용 |
| AI Server | 종목 뉴스 종합 요약 | DONE | POST /summarize/stock-news, provider별 adapter 및 출력 검증 |
| Backend | 기존 종목 요약 연결 | DONE | 기존 인증 GET 계약 유지, AI_SERVER_URL 호출 및 provider별 캐시 |
| Frontend | 기존 종목 요약 UI | 기존 유지 | StockNewsSummary와 기존 Backend GET 사용 |
| Backend | 뉴스 분석 proxy | TEAM_REQUEST | 인증, timeout/error 처리, 뉴스 원본 응답 유지, AI_SERVER_URL 연결 |
| Backend | provider management proxy | OPTIONAL / TEAM_REQUEST | 관리/시연용 전환이 필요할 때만 인증된 proxy 추가 |
| Frontend | 뉴스 분석 badge 연결 | TEAM_REQUEST | 기존 뉴스 화면에 sentiment/importance 표시 및 비차단 실패 처리 |
| Frontend | provider switch UI | OPTIONAL / TEAM_REQUEST | 관리/시연 UI로 필요할 때만 Backend proxy에 연결 |
