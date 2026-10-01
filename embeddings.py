from __future__ import annotations

import os
import re
import hashlib
import threading
from uuid import uuid4
from abc import ABC, abstractmethod
from time import perf_counter
from typing import Any

from dotenv import load_dotenv
from openai import DefaultHttpxClient, OpenAI

from diagnostics import PerformanceStatus


load_dotenv()


DEFAULT_EMBEDDING_MODEL = "text-embedding-nomic-embed-text-v1.5"
EMBEDDING_BATCH_SIZE = 32
_HTTP_REQUEST_STARTED = "indai_embedding_request_started"
_TIMING_HEADERS = frozenset({
    "server-timing", "x-process-time", "x-inference-time", "x-compute-time",
})


def get_embedding_model_name() -> str:
    return (
        os.getenv("RAG_EMBEDDING_MODEL", "").strip()
        or DEFAULT_EMBEDDING_MODEL
    )


def get_lmstudio_embedding_api_key() -> str:
    """Return the configured embedding key without exposing it to diagnostics."""
    return os.getenv("LMSTUDIO_API_KEY") or "lm-studio"


class EmbeddingProvider(ABC):
    model: str

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError


class LMStudioEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        recorder: Any | None = None,
        api_key: str | None = None,
    ) -> None:
        base_url = (base_url or os.getenv("LMSTUDIO_BASE_URL", "")).strip()
        if not base_url:
            raise ValueError(
                "LMSTUDIO_BASE_URL es necesaria para generar embeddings."
            )
        self.model = model or get_embedding_model_name()
        self.base_url = base_url
        self.api_key = api_key if api_key is not None else get_lmstudio_embedding_api_key()
        self.provider_instance_id = uuid4().hex[:16]
        self.client_instance_id = uuid4().hex[:16]
        self.recorder = recorder
        self._request_local = threading.local()
        self._request_lock = threading.Lock()
        self._request_count = 0
        client_started = perf_counter()
        http_client = DefaultHttpxClient(event_hooks={
            "request": [self._on_http_request],
            "response": [self._on_http_response],
        })
        self.client = OpenAI(
            base_url=base_url,
            api_key=self.api_key,
            max_retries=0,
            http_client=http_client,
        )
        self._client_initialization_seconds = perf_counter() - client_started

    def set_recorder(self, recorder: Any | None) -> None:
        """Attach the operation recorder without changing provider behavior."""
        self.recorder = recorder

    def _on_http_request(self, request: Any) -> None:
        # HTTPX calls hooks for each attempt/redirect. Keep only timing state and
        # never inspect or retain the request URL body, headers, or credentials.
        try:
            now = perf_counter()
            request.extensions[_HTTP_REQUEST_STARTED] = now
            self._request_local.request_hook_at = now
        except Exception:
            return

    def _on_http_response(self, response: Any) -> None:
        try:
            request = response.request
            started = request.extensions.pop(_HTTP_REQUEST_STARTED, None)
            self._request_local.response_headers_seconds = (
                max(0.0, perf_counter() - started) if isinstance(started, (int, float)) else None
            )
            self._request_local.response_hook_at = perf_counter()
            headers = response.headers
            safe_headers = {}
            for name, value in headers.items():
                folded_name = name.casefold()
                if folded_name not in _TIMING_HEADERS:
                    continue
                safe_value = _safe_timing_header_value(folded_name, str(value))
                if safe_value is not None:
                    safe_headers[folded_name] = safe_value
            self._request_local.server_timing_headers = safe_headers
        except Exception:
            self._request_local.response_headers_seconds = None
            self._request_local.response_hook_at = None
            self._request_local.server_timing_headers = {}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed_documents(texts, recorder=self.recorder)

    def embed_documents_with_recorder(
        self, texts: list[str], recorder: Any | None,
        lifecycle_metadata: dict[str, Any] | None = None,
    ) -> list[list[float]]:
        """Attach operation telemetry without mutating a shared provider."""
        return self._embed_documents(
            texts, recorder=recorder, lifecycle_metadata=lifecycle_metadata,
        )

    def _embed_documents(
        self, texts: list[str], *, recorder: Any | None,
        lifecycle_metadata: dict[str, Any] | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + EMBEDDING_BATCH_SIZE]
            vectors.extend(self._create_embeddings(
                operation="documents", original_texts=batch, prefix="search_document: ",
                recorder=recorder, lifecycle_metadata=lifecycle_metadata,
            ))
        _validate_vectors(vectors, len(texts))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed_query(text, recorder=self.recorder)

    def embed_query_with_recorder(
        self, text: str, recorder: Any | None,
        lifecycle_metadata: dict[str, Any] | None = None,
    ) -> list[float]:
        """Attach telemetry to this call, safe when a provider serves many operations."""
        return self._embed_query(
            text, recorder=recorder, lifecycle_metadata=lifecycle_metadata,
        )

    def _embed_query(
        self, text: str, *, recorder: Any | None,
        lifecycle_metadata: dict[str, Any] | None = None,
    ) -> list[float]:
        if not text.strip():
            raise ValueError("La consulta RAG no puede estar vacía.")
        vector = self._create_embeddings(
            operation="query", original_texts=[text], prefix="search_query: ",
            recorder=recorder, lifecycle_metadata=lifecycle_metadata,
        )
        return vector[0]

    def _create_embeddings(
        self, *, operation: str, original_texts: list[str], prefix: str,
        recorder: Any | None = None,
        lifecycle_metadata: dict[str, Any] | None = None,
    ) -> list[list[float]]:
        total_started = perf_counter()
        # Prefixing and request-input metadata are local work. Count the query
        # separately from the model prefix, without storing or logging its text.
        preparation_started = perf_counter()
        prepared_inputs = [f"{prefix}{text}" for text in original_texts]
        character_count = sum(len(text) for text in original_texts)
        byte_count = sum(len(text.encode("utf-8")) for text in original_texts)
        preparation_seconds = perf_counter() - preparation_started

        with self._request_lock:
            client_reused = self._request_count > 0
            self._request_count += 1
            client_initialization_seconds = (
                self._client_initialization_seconds if self._request_count == 1 else None
            )
        self._request_local.response_headers_seconds = None
        self._request_local.response_hook_at = None
        self._request_local.request_hook_at = None
        self._request_local.server_timing_headers = {}
        api_started = perf_counter()
        response = None
        failure = False
        try:
            response = self.client.embeddings.create(model=self.model, input=prepared_inputs)
            data = sorted(response.data, key=lambda item: item.index)
            vectors = [list(item.embedding) for item in data]
            _validate_vectors(vectors, len(prepared_inputs))
            return vectors
        except BaseException:
            failure = True
            raise
        finally:
            api_finished = perf_counter()
            response_hook_at = getattr(self._request_local, "response_hook_at", None)
            response_parse_seconds = (
                max(0.0, api_finished - response_hook_at)
                if isinstance(response_hook_at, (int, float)) else None
            )
            metadata: dict[str, Any] = {
                "embedding_operation": operation,
                "embedding_model": self.model,
                "embedding_provider_instance_id": self.provider_instance_id,
                "embedding_client_instance_id": self.client_instance_id,
                "embedding_input_count": len(original_texts),
                "query_character_length": character_count if operation == "query" else None,
                "query_byte_length": byte_count if operation == "query" else None,
                "embedding_input_character_length": sum(map(len, prepared_inputs)),
                "embedding_input_byte_length": sum(len(value.encode("utf-8")) for value in prepared_inputs),
                "embedding_dimensions": len(vectors[0]) if not failure and vectors else None,
                "embedding_preparation_seconds": preparation_seconds,
                "client_initialization_seconds": client_initialization_seconds,
                "client_reused": client_reused,
                "client_pre_http_seconds": self._elapsed_from_hook(api_started),
                "http_request_to_response_headers_seconds": getattr(
                    self._request_local, "response_headers_seconds", None,
                ),
                "response_body_and_sdk_parse_seconds": response_parse_seconds,
                "total_embedding_request_seconds": max(0.0, api_finished - total_started),
                "server_timing_headers": getattr(self._request_local, "server_timing_headers", {}),
            }
            if lifecycle_metadata:
                # Pool metadata is operation-scoped; never mutate shared provider state.
                metadata.update(lifecycle_metadata)
            try:
                usage = getattr(response, "usage", None) if response is not None else None
                metadata["provider_prompt_tokens"] = _safe_nonnegative_int(
                    getattr(usage, "prompt_tokens", None) if usage is not None else None,
                )
                metadata["provider_total_tokens"] = _safe_nonnegative_int(
                    getattr(usage, "total_tokens", None) if usage is not None else None,
                )
            except Exception:
                metadata["provider_prompt_tokens"] = None
                metadata["provider_total_tokens"] = None
            self._record_telemetry(
                recorder if recorder is not None else self.recorder,
                PerformanceStatus.FAILED if failure else PerformanceStatus.COMPLETED,
                max(0.0, api_finished - total_started), metadata,
            )

    def _elapsed_from_hook(self, api_started: float) -> float | None:
        # The request event fires immediately before HTTPX dispatch. This local
        # interval is SDK/client request construction, not network connection time.
        started = getattr(self._request_local, "request_hook_at", None)
        return max(0.0, started - api_started) if isinstance(started, (int, float)) else None

    def _record_telemetry(
        self, recorder: Any | None, status: PerformanceStatus, duration_seconds: float,
        metadata: dict[str, Any],
    ) -> None:
        if recorder is None:
            return
        try:
            recorder.record_stage(
                "embedding_request", status=status, duration_seconds=duration_seconds, **metadata,
            )
        except Exception:
            # Optional diagnostics must never turn a successful embedding into a failure.
            return


