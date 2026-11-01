import json
from openai import OpenAI
from ..settings import get_openai_api_key
from ..stock_brief_schemas import StockBriefResponse
from ..execution import execute_provider_call, openai_usage
from .base import SummarizerError, SummarizerUnavailableError
from .stock_brief import build_stock_brief_prompt, sanitize_stock_output

class StockBriefGenerator:
    def __init__(self,client=None):
        key=get_openai_api_key()
        if client is None and not key:raise SummarizerUnavailableError("Stock generator unavailable")
        self.client=client or OpenAI(api_key=key,max_retries=0,timeout=25)
    def generate(self,request,trace_id):
        result=execute_provider_call(trace_id=trace_id,provider="openai",model="gpt-6-luna",
            call=lambda:self.client.responses.create(model="gpt-6-luna",reasoning={"effort":"none"},
                input=build_stock_brief_prompt(request),text={"format":{"type":"json_schema","name":"stock_brief","strict":True,"schema":StockBriefResponse.model_json_schema()}}),
            parse=lambda r:StockBriefResponse.model_validate(sanitize_stock_output(json.loads(r.output_text),request)),usage=openai_usage,
            error_type=SummarizerError,error_message="Stock generation failed")
        return result.result
