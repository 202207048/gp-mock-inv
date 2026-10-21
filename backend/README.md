## 통합 배포 migration (2026-10-21)

배포 전/배포 시 `alembic upgrade head`로 `g20261021_ai_community_merge`까지 적용합니다.
이 head는 `f20261021_order_filled_at`과 `f20261021_community`를 함께 보존하며,
merge revision 자체는 schema를 변경하지 않습니다. 기존 DB가 어느 branch에 있든 나머지 migration도 적용됩니다.
실제 개발/운영 DB migration은 이번 통합 작업에서 실행하지 않았습니다.

## 주문 체결 시각 추가 (2026-10-21)

새 코드 적용 **전에** 기존 Alembic 방식으로 `alembic upgrade head`를 실행하여
`f20261021_order_filled_at`까지 적용한 뒤 서버를 재시작합니다.
이번 변경은 nullable `orders.filled_at` 한 컬럼만 추가하며, 시작 시 자동 DDL에는 추가하지 않습니다.

- `created_at`은 기존 주문 접수 시각이고, `filled_at`은 보유수량/현금에 반영된 체결 시각(UTC)입니다.
- 미체결·취소·거부 주문은 NULL이며 과거 주문도 backfill 없이 NULL로 남습니다. 과거 시장가 주문도 자동 추정하지 않습니다.
- 기존 주문 API 응답은 유지합니다. 향후 내부 재생은 `filled_at`, `order_id` 순으로 하되 NULL 체결 주문을 정상 시계열에 섞지 않습니다.
- 같은 시각의 `order_id`는 결정적 보조 정렬일 뿐 실제 체결 순서의 증거는 아닙니다. 지연 주문의 순서가 모호하면 향후 Fact Bundle에서 unavailable로 처리해야 합니다.
- 실제 DB에는 이번 개발 작업으로 migration을 적용하지 않았습니다. 개발/운영 환경 적용은 별도로 검증해야 합니다.

## 보유종목 브리핑용 사실 API

`GET /api/ai/portfolio-brief/facts/{symbol_code}?account_id={id}`는 기존 로그인 인증이 필요합니다.
본인 계좌만 조회하며 다른 사용자/존재하지 않는 계좌와 현재 미보유 종목은 404입니다.
AI Server나 LLM을 호출하지 않고 holding, position, trades, recent_news, price_context, limitations를 반환합니다.

- holding은 Portfolio 수량·평균단가를 그대로 사용합니다. 현재가가 있으면 평가손익과 수익률을 계산하며 수수료·세금을 다시 반영하지 않습니다. Decimal은 기존 주문 계약처럼 JSON 문자열로 직렬화됩니다.
- 체결 주문의 filled_at만 사용합니다. 해당 계좌·종목의 legacy NULL 체결이 하나라도 있으면 position.available=false, reason=legacy_execution_time_unavailable, trades=[]입니다. created_at fallback은 없습니다.
- 음수 재생·잘못된 체결 자료는 invalid_trade_history, 보유수량 불일치는 portfolio_quantity_mismatch로 position만 unavailable 처리합니다. 핵심 DB 조회 오류는 정상 응답으로 숨기지 않습니다.
- 동일 시각의 같은 방향 체결은 묶어서 처리합니다. 여러 매수로 시작하면 started_at은 알려져도 opening_order_id/opening_price는 null이고 opening_trade_ambiguous를 표시합니다.
- 동일 시각의 혼합 매수·매도는 그룹 직전 수량이 매도 총량보다 클 때만 순서와 무관하게 기존 position을 유지하며 순수량을 반영합니다. 그 외에는 0 도달·재시작 가능성을 배제할 수 없어 ambiguous_execution_order입니다. order_id 보조 정렬은 실제 체결 순서가 아닙니다.
- 시세 실패·유효하지 않은 현재가는 current_price_unavailable로 표시하고 current_price/unrealized_pnl/return_rate를 null로 둡니다. 평균단가가 0 이하이면 파생값을 계산하지 않고 invalid_average_price를 표시합니다.
- 뉴스는 기존 service의 최신 5개만 사용합니다. 빈 목록과 수집 실패를 기존 service에서 구분할 수 없으므로 둘 다 news_unavailable로 표시합니다. 기사 URL을 추가 방문하지 않습니다.
- historical_news_unavailable, historical_price_unavailable은 현재 버전의 범위 제한입니다. 과거 매수시점 뉴스라고 주장하지 않습니다. price_context.position_start_price와 change_since_position_start는 null이며 opening fill price로 대체하지 않습니다.
- holding과 주문은 소유 계좌 범위의 단일 DB 조회로 읽습니다. 시세·뉴스는 그 후 조회하므로 동일 시각의 시장 스냅샷을 보장하지 않습니다. 새 테이블이나 추가 migration은 없습니다.

