"""Manually measure three LM Studio query-embedding requests without logging query text."""
from __future__ import annotations

import sys
from pathlib import Path

# Direct script invocation places scripts/ on sys.path.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from diagnostics import PerformanceRecorder
from embeddings import LMStudioEmbeddingProvider, get_embedding_model_name


QUERIES = (
    "¿Qué dice el contrato sobre la entrega?",
    "¿Qué dice el contrato sobre la entrega?",
    "¿Plazo de entrega?",
)


def main() -> int:
    model = get_embedding_model_name()
    recorder = PerformanceRecorder("embedding-benchmark", "lmstudio", model, "embedding_benchmark")
    provider = LMStudioEmbeddingProvider(model=model, recorder=recorder)
    try:
        for index, query in enumerate(QUERIES, start=1):
            try:
                vector = provider.embed_query(query)
            except Exception as error:
                # Avoid printing an exception that might contain request details.
                print(f"request={index} status=FAILED error_type={type(error).__name__}")
                return 1
            event = next(
                item for item in reversed(recorder.snapshot().events)
                if item.stage == "embedding_request"
            )
            data = event.metadata
            print(
                f"request={index} status=PASS model={data['embedding_model']} "
                f"query_chars={data['query_character_length']} "
                f"query_bytes={data['query_byte_length']} dimensions={len(vector)} "
                f"client_reused={data['client_reused']} "
                f"client_initialization_seconds={_format(data['client_initialization_seconds'])} "
                f"pre_http_seconds={_format(data['client_pre_http_seconds'])} "
                f"request_to_headers_seconds={_format(data['http_request_to_response_headers_seconds'])} "
                f"body_and_sdk_parse_seconds={_format(data['response_body_and_sdk_parse_seconds'])} "
                f"total_seconds={_format(data['total_embedding_request_seconds'])} "
                f"provider_prompt_tokens={data['provider_prompt_tokens']} "
                f"provider_total_tokens={data['provider_total_tokens']} "
                f"server_timing_headers={data['server_timing_headers']}"
            )
    finally:
        provider.client.close()
    return 0


def _format(value: float | None) -> str:
    return f"{value:.4f}" if isinstance(value, (int, float)) else "unavailable"


if __name__ == "__main__":
    raise SystemExit(main())
