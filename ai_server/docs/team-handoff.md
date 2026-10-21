# 졸업작품 AI 서버 통합 인수인계

이 문서는 현재 구현된 뉴스 투자영향·중요도 분석과 runtime provider 설정을 Backend 및 Frontend에 연결하기 위한 안내입니다. AI Server 기능은 구현되어 있지만 Backend proxy와 Frontend 연결은 아직 구현되지 않았습니다.

## 구현 완료 기능

| 기능 | 상태 | 동작 |
| --- | --- | --- |
| 뉴스 투자영향 분석 | DONE | 기사별 sentiment를 `POSITIVE`, `NEUTRAL`, `NEGATIVE` 중 하나로 반환 |
| 중요도 분석 | DONE | 기사별 `score` (0~1)와 `LOW`, `MEDIUM`, `HIGH` label 반환 |
| OpenAI provider | DONE | 기본 모델 `gpt-6-luna`, reasoning effort `none` |
| Gemini provider | DONE | 기본 모델 `gemini-3.8-flash` |
| Runtime provider switch | DONE | 현재 프로세스에서 OpenAI/Gemini를 바꾸며 자동 fallback은 하지 않음 |

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
- Provider 변경 API는 key의 존재 여부만 확인하며, 실제 인증 성공이나 provider 사용 가능 여부를 미리 검증하지 않습니다. 실제 분석 요청은 이후 뉴스 분석 시점에 provider에 전달됩니다.

## 검증 상태

- Provider 전환 기능 구현 후 AI Server 전체 테스트: **72 passed**
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
| AI Server | runtime provider switch | DONE | GET/PUT 설정 API 및 process memory 적용 |
| Backend | 뉴스 분석 proxy | TEAM_REQUEST | 인증, timeout/error 처리, 뉴스 원본 응답 유지, AI_SERVER_URL 연결 |
| Backend | provider management proxy | OPTIONAL / TEAM_REQUEST | 관리/시연용 전환이 필요할 때만 인증된 proxy 추가 |
| Frontend | 뉴스 분석 badge 연결 | TEAM_REQUEST | 기존 뉴스 화면에 sentiment/importance 표시 및 비차단 실패 처리 |
| Frontend | provider switch UI | OPTIONAL / TEAM_REQUEST | 관리/시연 UI로 필요할 때만 Backend proxy에 연결 |
