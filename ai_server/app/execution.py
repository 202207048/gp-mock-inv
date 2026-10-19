"""Request-owned generation observations; never retain SDK bodies or secrets."""
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Generic, TypeVar
from uuid import UUID, uuid4

from .schemas import AIExecutionMetadata, UsageMetrics

T = TypeVar("T")


@dataclass(frozen=True)
class ProviderExecutionResult(Generic[T]):
    result: T
    metadata: AIExecutionMetadata


def normalize_trace_id(value: str | None) -> UUID:
    try:
        parsed = UUID(value) if value and len(value) <= 36 else None
        if parsed is not None and parsed.version == 4:
            return parsed
    except (ValueError, AttributeError):
        pass
    return uuid4()


def _count(obj: Any, name: str) -> int | None:
    value = getattr(obj, name, None)
    return value if type(value) is int and value >= 0 else None


def openai_usage(response: Any) -> UsageMetrics:
    usage = getattr(response, "usage", None)
    return UsageMetrics(
        input_tokens=_count(usage, "input_tokens"),
        output_tokens=_count(usage, "output_tokens"),
        reasoning_tokens=_count(getattr(usage, "output_tokens_details", None), "reasoning_tokens"),
        total_tokens=_count(usage, "total_tokens"),
        cached_tokens=_count(getattr(usage, "input_tokens_details", None), "cached_tokens"),
    )


def gemini_usage(response: Any) -> UsageMetrics:
    usage = getattr(response, "usage_metadata", None)
    return UsageMetrics(
        input_tokens=_count(usage, "prompt_token_count"),
        output_tokens=_count(usage, "candidates_token_count"),
        reasoning_tokens=_count(usage, "thoughts_token_count"),
        total_tokens=_count(usage, "total_token_count"),
        cached_tokens=_count(usage, "cached_content_token_count"),
    )


def execute_provider_call(*, trace_id: UUID | None, provider: str, model: str,
                          call: Callable[[], Any], parse: Callable[[Any], T],
                          usage: Callable[[Any], UsageMetrics], error_type: type[Exception],
                          error_message: str) -> ProviderExecutionResult[T]:
    # ID and timer belong to this invocation, never to a reusable adapter field.
    trace_id = trace_id if trace_id is not None else uuid4()
    operation_id = uuid4()
    started = perf_counter()
    try:
        response = call()
    except Exception as exc:
        metadata = AIExecutionMetadata(
            trace_id=trace_id, operation_id=operation_id, provider=provider, model=model,
            usage=UsageMetrics(), latency_ms=(perf_counter() - started) * 1000,
        )
        raise error_type(error_message, metadata=metadata) from exc
    elapsed = (perf_counter() - started) * 1000
    metadata = AIExecutionMetadata(
        trace_id=trace_id, operation_id=operation_id, provider=provider, model=model,
        usage=usage(response), latency_ms=elapsed,
    )
    try:
        result = parse(response)
    except Exception as exc:
        if isinstance(exc, error_type):
            exc.metadata = metadata
            raise
        raise error_type(error_message, metadata=metadata) from exc
    return ProviderExecutionResult(result=result, metadata=metadata)
