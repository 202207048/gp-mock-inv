# AI Server 서비스 인증 및 배포 준비

## 연결과 권한

`Frontend → Backend → AI Server → OpenAI / Gemini` 구조를 유지합니다. Backend의 사용자 로그인 인증과 AI Server의 서비스 인증은 별개입니다. Frontend에 서비스 토큰을 보내거나 사용자 Authorization 헤더를 그대로 AI Server로 전달하지 않습니다.

Backend가 `Authorization: Bearer <AI_SERVICE_TOKEN>`을 보냅니다. AI Server는 `secrets.compare_digest`로 비교합니다. 토큰 미설정·빈 값·공백 설정이면 보호 경로는 안전한 503, 헤더 누락·형식 오류·잘못된 토큰이면 401을 반환합니다. 인증 검사는 schema 검증과 adapter 생성 전에 실행됩니다. 401 응답에는 `WWW-Authenticate: Bearer`가 포함됩니다.

| 분류 | 경로 | 정책 |
| --- | --- | --- |
| PUBLIC | GET /health | `{"status":"ok"}`만 반환; 자격증명·모델·내부 URL 정보 없음 |
| SERVICE_PROTECTED | POST /analyze/news | 서비스 토큰 필요 |
| SERVICE_PROTECTED | POST /summarize/stock-news | 서비스 토큰 필요 |
| SERVICE_PROTECTED | POST /brief/portfolio | 서비스 토큰 필요 |
| SERVICE_PROTECTED | GET /profiles | 서비스 토큰 필요 |
| SERVICE_PROTECTED | GET /docs, /redoc, /openapi.json, /docs/oauth2-redirect | 자동 문서 경로도 같은 보호 적용 |
| ADMIN_PROTECTED | GET /settings/provider, PUT /settings/provider | 같은 서비스 토큰으로 조회/변경; 별도 admin 토큰이나 역할 구분은 없음 |

PUBLIC 예외는 정확히 `GET /health`뿐입니다. 다른 method, 경로 및 향후 추가 경로도 기본적으로 보호됩니다. root health/info 경로는 별도 구현하지 않았습니다. 서비스 토큰 보유자는 provider 설정 변경도 할 수 있으므로 Backend 관리 proxy/UI를 추가할 때는 관리자 권한을 따로 검사해야 합니다. 일반 브라우저는 Bearer 헤더 없이 `/docs`를 열 수 없습니다. 문서 조회에도 인증 헤더를 보내는 신뢰된 도구를 사용하고 토큰을 URL/query에 넣지 않습니다.

## 환경 설정

아래는 실제 값이 아닌 자리표시자입니다. 실제 토큰 생성·입력은 배포 담당자가 수행합니다.

AI Server 환경:

```text
AI_SERVICE_TOKEN=<server secret>
```

Backend 환경:

```text
AI_SERVER_URL=https://<ai-server-host>
AI_SERVICE_TOKEN=<same server secret>
```

- 암호학적으로 안전한 난수 생성기로 최소 32바이트의 난수를 생성하고 URL-safe ASCII 문자열로 인코딩합니다. 사람이 만든 암호나 provider API key를 재사용하지 않습니다. 공백·개행 없이 두 서버에 같은 값을 설정합니다.
- 배포 환경변수를 권장합니다. AI Server의 로컬 `.env`는 `ai_server/.env`를 명시적으로 로드하며 이미 존재하는 프로세스 환경변수를 덮어쓰지 않습니다. Backend의 로컬 `.env`는 `backend` working directory 기준입니다.
- Backend 설정은 `SecretStr`와 `repr=False`를 사용합니다. 전체 설정이나 요청 Authorization 헤더를 로그에 남기지 않습니다. 프록시/관측 도구에서도 헤더를 가립니다.
- 토큰 누락·잘못된 로컬 헤더 값이면 Backend는 HTTP client를 만들지 않습니다. 뉴스 요약은 기존 HTTP 200 제목 기반 안내를 유지하며 실패를 캐시하지 않습니다. Portfolio Brief는 설정 누락 503, upstream 401/403 등은 502, upstream 503은 503을 유지합니다.
- 공통 AI proxy(`/recommend`, `/pattern` 호출)도 같은 헤더를 보냅니다. 이 경로가 AI Server에 구현되어 있다는 의미는 아닙니다. 기존 Backend Gemini 직접 호출 기능은 변경하지 않았습니다.
- `.env`, 실제 토큰, API key를 Git에 넣지 않습니다. 기존 `.env.example` 사용자 변경은 그대로 보존했습니다. 자동 fallback/retry는 추가하지 않았습니다.

