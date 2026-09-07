"""
services/ai_service.py - Gemini 투자성향 분석

AI팀 로직을 백엔드에 통합했다.
별도 AI 서버를 띄우지 않고, 설문 저장 시점에 Gemini를 직접 호출한다.

모델은 무료 티어 Flash를 사용한다.
"""

import json

from google import genai
from google.genai import types

from app.config import settings

# 프론트 설문 문항 번호 → 프롬프트에 넣을 질문 문구
_QUESTION_LABELS = {
    1: "투자 경험",
    2: "자금 성격",
    3: "감당 가능한 손실 범위",
    4: "원금 보존 중요도",
    5: "예상 투자 기간",
    6: "투자 목표",
}

_FALLBACK = {
    "investor_type": "위험중립형",
    "risk_score": 50,
    "summary": "AI 분석에 실패해 기본 성향으로 안내합니다.",
    "advice": "설문을 다시 제출해 주세요.",
    "learning_roadmap": [],
}


def _answers_to_prompt_lines(answers: list[dict]) -> str:
    lines = []
    for item in answers:
        number = item.get("question_number")
        label = _QUESTION_LABELS.get(number, f"문항 {number}")
        selected = item.get("selected_answer", "")
        lines.append(f"{number}. {label}: {selected}")
    return "\n".join(lines) if lines else "응답 없음"


def analyze_survey_answers(answers: list[dict]) -> dict:
    """
    설문 답변을 Gemini에 보내 투자성향을 분석한다.

    Args:
        answers: [{"question_number": 1, "selected_answer": "1년 미만"}, ...]

    Returns:
        investor_type, risk_score, summary, advice, learning_roadmap
    """
    if not settings.GEMINI_API_KEY:
        print("[Gemini] GEMINI_API_KEY가 설정되지 않았습니다.")
        return _FALLBACK

    prompt = f"""
당신은 주식 모의투자 플랫폼의 금융 AI 전문가입니다.
사용자의 설문 응답을 분석하여 최적의 투자 성향 프로필을 도출하세요.

[사용자 설문 응답]
{_answers_to_prompt_lines(answers)}

[출력 규칙]
- investor_type: 반드시 ["안정형", "안정추구형", "위험중립형", "적극투자형", "공격투자형"] 중 하나
- risk_score: 0~100 사이의 정수
- summary: 사용자 성향 요약 (1~2문장)
- advice: 모의투자 실천 조언 (1~2문장)
- learning_roadmap: 단계별 학습 주제 3가지 문자열 배열

반드시 순수 JSON 포맷으로만 응답하세요.
"""

    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        data = json.loads(response.text)
        investor_type = data.get("investor_type", _FALLBACK["investor_type"])
        allowed = {"안정형", "안정추구형", "위험중립형", "적극투자형", "공격투자형"}
        if investor_type not in allowed:
            investor_type = _FALLBACK["investor_type"]
        return {
            "investor_type": investor_type,
            "risk_score": int(data.get("risk_score", 50)),
            "summary": str(data.get("summary", "")),
            "advice": str(data.get("advice", "")),
            "learning_roadmap": data.get("learning_roadmap") or [],
        }
    except Exception as e:
        print(f"[Gemini] 분석 오류: {e}")
        return _FALLBACK
