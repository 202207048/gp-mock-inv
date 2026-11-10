import json
from openai import OpenAI
from ..settings import get_openai_api_key
from ..asset_coach_schemas import AssetCoachDraft
from ..execution import execute_provider_call, openai_usage
from .base import SummarizerError, SummarizerUnavailableError
from .asset_coach import CoachRejected, build_asset_coach_prompt, validate_asset_coach

MODEL = "gpt-6-luna"
ATTEMPTS = 2


def _parse(response, facts):
    try:
        data = json.loads(response.output_text)
    except (TypeError, ValueError) as exc:
        raise CoachRejected("json") from exc
    return validate_asset_coach(data, facts)


class AssetCoachGenerator:
    def __init__(self, client=None):
        key = get_openai_api_key()
        if client is None and not key:
            raise SummarizerUnavailableError("Asset coach generator unavailable")
        # 문단이 늘어 한 번에 8초쯤 걸린다. 두 번 시도해도 백엔드의 45초 제한 안에 끝나게 한다.
        self.client = client or OpenAI(api_key=key, max_retries=0, timeout=20)

    def generate(self, request, trace_id):
        prompt = build_asset_coach_prompt(request)
        schema = AssetCoachDraft.model_json_schema()
        for attempt in range(ATTEMPTS):
            try:
                return execute_provider_call(
                    trace_id=trace_id, provider="openai", model=MODEL,
                    call=lambda: self.client.responses.create(
                        model=MODEL, reasoning={"effort": "none"}, input=prompt,
                        text={"format": {"type": "json_schema", "name": "asset_coach", "strict": True, "schema": schema}}),
                    parse=lambda r: _parse(r, request.facts), usage=openai_usage,
                    error_type=SummarizerError, error_message="Asset coach generation failed",
                ).result
            except CoachRejected:
                # 검사에 걸린 결과만 한 번 다시 만든다. 제공자 오류는 다시 부르지 않는다.
                if attempt == ATTEMPTS - 1:
                    raise