class LMStudioEmbeddingProviderPool:
    """Thread-safe, owner-scoped reuse of embedding clients by effective config.

    The pool itself has no global lifetime. The application owns one per Workspace
    session, so unrelated tests and users never share providers implicitly.
    """

    def __init__(self, provider_factory=None) -> None:
        self._provider_factory = provider_factory or LMStudioEmbeddingProvider
        self._lock = threading.RLock()
        self._providers: dict[tuple[str, str, str, str], LMStudioEmbeddingProvider] = {}

    def get_or_create(
        self, *, model: str | None = None, base_url: str | None = None,
        api_key: str | None = None,
    ) -> LMStudioEmbeddingProvider:
        return self.get_or_create_with_status(
            model=model, base_url=base_url, api_key=api_key,
        )[0]

    def get_or_create_with_status(
        self, *, model: str | None = None, base_url: str | None = None,
        api_key: str | None = None,
    ) -> tuple[LMStudioEmbeddingProvider, dict[str, Any]]:
        effective_model = model or get_embedding_model_name()
        effective_base_url = (base_url or os.getenv("LMSTUDIO_BASE_URL", "")).strip()
        effective_api_key = (
            api_key if api_key is not None else get_lmstudio_embedding_api_key()
        )
        if not effective_base_url:
            # Keep the existing lazy configuration failure at provider creation.
            raise ValueError("LMSTUDIO_BASE_URL es necesaria para generar embeddings.")
        key, fingerprint = _embedding_configuration_key(
            effective_model, effective_base_url, effective_api_key,
        )
        with self._lock:
            provider = self._providers.get(key)
            pool_hit = provider is not None
            if provider is None:
                provider = self._provider_factory(
                    model=effective_model, base_url=effective_base_url,
                    api_key=effective_api_key,
                )
                self._providers[key] = provider
            metadata = {
                "provider_pool_hit": pool_hit,
                "provider_pool_size": len(self._providers),
                "provider_pool_key_fingerprint": fingerprint,
                "embedding_provider_instance_id": getattr(provider, "provider_instance_id", None),
                "embedding_client_instance_id": getattr(provider, "client_instance_id", None),
            }
            return provider, metadata

    @property
    def provider_count(self) -> int:
        with self._lock:
            return len(self._providers)


def _embedding_configuration_key(
    model: str, base_url: str, api_key: str,
) -> tuple[tuple[str, str, str, str], str]:
    key_secret = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    key = ("lmstudio", model, base_url, key_secret)
    fingerprint = hashlib.sha256("\0".join(key).encode("utf-8")).hexdigest()[:16]
    return key, fingerprint


def _safe_nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _safe_timing_header_value(name: str, value: str) -> str | None:
    if name == "server-timing":
        return value if re.fullmatch(
            r"[A-Za-z0-9_-]+;dur=\d+(?:\.\d+)?(?:,\s*[A-Za-z0-9_-]+;dur=\d+(?:\.\d+)?)*",
            value,
        ) else None
    return value if re.fullmatch(r"\d+(?:\.\d+)?(?:ms|s)?", value.strip(), re.IGNORECASE) else None


def _validate_vectors(vectors: list[list[float]], expected: int) -> None:
    if len(vectors) != expected or not vectors:
        raise ValueError("LM Studio devolvió un número inesperado de embeddings.")
    dimension = len(vectors[0])
    if dimension == 0 or any(len(vector) != dimension for vector in vectors):
        raise ValueError("Los embeddings devueltos tienen dimensiones inválidas.")