## 커뮤니티 첨부 기능 (2026-10-21)

백엔드 배포 전에 `alembic upgrade head`로 `f20261021_community`를 적용한 뒤 서버와 프론트엔드를 함께 배포합니다. `posts.attachments`와 `post_votes`가 추가됩니다. 기존 게시글은 보존됩니다. 운영 DB에는 이번 로컬 작업에서 마이그레이션을 실행하지 않았습니다.

게시글 생성은 `images`(최대 4장, JPG/PNG/WEBP data URL, 장당 디코딩 후 1MB), `poll`(`question`, 서로 다른 `options` 2~4개), `symbol_code`, `trade_id`를 받습니다. 프론트엔드는 원본 10MB 이하 사진을 최대 1600px JPEG로 압축합니다. 사진은 게시글 JSON에 저장되므로 프록시 요청 본문 제한을 최소 6MB로 설정해야 합니다. 거래내역은 본인 계좌의 체결 내역만 서버에서 조회해 종목·매매구분·수량·단가·시각을 저장하며 계좌번호와 잔고는 공유하지 않습니다.

`PUT /api/community/posts/{post_id}/vote`에 `option_index`를 보내 투표하거나 선택을 변경합니다. 사용자별 1표이며 게시글 목록/상세에서 집계와 자신의 선택을 반환합니다. 검증: `python -m unittest tests.test_community_attachments tests.test_community_migration tests.test_withdrawal`.

`GET /api/community/capabilities`는 첨부·투표·인용 지원을 반환합니다. 프론트엔드는 첨부 등록 전에 이 API를 확인해 구버전 서버가 알 수 없는 필드를 조용히 버리는 것을 방지합니다. `quote_post_id`를 전달하면 원문 작성자·제목·본문 일부를 서버에서 조회하여 인용으로 저장합니다. `GET /api/community/posts?symbol_code=005930`으로 종목별 카테고리 필터를 적용하며, 프론트엔드의 카테고리 목록은 사용자별로 현재 브라우저에 저장됩니다.

## 미수 모의거래 추가 (2026-10-07)

새 코드 적용 **전에** `pip install -r requirements.txt`, `alembic upgrade head`로 `e20261007_misu`까지 적용하고 서버를 재시작합니다. 일반 주문·포트폴리오도 미수 장부를 참조하므로 스키마와 서버 코드를 함께 배포해야 합니다. 운영 DB에 이번 로컬 작업으로 마이그레이션을 실행하지 않았습니다.

`POST /api/trading/orders`에 `funding_type="미수"`, UUID `client_request_id`, `misu_risk_ack=true`를 전달합니다. 즉시 시장가 매수, 증거금 50%+수수료, T+2 거래일 17시 한국시간 결제, 기한 경과 다음 거래일부터 반대매매하는 **모의 규칙**입니다. 미수 감시는 시작 시 자동 실행되고 최근 정상 주기가 확인되지 않으면 신규 미수 주문을 거부합니다. 실제 증권사 주문이나 은행 이체를 호출하지 않습니다.

`GET /api/trading/misu/{account_id}`, `POST .../{account_id}/repay` (가상 외부입금: amount·client_request_id), `POST .../{account_id}/settle`을 제공합니다. 소유 계좌만 접근할 수 있고 GET은 장부를 변경하지 않습니다. 부족금이나 미결제 매도대금이 있으면 신규 매수를 제한합니다. 전량 처분 후에도 남은 부족금은 삭제하지 않습니다.

미수 상태 계좌의 매도는 T+2까지 지연하며, 순자산 계산은 `현금 + 주식평가액 + pending_proceeds - misu_debt`입니다. 기존 현금 주문은 즉시 결제 방식을 유지합니다. 실제 증권사별 증거금·소액 예외·이자·수량 산정가격·호가 잔량은 재현하지 않습니다. 임시 휴장은 XKRX 달력 패키지의 갱신 여부를 확인해야 합니다.

검증: `python -m unittest tests.test_misu tests.test_order_types tests.test_order_automations -q` (격리 DB·시세). 미수와 입금 UUID 중복, 휴일·연말, 당일/익일 매도, 감시 재시작, 잔여 채무와 최근 시세 여부를 검사합니다.

# GP 모의투자 백엔드

## 예약·조건 모의 주문 (2026-10-06)

