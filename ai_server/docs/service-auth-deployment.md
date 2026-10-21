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

## 오프라인 검증 결과

- AI Server 전체 425 passed (기존 373 + 인증 관련 52).
- Backend 전체 227 passed (기존 221 + 인증 관련 6).
- 미설정/잘못된 인증, protected endpoint 및 자동 문서 보호, 정상 요청, 헤더 전달, 비밀값 비노출, 누락 토큰의 HTTP 차단, 뉴스 안내 fallback과 Portfolio 503/502 정책을 검증했습니다.
- 실제 `.env` 읽기·외부 네트워크·실제 DB 연결을 차단한 실행기로 검증했습니다. Backend 기존 테스트는 SQLite 메모리만 사용했습니다. 실제 provider 호출·운영 연결·Render 배포는 검증하지 않았습니다.

## 현재 Render 상태 — 2026-10-21 확인

Backend #5는 open / mergeable이며 HEAD는 `825d9416bdc67b91e11d2338d5869ff797918317`입니다. Frontend #4도 open / mergeable입니다. 인증 구현 기준은 `8a5dc93f827336ff826f5228cc23b3a085c58fca`이며 현재 branch는 `feature/ai-service-auth-deploy`입니다. fetch 후 `origin/main`은 `3d3f62b794e3fbbc0b6d4c42fc61e3d32adebb7e`, 인증 branch는 main 대비 13 ahead / 0 behind입니다. 이는 확인 시점의 기록이며 실제 실행 직전에 다시 확인합니다.

Render Dashboard는 로그인 화면으로 연결되어 서비스 상세에 접근하지 않았습니다. 기존 팀 전달 내용상 Backend는 Render에서 운영 중이나 현재 dashboard 설정을 확인한 결과와 구분합니다. 로그인·secret reveal·설정 저장·서비스 생성·배포는 수행하지 않았습니다.

| 확인 항목 | 실제 Dashboard 확인 결과 |
| --- | --- |
| Backend 서비스 이름 / type | UNKNOWN / UNKNOWN |
| region / compute plan | UNKNOWN / UNKNOWN |
| 연결 repository / branch / 실제 배포 SHA | UNKNOWN / UNKNOWN / UNKNOWN |
| root / build / start command | UNKNOWN / UNKNOWN / UNKNOWN |
| health check / auto-deploy | UNKNOWN / UNKNOWN |
| DB service 연결 방식 / 대상 DB / current revision | UNKNOWN / UNKNOWN / UNKNOWN |
| 환경변수 이름별 존재 여부 | 모두 UNKNOWN; 값은 조회하지 않음 |
| 기존 AI Server 서비스 존재 / URL / plan | UNKNOWN / UNKNOWN / UNKNOWN |
| PRE_DEPLOY_AVAILABLE | UNKNOWN |
| sleep / cold start | 현재 서비스는 UNKNOWN; Free를 선택하면 아래 제한 적용 |

배포 담당자는 위 항목을 값 노출 없는 설정 화면에서 확인해야 합니다. DB 연결은 환경변수 이름과 연결 방식만 기록하며 전체 URL, 비밀번호, 토큰은 문서·채팅·로그에 복사하지 않습니다.

## AI Server 설정표와 환경변수 inventory

신규 서비스가 없다면 **Public Web Service**, repository `202207048/gp-mock-inv`, root `ai_server`, 위 표의 build/start, single worker, health `GET /health`를 사용합니다. 배포 branch는 인증 코드가 main에 반영된 후 확정하며, 현재 feature를 운영 branch로 임의 지정하지 않습니다. 이름·region·plan·최종 hostname은 아직 UNKNOWN입니다. region은 Backend/DB 위치를 확인한 뒤 결정합니다. Backend/AI Server compute 비용은 서비스별로 확인하며 무료 운영을 가정하지 않습니다.

각 서비스는 자체 requirements를 설치합니다. Backend의 `alembic`, `psycopg2-binary`, `pydantic-settings`와 AI Server의 `openai`, `google-genai`는 각각 해당 requirements에 선언되어 있습니다. 이번 문서 작업에서는 설치하지 않습니다. 배포 담당자는 build 로그의 Python 버전과 설치 성공을 확인하며 두 서비스를 하나의 가상환경으로 합치지 않습니다.

