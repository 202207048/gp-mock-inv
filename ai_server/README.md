# 뉴스 분석 AI 서버

FastAPI 기반 뉴스 투자영향 분석 서비스입니다. 기사 문체의 감정이 아니라 해당 종목이나 시장에 미치는 방향과 중요도를 분석합니다.

## API

`POST /analyze/news`에 기사 1~50건을 보내면 입력 순서대로 `article_id`, `sentiment` (`label`, `confidence`), `importance` (`score`, `label`)를 반환합니다. 중요도 점수는 0~1이며 label은 서버 기준으로 산출합니다.

기본 provider는 GPT-6 Luna (`reasoning_effort=none`)이며 Gemini 기본 모델은 `gemini-3.8-flash`입니다. `AI_PROVIDER=gemini`로 시작 provider를 설정할 수 있고, 실행 중에는 `GET /settings/provider`로 조회하거나 `PUT /settings/provider`에 `{"provider":"openai"}` 또는 `{"provider":"gemini"}`를 보내 전환할 수 있습니다. 변경은 현재 프로세스에만 유지되며 재시작하면 `AI_PROVIDER` 설정으로 초기화됩니다. provider 오류 시 자동 전환하지 않습니다.

## 설치, 실행, 테스트

Python 환경에서 `ai_server` 폴더로 이동한 뒤:

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`에 사용할 provider의 키를 설정한 다음 실행합니다.

```powershell
python -m uvicorn app.main:app --reload --port 8001
python -m pytest tests
```

환경변수 예제와 Backend/Frontend 연결 안내는 [팀 인수인계 문서](docs/team-handoff.md)를 참고하세요.
