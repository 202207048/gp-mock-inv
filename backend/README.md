# GP 모의투자 백엔드

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
