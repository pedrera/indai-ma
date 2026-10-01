"""Industrial demo knowledge on the repository's existing local RAG stack."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from openai import APIError

from diagnostics import PerformanceRecorder, PerformanceStatus
from embeddings import EmbeddingProvider, LMStudioEmbeddingProviderPool
from rag_models import RetrievedChunk
from rag_service import RAGService
from vector_store import LocalVectorStore


IDENTITY_FIELDS = (
    "customer_id", "site_id", "application_id", "gas_product_id", "installation_id",
)
CORPUS_DIR = Path(__file__).resolve().parent / "knowledge_corpus"


class IndustrialKnowledgeOperationalError(RuntimeError):
    """Expected provider or local-storage failure while retrieving knowledge."""


def _document_metadata() -> dict[str, dict[str, str]]:
    return {
        "hospital_o2_supply_contract.txt": {
            "document_type": "supply_contract", "customer_id": "hospital-costa-sur",
            "site_id": "hospital-costa-sur-site", "application_id": "hospital-costa-sur-medical-oxygen",
            "gas_product_id": "medical-oxygen", "installation_id": "hospital-costa-sur-bulk-cryogenic-o2",
        },
        "hospital_o2_operating_procedure.txt": {
            "document_type": "operating_procedure", "customer_id": "hospital-costa-sur",
            "site_id": "hospital-costa-sur-site", "application_id": "hospital-costa-sur-medical-oxygen",
            "gas_product_id": "medical-oxygen", "installation_id": "hospital-costa-sur-bulk-cryogenic-o2",
        },
        "hospital_o2_installation_specification.txt": {
            "document_type": "installation_specification", "customer_id": "hospital-costa-sur",
            "site_id": "hospital-costa-sur-site", "application_id": "hospital-costa-sur-medical-oxygen",
            "gas_product_id": "medical-oxygen", "installation_id": "hospital-costa-sur-bulk-cryogenic-o2",
        },
        "alimentos_co2_supply_contract.txt": {
            "document_type": "supply_contract", "customer_id": "alimentos-del-sur",
            "site_id": "malaga-production-plant", "application_id": "beverage-carbonation",
            "gas_product_id": "co2", "installation_id": "co2-bulk-installation",
        },
        "alimentos_co2_installation_specification.txt": {
            "document_type": "installation_specification", "customer_id": "alimentos-del-sur",
            "site_id": "malaga-production-plant", "application_id": "beverage-carbonation",
            "gas_product_id": "co2", "installation_id": "co2-bulk-installation",
        },
        "alimentos_n2_operating_procedure.txt": {
            "document_type": "operating_procedure", "customer_id": "alimentos-del-sur",
            "site_id": "malaga-production-plant", "application_id": "modified-atmosphere-inerting",
            "gas_product_id": "n2", "installation_id": "n2-bulk-installation",
        },
        "alimentos_n2_installation_specification.txt": {
            "document_type": "installation_specification", "customer_id": "alimentos-del-sur",
            "site_id": "malaga-production-plant", "application_id": "modified-atmosphere-inerting",
            "gas_product_id": "n2", "installation_id": "n2-bulk-installation",
        },
        "global_demo_supply_policy.txt": {
            "document_type": "supply_policy", "scope": "global",
            "applicable_domain": "industrial_gases",
        },
    }


def demo_corpus_files() -> list[tuple[str, bytes]]:
    """Load the explicitly fictional documents shipped for the demo."""
    return [(name, (CORPUS_DIR / name).read_bytes())
            for name in sorted(_document_metadata())]


def demo_corpus_metadata() -> dict[str, dict[str, str]]:
    return {name: dict(metadata) for name, metadata in _document_metadata().items()}


def identity_scope_allows(metadata: dict[str, Any], scope: dict[str, str]) -> bool:
    """Check scope before similarity; only explicitly global docs bypass identity."""
    if metadata.get("scope") == "global":
        return metadata.get("applicable_domain") == "industrial_gases"
    if not scope or not any(field in metadata for field in IDENTITY_FIELDS):
        return False
    return all(metadata.get(field) == value
               for field, value in scope.items()
               if field in IDENTITY_FIELDS and field in metadata)


class IndustrialKnowledgeService:
    """Scoped retrieval preserving the RAG chunk/document provenance."""

    def __init__(self, rag_service: RAGService) -> None:
        self.rag_service = rag_service

    def ensure_demo_corpus(self) -> None:
        expected = set(_document_metadata())
        existing = {item.document_name for item in self.rag_service.store.documents}
        missing = expected - existing
        if missing:
            files = [item for item in demo_corpus_files() if item[0] in missing]
            self.rag_service.ingest(files, metadata_by_name=demo_corpus_metadata())

    def search(
        self,
        *,
        identity: dict[str, str | None],
        query: str,
        top_k: int = 4,
    ) -> tuple[str, tuple[RetrievedChunk, ...]]:
        try:
            self.ensure_demo_corpus()
            scope = {field: identity[field] for field in IDENTITY_FIELDS
                     if identity.get(field) is not None}
            # If identity is incomplete, only explicitly global knowledge is eligible.
            complete_identity = all(identity.get(field) for field in IDENTITY_FIELDS)

            def eligible(chunk) -> bool:
                if chunk.metadata.get("scope") == "global":
                    return identity_scope_allows(chunk.metadata, scope)
                return complete_identity and identity_scope_allows(chunk.metadata, scope)

            retrieval = self.rag_service.retrieve(query, top_k=top_k, eligible_chunk=eligible)
        except (APIError, OSError, TimeoutError, ConnectionError) as error:
            raise IndustrialKnowledgeOperationalError(
                "Industrial knowledge retrieval is temporarily unavailable."
            ) from error
        # The shared vector store always returns its nearest eligible rows. A
        # zero/negative cosine is orthogonal or opposing evidence, not a match.
        matches = tuple(item for item in retrieval.matches if item.score > 0)
        status = "retrieved" if matches else "no_applicable_knowledge"
        return status, matches


def demo_knowledge_service(
    embeddings: EmbeddingProvider,
    recorder: PerformanceRecorder | None = None,
    root: str | Path | None = None,
    embedding_lifecycle_metadata: dict[str, Any] | None = None,
) -> IndustrialKnowledgeService:
    model = embeddings.model
    if root is None:
        configured = Path(os.getenv("RAG_INDEX_PATH", ".indai_ma/rag_index"))
        base = configured if configured.is_absolute() else Path(__file__).resolve().parents[1] / configured
        model_id = hashlib.sha256(model.encode("utf-8")).hexdigest()[:12]
        root = base.parent / "industrial_knowledge_demo" / model_id
    store = LocalVectorStore(root, model)
    return IndustrialKnowledgeService(RAGService(
        embeddings, store, recorder,
        embedding_lifecycle_metadata=embedding_lifecycle_metadata,
    ))


def lazy_demo_knowledge_service(
    provider_pool: LMStudioEmbeddingProviderPool,
    *,
    model: str,
    base_url: str | None,
    api_key: str,
    recorder: PerformanceRecorder | None = None,
    root: str | Path | None = None,
) -> Callable[[], IndustrialKnowledgeService]:
    """Return an operation-scoped knowledge service factory with pooled embeddings."""
    def create() -> IndustrialKnowledgeService:
        started = perf_counter()
        try:
            embeddings, lifecycle = provider_pool.get_or_create_with_status(
                model=model, base_url=base_url, api_key=api_key,
            )
        except Exception:
            _record_pool_lookup(
                recorder, PerformanceStatus.FAILED, perf_counter() - started,
                provider_pool_lookup_occurred=True,
                provider_pool_hit=False,
                provider_pool_size=provider_pool.provider_count,
            )
            raise
        lifecycle = {**lifecycle, "provider_pool_lookup_occurred": True}
        _record_pool_lookup(
            recorder, PerformanceStatus.COMPLETED, perf_counter() - started, **lifecycle,
        )
        # Keep the RAG service, vector-store wrapper and recorder operation-scoped.
        return demo_knowledge_service(
            embeddings, recorder, root, embedding_lifecycle_metadata=lifecycle,
        )

    return create


def _record_pool_lookup(
    recorder: PerformanceRecorder | None,
    status: PerformanceStatus,
    duration_seconds: float,
    **metadata: Any,
) -> None:
    if recorder is None:
        return
    try:
        recorder.record_stage(
            "embedding_provider_pool_lookup", status=status,
            duration_seconds=max(0.0, duration_seconds), **metadata,
        )
    except Exception:
        # Diagnostics must never change provider lookup or retrieval behavior.
        return