`alembic upgrade head`로 `d20261006_automations`를 적용한 뒤 서버를 재시작합니다. 새 테이블이 없으면 기존 API는 유지되고 예약·조건 감시는 시작하지 않습니다.

- `ENABLE_ORDER_AUTOMATIONS=true`(기본값)와 유효한 KIS 시세 키가 필요합니다. API 서버 시작 시 30초 간격의 서버 감시가 시작됩니다. 휴면 서버에서는 실행되지 않습니다.
- `GET /api/trading/automations/capabilities`: 감시 준비 여부와 마지막 실행 주기. 조회·등록·취소는 로그인 필요.
- `POST /api/trading/automations`: `scheduled` 예약 또는 `condition` 가격 이상/이하, 최대 30일·활성 20개/계좌. `client_request_id` UUID로 등록 재시도 중복 방지.
- `GET /api/trading/automations?account_id=...`: 활성 우선 최대 200개 내역.
- `PUT /api/trading/automations/{automation_id}/cancel`: 실행 전 취소.
- 평일 09:00~15:20 KST에 최근 2분 이내 당일 분봉 체결 시세로 1회 시장가 모의 체결합니다. 거래량 없는/오래된 시세, 장외, 통신 장애에서는 보류합니다. 잔고 부족은 거부로 종료합니다.
- 등록 시 현금과 수량을 예약하지 않습니다. 실행 때 수수료·세금을 적용하고 주문 생성·계좌 변경·감시 종료를 같은 트랜잭션으로 저장합니다. 기존 지정가 대기의 자동 체결 기능은 별개로 제공하지 않습니다.
- 감시 중지 설정은 등록 내역을 삭제하지 않습니다. 재시작 시 유효한 작업이 다시 실행 대상이 되므로 종료할 작업은 API로 취소합니다.

상세 공식 자료 분석·UI·API 계약·제한은 동반 프론트 저장소 `Stockmaster/Doc/2026-10-06_예약_조건주문_분석_및_설계.md`에 있습니다. 운영 DB/주문 호출 없이 테스트하려면 `python -m unittest discover -s tests -v`를 실행합니다. PostgreSQL 다중 워커의 실제 잠금 경합은 배포 전 별도 개발 DB에서 확인해야 합니다.

## 시작하기 (처음 세팅)

### 1. Python 가상환경 생성 및 활성화
```bash
# backend 폴더 안에서 실행
python -m venv venv

# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate
```

### 2. 패키지 설치
```bash
pip install -r requirements.txt
```

### 3. 환경변수 설정
```bash
# .env.example을 복사해서 .env 파일 생성 후 값 입력
copy .env.example .env
```
`.env` 파일을 열어서 `DATABASE_URL`에 실제 PostgreSQL 접속 정보를 입력하세요.

### 4. DB 테이블 생성 (Alembic 마이그레이션)
```bash
# 마이그레이션 파일 생성
alembic revision --autogenerate -m "init"

# DB에 테이블 적용
alembic upgrade head
```

### 5. 서버 실행
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

서버 실행 후 브라우저에서 확인:
- API 문서 (Swagger): http://localhost:8000/docs
- 상태 확인: http://localhost:8000/

---

## API 목록

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | /auth/register | 회원가입 |
| POST | /auth/login | 로그인 (JWT 토큰 발급) |
| GET | /auth/me | 내 정보 조회 |
| PUT | /auth/survey | 투자 성향 저장 |
| GET | /accounts | 내 계좌 목록 |
| POST | /accounts | 계좌 개설 |
| GET | /securities | 종목 목록/검색 |
| GET | /securities/{code}/price | 현재가 조회 |
| GET | /securities/{code}/chart | 차트 데이터 |
| POST | /orders | 매수/매도 주문 |
| GET | /orders | 주문 내역 |
| GET | /portfolio | 보유 종목 조회 |
| GET | /news/market | 전체 시장 뉴스 |
| GET | /news/{code} | 종목별 뉴스 |
| GET | /community/posts | 게시글 목록 |
| POST | /community/posts | 게시글 작성 |
| POST | /community/posts/{id}/like | 좋아요 |
| POST | /community/posts/{id}/comments | 댓글 작성 |
| GET | /ranking | 수익률 랭킹 |
| GET | /ai/recommend | AI 종목 추천 |
| GET | /ai/coach | AI 투자 코칭 |

---

## 팀 협업

- **프론트팀**: `http://localhost:8000/docs` 에서 API 명세 확인 가능
- **DB팀**: 스키마 변경 시 `alembic revision --autogenerate -m "변경내용"` 후 공유
- **AI팀**: `.env`의 `AI_SERVER_URL`에 AI 서버 주소 입력