실제 환경변수 **값은 기록하지 않습니다**. 아래는 코드에서 확인한 이름·필요 조건·책임만 나타냅니다. Render에서 존재하는지는 아직 UNKNOWN입니다.

| NAME | REQUIRED | SERVICE | PURPOSE |
| --- | --- | --- | --- |
| AI_SERVICE_TOKEN | REQUIRED | AI Server | 보호 endpoint의 기대 서비스 토큰 |
| AI_PROVIDER | OPTIONAL | AI Server | 프로세스 시작 시 legacy provider 선택 |
| OPENAI_API_KEY | PROVIDER-SPECIFIC; 현재 Frontend Luna 브리핑에는 필요 | AI Server | OpenAI 및 luna/sol profile 실행 |
| GEMINI_API_KEY | PROVIDER-SPECIFIC | AI Server | Gemini legacy 경로 실행 |
| OPENAI_MODEL | OPTIONAL | AI Server | profile 미지정 OpenAI 경로의 모델 선택 |
| OPENAI_REASONING_EFFORT | OPTIONAL | AI Server | profile 미지정 OpenAI reasoning 설정 |
| GEMINI_MODEL | OPTIONAL | AI Server | Gemini 모델 선택 |
| AI_SERVER_URL | REQUIRED (배포 연결) | Backend | 실제 AI Server HTTPS origin |
| AI_SERVICE_TOKEN | REQUIRED | Backend | AI Server와 동일한 서비스 토큰 |
| DATABASE_URL | REQUIRED | Backend / 승인된 migration runner | 대상 PostgreSQL 연결; AI Server에는 불필요 |
| SECRET_KEY | REQUIRED (사용자 인증) | Backend | JWT 서명; AI 서비스 토큰과 별개 |
| KIS_APP_KEY / KIS_APP_SECRET | FEATURE-SPECIFIC | Backend | 기존 KIS 기능 |
| KIS_REAL_APP_KEY / KIS_REAL_APP_SECRET | FEATURE-SPECIFIC | Backend | 기존 실제 시세 KIS 기능 |
| GEMINI_API_KEY / GEMINI_MODEL | FEATURE-SPECIFIC | Backend | 기존 직접 Gemini 설문·코칭; AI Server 키와 필요 위치 구분 |
| ALGORITHM / ACCESS_TOKEN_EXPIRE_MINUTES | OPTIONAL | Backend | 기존 JWT 설정 |
| ENABLE_ORDER_AUTOMATIONS | OPTIONAL | Backend | 기존 주문 감시 설정 |
| PORT | 플랫폼 제공 | Backend / AI Server | 위 start command의 listen port |

Frontend에는 `AI_SERVER_URL`, `AI_SERVICE_TOKEN`, provider key, DB/JWT secret을 설정하지 않습니다. 기존 Backend API origin 연결만 유지합니다. production `AI_SERVER_URL`은 `https://<AI Server onrender hostname>` 형태의 HTTPS origin만 사용합니다. 사용자정보·토큰·query를 URL에 넣지 않습니다. 로컬 코드가 HTTP도 허용하더라도 배포 정책은 HTTPS로 제한합니다.

주의: `luna` / `sol`의 모델·reasoning은 `app/model_profiles.py`의 서버 allowlist에 고정되어 있습니다. `OPENAI_MODEL`이나 runtime provider 변경으로 explicit profile이 변경되지 않습니다. 현재 Frontend 브리핑은 명시적으로 `luna`를 요청하므로 Gemini만 설정해서 Luna 브리핑이 동작한다고 가정하면 안 됩니다. 자동 fallback은 없습니다.

## Pre-Deploy와 migration 실행 방법 결정

실제 plan을 확인하지 못했으므로 **최종 실행 수단은 미확정**입니다. 아래 분기 중 하나를 담당자가 확정하고 승인하기 전에는 DB 작업이나 Backend 새 버전 배포를 진행하지 않습니다. workspace plan과 service compute plan을 혼동하지 않습니다.

