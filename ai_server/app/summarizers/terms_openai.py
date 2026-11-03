import json
from openai import OpenAI
from ..settings import get_openai_api_key
from ..term_schemas import TermDetectResponse
from ..execution import execute_provider_call, openai_usage
from .base import SummarizerError, SummarizerUnavailableError
from .terms import build_term_prompt, sanitize_term_output

class TermDetector:
    def __init__(self,client=None):
        key=get_openai_api_key()
        if client is None and not key:raise SummarizerUnavailableError("Term detector unavailable")
        self.client=client or OpenAI(api_key=key,max_retries=0,timeout=20)
    def detect(self,request,trace_id):
        result=execute_provider_call(trace_id=trace_id,provider="openai",model="gpt-6-luna",
            call=lambda:self.client.responses.create(model="gpt-6-luna",reasoning={"effort":"none"},
                input=build_term_prompt(request),text={"format":{"type":"json_schema","name":"term_detect","strict":True,"schema":TermDetectResponse.model_json_schema()}}),
            parse=lambda r:TermDetectResponse.model_validate(sanitize_term_output(json.loads(r.output_text),request)),usage=openai_usage,
            error_type=SummarizerError,error_message="Term detection failed")
        return result.result