## 보유종목 AI 브리핑

- `GET /api/ai/portfolio-brief/{symbol_code}?account_id={id}&profile=luna`: 로그인 및 본인 계좌/보유종목 확인 후 Fact Bundle → AI Server `POST /brief/portfolio` → 구조화 브리핑을 반환합니다. `profile`은 `luna`(기본값) 또는 `sol`이며 전역 provider를 변경하지 않습니다.
- 기존 `GET /api/ai/portfolio-brief/facts/{symbol_code}`는 그대로 유지합니다. AI 입력에는 계좌/사용자 정보와 Backend 전용 `price_context`를 보내지 않으며 Decimal은 십진수 문자열로 보존합니다. 현재 AI 계약에서 필요한 주문 ID만 유지합니다.
- 외부 응답은 `symbol_code`, `security_name`, `headline`, `current_status`, `position_summary`, `entry_context`, `recent_issues`, `positive_factors`, `risk_factors`, `watch_points`, `limitations`, 고정 `disclaimer`입니다. 내부 provider/model/usage는 공개하지 않으며 `X-Trace-ID`로 요청을 연결합니다.
- AI Server는 `AI_SERVER_URL`로 지정하고 요청 제한시간은 30초입니다. 자동 retry, 모델 fallback, 생성 결과 캐시는 없습니다. 잘못된 profile은 422, upstream timeout/오류/잘못된 응답은 502, 설정/연결 불가 또는 upstream 503은 503으로 안전한 안내만 반환합니다. Backend 입력 검증 실패는 422를 유지하고, AI Server의 내부 계약 거부(HTTP 422)는 안전한 502로 변환합니다. provider 인증 오류를 사용자 로그인 401로 전달하지 않습니다.
- 내부 요청은 `include_ai_metadata=true`를 사용하며 응답의 실행 identity를 필수로 검증합니다. 본문·trace·provider/model/profile identity를 검증하고, 숫자 diagnostics만 잘못되면 정상 본문을 유지한 채 diagnostics를 버립니다.
- AI Server 호출에는 Backend 환경변수 `AI_SERVICE_TOKEN`의 Bearer 인증을 사용합니다. 토큰 누락 시 HTTP를 보내지 않고 뉴스 요약은 안내 fallback, 보유종목 브리핑은 503을 반환합니다. [서비스 인증·배포 안내](../ai_server/docs/service-auth-deployment.md)를 참고하세요.
- 과거 뉴스 timeline은 미지원입니다. 포지션 시작 전 가격 문맥은 아래 범위에서 제공합니다. service-token 인증은 구현되어 있으며 실제 Public AI Server 배포와 양쪽 토큰 설정·연결 검증은 별도 선행조건입니다. 보호되지 않은 AI Server를 외부에 노출하지 않습니다. PostgreSQL migration 적용과 실제 Backend/Frontend E2E도 별도 확인해야 합니다.


### 포지션 시작 전 가격 문맥

Fact Bundle의 `entry_price_context`는 기존 KIS 일봉 차트(`range=Y`, 최근 12개월)의 날짜별 OHLC를 사용합니다. KST 기준 현재 `position.started_at`의 **날짜보다 이른** 확정 일봉만 사용하며 시작 당일 종가와 이후 가격은 제외합니다. 주말/휴장일을 달력으로 보충하지 않고 실제 제공된 거래 세션으로 계산합니다.

- `prior_close`: 시작일 직전 마지막 일봉 종가. 5/20세션 수익률은 각각 6/21개의 종가가 있어야 계산합니다: `(직전 종가 / N세션 전 종가 - 1) × 100`.
- `high_20_sessions`/`low_20_sessions`: 최근 최대 20개 완료 세션의 고저가. 최소 1개면 제공하며 실제 개수 `range_sessions`를 함께 보냅니다. 부족한 수익률은 null, 일부 기간만 있으면 `entry_price_history_partial`입니다.
- `opening_vs_prior_close_pct`: 정확한 최초 체결가격이 있을 때만 계산합니다. 모호한 opening 가격을 평균으로 대체하지 않습니다.
- 포지션 불명 또는 조회 실패/범위 밖/잘못된 일봉은 `available=false`이며 다른 보유 사실을 유지합니다. 기존 `price_context`의 과거 시장가격 필드를 체결가격으로 채우지 않습니다.

AI의 `entry_context`는 이 숫자만 설명합니다. 최신 뉴스와 과거 진입 시점을 연결하거나 사용자 매수 이유/실력/좋고 나쁜 매수를 추정하지 않습니다. Frontend와 실제 시장 데이터/E2E 검증은 별도 단계입니다.