| 확인된 조건 | migration 실행 방법 | 진행 조건 |
| --- | --- | --- |
| Paid Web Service 및 Pre-Deploy 사용 가능 | Backend root에서 `python -m alembic -c alembic.ini upgrade head`를 별도 Pre-Deploy 단계로 사용 | 해당 배포 artifact에 새 migration이 포함되어야 함. 단계 실패 시 배포 중단 |
| Pre-Deploy를 쓰지 않으며 허용된 Shell/One-off Job 존재 | 승인된 단일 migration 작업을 별도 실행 | runner의 코드 SHA와 migration graph 확인; 운영 Backend 시작과 분리 |
| Free Web Service | 승인된 별도 수동 runner에서 동일 코드와 대상 DB 환경으로 실행 | Free는 Shell·One-off Job을 지원하지 않음. 연결 권한·SSL·복구 수단·실행 담당자 확인 전 중단 |
| plan 또는 접근 수단 UNKNOWN | 실행하지 않음 | 담당자가 안전한 실행 경로를 확정해야 함 |

Pre-Deploy는 paid web service 등에서 제공됩니다. Free Web Service는 유휴 15분 후 sleep하며 다음 요청의 기동에 약 1분이 걸릴 수 있습니다. 이는 현재 서비스 plan을 확인한 결과가 아닌 공식 기능 제한입니다. [배포 단계](https://render.com/docs/deploys#pre-deploy-command), [Free 제한](https://render.com/docs/free), [Shell 지원](https://render.com/docs/ssh#compatible-service-types)을 참고합니다.

One-off Job은 base service의 **최근 성공한 build artifact**를 사용합니다. 기존 운영 artifact에 신규 migration이 없으면 그 Job으로 새 head를 적용할 수 없습니다. 잘못된 artifact로 실행하거나 migration을 얻기 위해 준비되지 않은 Backend를 먼저 배포하지 않습니다. 외부 runner가 필요하면 대상 코드·환경·접근 권한을 별도로 준비합니다. [One-off Jobs](https://render.com/docs/one-off-jobs)

Start Command에 `alembic upgrade head && uvicorn ...`를 넣는 방식을 기본안으로 사용하지 않습니다. 재시작마다 실행되거나 여러 instance가 동시에 schema를 바꾸는 상황을 피하고 단일 실행 책임자를 둡니다. 이번 작업에서 이러한 설정을 추가하지 않았습니다.

## Migration Procedure — 다음 승인된 배포 작업용

working directory는 repository의 `backend`, 설정 파일은 `backend/alembic.ini`입니다. `script_location=alembic`이며 실제 DB 연결은 `app.config.settings.DATABASE_URL`에서 가져옵니다. 명령에 DB URL을 직접 쓰거나 shell history에 credential을 붙이지 않습니다. 여기 적힌 모든 명령과 SQL은 **이번 작업에서 실행하지 않았습니다**.

대상 graph:

```text
                       f20261021_order_filled_at
                      /                         \
 e20261007_misu ------                            g20261021_ai_community_merge
                      \                         /
                       f20261021_community
```

- `f20261021_order_filled_at`: nullable timezone-aware `orders.filled_at` 추가. 과거 주문 backfill 없음.
- `f20261021_community`: nullable `posts.attachments`와 `post_votes` 테이블/제약 추가.
- `g20261021_ai_community_merge`: 두 sibling을 부모로 하는 no-op revision. 자체 schema 변경 없음.

담당자 실행 순서:

1. 대상 DB와 코드 SHA를 값 노출 없이 확인합니다. `backend`에 `alembic.ini` 및 두 sibling/merge revision이 있어야 합니다. 현재 운영 배포 SHA·복구 가능한 이전 배포·backup 시점/복구 담당자/복구 가능성을 먼저 확인합니다. backup/recovery가 미확정이면 중단합니다.
2. 승인된 runner의 Backend 환경을 사용하여 현재 revision과 코드 head를 확인합니다. 출력은 revision만 기록하고 연결 URL/traceback은 공유하지 않습니다.

   ```bash
   python -m alembic -c alembic.ini current
   python -m alembic -c alembic.ini heads
   ```

3. 코드 head가 `g20261021_ai_community_merge`인지 확인합니다. DB가 부모 중 하나여도 나머지 sibling이 적용됩니다. current가 없거나 다른 이력, schema와 revision 불일치, 이미 수동 생성된 column/table 충돌이 의심되면 중단합니다. 임의 `stamp`, autogenerate, drop/reset으로 맞추지 않습니다.
4. 현재 revision에 따라 적용될 모든 migration 범위를 검토한 뒤 한 담당자가 실행합니다. 오래된 DB라면 위 두 sibling 외 선행 migration도 실행될 수 있으므로 두 변경만 적용된다고 가정하지 않습니다.

   ```bash
   python -m alembic -c alembic.ini upgrade head
   ```

5. 성공한 뒤 다시 current/head를 확인합니다. 두 sibling current나 단순 exit 0만으로 완료 처리하지 않고 단일 merge head인지 확인합니다.

   ```bash
   python -m alembic -c alembic.ini current
   python -m alembic -c alembic.ini heads
   ```

6. 승인된 DB 관리 경로에서 아래 **metadata 조회만** 수행해 nullable timezone column과 Community schema 존재를 확인합니다. 실제 schema가 기본 schema와 다르면 담당자가 그 schema를 지정합니다. revision 일치와 물리 schema 확인을 모두 마쳐야 통과입니다.

   ```sql
   SELECT column_name, data_type, is_nullable
   FROM information_schema.columns
   WHERE table_schema = current_schema() AND table_name = 'orders'
     AND column_name = 'filled_at';

   SELECT EXISTS (
     SELECT 1 FROM information_schema.columns
     WHERE table_schema = current_schema() AND table_name = 'posts'
       AND column_name = 'attachments'
   ) AS attachments_present,
   EXISTS (
     SELECT 1 FROM information_schema.tables
     WHERE table_schema = current_schema() AND table_name = 'post_votes'
   ) AS post_votes_present;
   ```

7. `filled_at`이 `timestamp with time zone`, nullable이고 Community schema가 존재하는지 확인합니다. 실제 PostgreSQL migration 검증은 아직 대기 상태이며 SQLite 회귀 통과로 대체하지 않습니다.

실패하면 Backend 새 배포를 중단하고 기존 앱을 유지합니다. production에서 downgrade/drop/reset/autogenerate를 일반 복구 수단으로 사용하지 않습니다.

## Deployment Order — 실행 전 gate 포함

0. **merge 전 gate:** 실제 Backend auto-deploy와 branch/배포 SHA를 확인합니다. 자동 배포가 켜져 있으면 #5 merge만으로 migration 전 새 Backend가 올라갈 수 있습니다. 담당자가 승인된 배포 보류/Pre-Deploy 방식을 먼저 마련해야 합니다. UNKNOWN 상태에서는 배포를 유발할 merge를 진행하지 않습니다. 이번 작업은 설정을 변경하지 않았습니다.
1. Backend #5 검토/merge. 기존 PR branch에 새 commit을 추가하지 않습니다.
2. 별도 작업으로 인증 branch와 최신 main 정합성을 확인하고 auth PR을 생성·반영합니다. 이번 작업에서는 merge/rebase/PR 수정을 하지 않습니다.
3. 별도 runner 방식이면 위 절차로 DB migration을 수행·검증합니다. Pre-Deploy 방식이면 실행은 5단계의 Backend 배포 안에서 이루어지므로, 이 단계에서는 backup과 runner/검증 절차를 확정합니다. Pre-Deploy 성공 후 start를 임의로 일시 정지할 수 있다고 가정하지 않습니다.
4. 인증이 포함된 확정 branch/SHA로 AI Server를 배포하고 `/health`·인증 smoke test를 통과시킵니다. health 200만으로 provider 사용 가능을 판정하지 않습니다.
5. Backend에 HTTPS AI origin과 동일 서비스 토큰을 비밀 설정으로 반영한 뒤 배포합니다. Pre-Deploy 방식은 이때 migration을 실행하며, 필요한 revision/schema 검증도 승인된 pre-deploy 절차 안에 포함합니다. 이를 구성할 수 없으면 별도 runner로 먼저 검증하는 방식을 선택합니다. 즉 별도 runner는 DB → AI → Backend 순서이고, Pre-Deploy는 AI → Backend pre-deploy DB → Backend start 순서입니다. 어느 방식도 DB/AI 확인 전에 Backend 새 start로 넘어가지 않습니다. Backend `/` 200, 기존 사용자 인증과 DB 기동을 확인합니다.
6. 가능한 실행 수단에서 Backend → AI Server의 비생성 연결을 검증합니다. 비용 승인 후 한 번의 generation smoke를 수행합니다.
7. Backend 기능이 준비된 후 Frontend #4를 검토/merge/deploy하고 브라우저 E2E를 확인합니다. generation smoke를 E2E의 한 번 클릭으로 통합해 중복 비용을 피할 수 있습니다.

## Smoke Tests — 비생성부터

아래는 **아직 실행하지 않은 계획**입니다. 실제 토큰을 명령 인수, URL/query, 채팅, clipboard 기록, shell history나 로그에 남기지 않습니다. 이미 안전하게 주입된 Backend 환경변수/설정만 프로세스 내부에서 사용합니다. 출력은 HTTP status와 판정만 남기고 응답 원문·Authorization 헤더·전체 exception은 출력하지 않습니다.

| 검사 | 기대 결과 | generation |
| --- | --- | --- |
| AI GET /health, token 없음 | 200, 안전한 status만 | 0 |
| AI GET /profiles, token 없음 | 401 | 0 |
| AI GET /profiles, 정상 token | 200 | 0 |
| AI GET /settings/provider, token 없음 | 401 | 0 |
| AI GET /settings/provider, 정상 token | 200 | 0 |
| Backend GET / | 200 | 0 |

토큰이 미설정된 AI Server는 보호 경로에서 401 대신 503입니다. 따라서 `/health` 200과 보호 경로 503 조합은 배포 연결 완료가 아닙니다. `/profiles` 200도 key의 존재를 반영할 뿐 provider 인증 성공/잔액/모델 접근 권한을 검증하지 않습니다. provider PUT은 smoke에 필요하지 않습니다.

다음은 승인 후 **Backend root와 실제 Backend 환경**에서 실행할 비생성 확인 예시입니다. Shell/Job 사용 가능 여부를 먼저 확정합니다. `get_summary_provider`는 실제 Backend transport를 사용하며 DB와 provider SDK를 호출하지 않습니다.

```bash
python - <<'PY'
import asyncio
import httpx
from app.config import settings
from app.services.ai_service_auth import service_headers
from app.services.news_summary_service import get_summary_provider

try:
    base = httpx.URL(settings.AI_SERVER_URL)
    if (base.scheme != "https" or not base.host or base.userinfo or base.query
            or base.fragment or base.path not in ("", "/")):
        raise ValueError("Invalid deployment origin")
    headers = service_headers()
    cases = [("/health", {}, 200), ("/profiles", {}, 401),
             ("/profiles", headers, 200), ("/settings/provider", {}, 401),
             ("/settings/provider", headers, 200)]
    with httpx.Client(follow_redirects=False) as client:
        for path, auth, expected in cases:
            result = client.get(str(base).rstrip("/") + path, headers=auth,
                                timeout=90.0 if path == "/health" else 10.0)
            print(path, result.status_code, "PASS" if result.status_code == expected else "FAIL")
            if result.status_code != expected:
                raise RuntimeError("Smoke failed")
            if path == "/health" and result.json() != {"status": "ok"}:
                raise RuntimeError("Unexpected health response")
    asyncio.run(get_summary_provider(str(base).rstrip("/")))
    print("BACKEND_CLIENT_PASS")
except Exception as exc:
    print("SMOKE_FAILED", type(exc).__name__)
    raise SystemExit(1) from None
PY
```

외부 runner 성공은 해당 runner의 연결 확인일 뿐 실제 Render Backend의 연결 확인과 구분합니다. Free에서 실행 수단이 없으면 실제 Backend의 비생성 연결은 미검증으로 남기고, 승인 후 생성 1회로 실제 연결을 확인합니다. 확인을 위해 공개 관리 proxy를 새로 추가하지 않습니다.

비용 승인 후에는 로그인된 본인 계좌와 실제 보유 종목을 사용하여 Backend `GET /api/ai/portfolio-brief/{symbol_code}?account_id={id}&profile=luna`를 한 번만 요청합니다. 이 단계는 DB/시세/뉴스 및 provider 연결을 수반합니다. 자동 retry하지 않으며 실패를 반복 클릭으로 재현하지 않습니다. 뉴스 요약 경로도 생성 비용이 발생하므로 비생성 health 확인에 사용하지 않습니다.

## Production E2E — 미실행 checklist

실행 준비 상태는 **문서 절차 준비 / 실제 검증 대기**입니다. 위 gate와 양쪽 배포를 통과한 뒤 수행합니다.

1. Frontend 접속 → 로그인 → Assets로 이동합니다. 로그인 토큰과 응답 원문은 공유하지 않습니다.
2. 본인 계좌와 실제 보유종목, AI 카드를 확인합니다. 기존 자산/보유 목록이 먼저 표시되어야 합니다.
3. 초기 진입과 종목 선택만으로 Portfolio Brief 생성 호출은 각각 0회인지 확인합니다. 다른 기존 AI 기능의 트래픽과 구분합니다.
4. `AI 브리핑 보기`를 한 번 클릭합니다. loading 중 중복 요청이 없어야 하며 Backend 요청은 1회, `profile=luna`여야 합니다.
5. Backend 200, AI Server 서비스 인증 성공, Luna 생성 1회를 확인합니다. Authorization/header·사용자 토큰·응답 원문을 캡처/공유하지 않고 status와 개수만 기록합니다.
6. headline과 구조화 section, disclaimer가 표시되는지 확인합니다. 과거 진입 가격 문맥과 최근 뉴스가 구분되고 과거 매수 이유나 뉴스-가격 인과가 추가되지 않아야 합니다.
7. raw provider/model/usage/내부 URL/secret이 UI에 없는지 확인합니다. 없는 section은 생략되어야 합니다.
8. 계좌/종목 전환 시 이전 결과가 남지 않는지 확인합니다. 오류는 카드 내부 안내로 한정되고 자산/보유 목록은 유지되어야 합니다. 실패 주입이나 추가 생성은 별도 승인 범위로 구분합니다.

## Failure Matrix

| 관찰 | 확인/조치 | 진행 여부 |
| --- | --- | --- |
| AI /health 실패 또는 cold start 미완료 | 서비스 상태, 포트/기동 명령, secret 없는 로그 확인 | Backend/Frontend 다음 단계 중단 |
| AI 보호 경로 401 | 토큰 존재와 양쪽 일치 여부를 담당자가 값 노출 없이 확인 | generation 중단; token을 URL로 보내지 않음 |
| AI 503 | 보호 경로는 서비스 토큰 미설정, 생성 경로는 provider/profile 설정도 확인 | health만 보고 정상 판정하지 않음 |
| Backend Portfolio 502 | upstream contract/schema/provider 오류, timeout, 상태 코드 확인 | 사용자 로그인 401로 해석하지 않음; 자동 retry 없음 |
| Backend 503 | AI URL/token 설정·연결 불가·upstream 503 확인 | 안전한 카드 오류; 다음 배포 단계 보류 |
| migration 실패 / revision-schema 불일치 | 새 앱 배포 중단, DBA/담당자 확인 | 임의 stamp/downgrade/drop 금지 |
| Frontend 카드 오류 | Backend HTTP 상태/버전/사용자 권한 확인 | 원본 자산/뉴스를 유지; raw detail 표시 금지 |
| 뉴스 summary 일시 실패 | 기존 HTTP 200 제목 기반 안내인지 확인 | 실패/빈 뉴스 결과 캐시 금지 정책 유지 |

Backend는 기존 startup에서 일부 주문 column DDL과 DB/monitor 확인을 수행합니다. 이는 Alembic 전체 적용의 대체가 아니며 `filled_at`이나 Community migration 완료를 보장하지 않습니다. 이번 준비 작업에서는 Backend를 실제 기동하지 않았습니다.

## Rollback — 앱과 DB 분리

- 배포 전 서비스별 마지막 정상 deploy ID/SHA와 복구 가능 artifact를 기록합니다. 확인 전에는 rollback이 준비됐다고 판정하지 않습니다.
- 앱 문제는 승인된 담당자가 Render의 이전 정상 deploy rollback 또는 이전 검증 commit 재배포를 우선합니다. [Render Rollbacks](https://render.com/docs/rollbacks)에 따라 artifact 보존과 해당 deploy의 환경·health/start 설정을 확인합니다. 현재 설정 전체가 항상 그대로 쓰인다고 가정하지 않습니다.
- Dashboard rollback은 auto-deploy를 끄지만 API rollback은 끄지 않습니다. 담당자가 실제 상태를 확인하고 복구 중 새 merge가 재배포를 유발하지 않도록 통제합니다. 이번 작업에서는 어떤 설정도 변경하지 않았습니다.
- 인증된 AI Server를 무인증 과거 버전으로 되돌려 공개하지 않습니다. Backend/AI Server 양쪽 버전·토큰 정합성을 확인하고, 안전한 인증 버전이 없으면 AI 기능을 unavailable로 유지합니다.
- DB는 자동 downgrade하지 않습니다. `filled_at`은 nullable 추가 column이므로 예전 앱이 쓰지 않아도 row 삽입을 막지 않습니다. Community도 기존 schema 삭제 없이 nullable column/별도 table을 추가하며 merge revision은 no-op입니다. 코드 기준으로는 schema를 남긴 앱 rollback을 우선할 수 있지만 실제 배포 DB 호환성은 별도 확인해야 합니다.
- 구버전으로 체결된 주문은 `filled_at`이 NULL로 남을 수 있습니다. 신버전 복귀 후 해당 position이 unavailable이 되는 것은 안전한 제한이며 `created_at`으로 추정/backfill하지 않습니다.
- Community 데이터가 함께 있으므로 일반 장애 복구에 DB downgrade/drop/reset을 사용하지 않습니다. 별도 backup 복원은 DBA 승인·데이터 손실 평가가 필요한 절차로 이 runbook의 자동 단계가 아닙니다.

## 발표 당일 checklist

11월 11일 전 실제 연결·권한·모델 접근을 한 번 검증하고 마지막 정상 deploy를 확보합니다. Free를 사용하면 약 1분 cold start가 Backend 생성 timeout 30초, provider 조회 5초, Frontend 60초 timeout보다 길 수 있습니다. 발표 전 AI `/health`와 Backend `/`를 비생성 방식으로 확인해 기동 완료를 기다립니다. 반복적인 유료 생성으로 서비스를 깨우지 않습니다. sleep이 없는 유료 compute를 선택할지는 담당자 비용 승인 사항이며 여기서 plan을 변경하지 않습니다.

- A 정상: 본인 계좌/보유 확인 → 한 번 클릭 → 실제 구조화 브리핑과 disclaimer를 보여줍니다.
- B AI 장애: 기존 뉴스는 제목 기반 안내, Portfolio Brief는 카드 안전 오류로 설명하고 자산/보유 목록이 유지됨을 보여줍니다.
- C provider 장애: 반복 클릭을 멈추고 이미 검증한 화면 이동/자산 조회 흐름으로 시연합니다. 앞서 실제 생성한 화면이 남아 있다면 그 결과임을 분명히 하며 새 성공처럼 꾸미지 않습니다.
- 가짜 응답, 하드코딩 성공 데이터, 인증 우회, 자동 provider fallback을 추가하지 않습니다.

## 현재 남은 수동 확인과 종료 조건

현재 runbook 내용은 준비했지만 **MANUAL_RENDER_INFO_REQUIRED**입니다. 서비스 이름/type/region/compute plan/branch/root/build/start/auto-deploy/health와 DB 연결 방식, 변수 이름별 존재 여부, backup/recovery와 migration runner를 담당자가 확인해야 합니다. secret 값 자체는 보고하지 않습니다. 이 정보로 migration 분기·배포 보류 수단을 확정한 뒤 승인된 별도 작업에서만 배포합니다.

이번 문서 작업의 Render 변경·secret 생성/입력·DB query/migration·provider 생성·Frontend 배포는 모두 0회입니다. 기존 오프라인 테스트 결과는 재사용했으며 코드·tests·requirements를 수정하지 않았습니다.