## 저장소에서 확인한 실행 구조

현재 tracked 파일에는 `render.yaml`, `Procfile`, Dockerfile이 없습니다. 실제 Render dashboard의 root/build/start 설정은 이 저장소만으로 확정할 수 없으므로 아래 값과 대조해야 합니다. Backend README는 `backend`에서 `uvicorn app.main:app` 실행을 안내하고 있습니다.

| 서비스 | Root Directory | Build Command | Start Command (Render 셸 기준) |
| --- | --- | --- | --- |
| AI Server | ai_server | python -m pip install -r requirements.txt | python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 |
| Backend | backend | python -m pip install -r requirements.txt | python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT |

각 서비스의 자체 `requirements.txt`를 사용하며 Backend와 AI Server dependency 환경을 분리합니다. 인증 구현에 신규 dependency는 없습니다. AI Server는 DB 연결 없이 시작하며 health check path는 `/health`입니다. health 200만으로 토큰 설정이나 provider 인증 성공이 검증되지는 않습니다. Backend는 시작 시 DB 확인/기존 초기화 작업이 있으므로 실제 DB 설정 및 migration 준비를 담당자가 확인해야 합니다.

런타임 provider 상태는 프로세스 메모리이므로 AI Server는 single worker로 운용합니다. 서버 재시작 시 `AI_PROVIDER`로 초기화하며 자동 provider fallback은 없습니다. 모델·key 설정은 [README](../README.md)와 [인수인계](team-handoff.md)를 따릅니다.

## 배포 담당자 checklist — 아직 실행하지 않음

1. Backend #5 통합 후 이 인증 branch를 최신 main과 맞추고 별도 PR로 검토/반영합니다.
2. AI Server Public Web Service의 root/build/start와 HTTPS를 확인하고 provider key 및 서비스 토큰을 비밀 환경변수로 설정합니다. 실제 token 값은 문서/PR에 기록하지 않습니다.
3. `/health` 200, 인증 없는 보호 경로 401, 올바른 토큰의 `/profiles` 및 `GET /settings/provider` 200을 확인합니다. 토큰 미설정 배포에서는 보호 경로 503이어야 합니다. 이 확인은 AI 생성 호출이 아닙니다.
4. Backend에 HTTPS `AI_SERVER_URL`과 동일한 서비스 토큰을 설정하고 재배포합니다. 실제 DB migration은 별도 승인된 운영 절차로 수행합니다.
5. Backend → AI Server 연결과 오류 변환을 확인한 뒤, 비용 발생에 동의한 범위에서 생성 smoke test를 진행합니다. Frontend는 Backend만 호출합니다.
6. Frontend #4 반영 후 사용자 권한, 브리핑 UI, 장애 안내를 포함한 production E2E를 수행합니다.

이번 구현에서 Render 서비스 생성, 환경변수 입력, production 배포, 실제 DB/AI 호출은 수행하지 않았습니다.

## 오프라인 검증 결과

- AI Server 전체 425 passed (기존 373 + 인증 관련 52).
- Backend 전체 227 passed (기존 221 + 인증 관련 6).
- 미설정/잘못된 인증, protected endpoint 및 자동 문서 보호, 정상 요청, 헤더 전달, 비밀값 비노출, 누락 토큰의 HTTP 차단, 뉴스 안내 fallback과 Portfolio 503/502 정책을 검증했습니다.
- 실제 `.env` 읽기·외부 네트워크·실제 DB 연결을 차단한 실행기로 검증했습니다. Backend 기존 테스트는 SQLite 메모리만 사용했습니다. 실제 provider 호출·운영 연결·Render 배포는 검증하지 않았습니다.
