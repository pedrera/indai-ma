import os

from application_service import AnalysisService
from embeddings import LMStudioEmbeddingProvider, get_embedding_model_name
from llm_client import get_llm_provider
from rag_service import RAGService
from runtime_config import LLMRuntimeConfig
from vector_store import LocalVectorStore


def build_service() -> tuple[AnalysisService, LLMRuntimeConfig]:
    runtime = LLMRuntimeConfig.from_environment()
    store = None
    try:
        store = LocalVectorStore.from_environment(get_embedding_model_name())
    except Exception:
        # Procurement and Risk remain usable without an optional contract index.
        store = None

    def rag_factory(recorder, config):
        if store is None or not store.chunk_count:
            raise ValueError("El índice RAG contractual no está disponible.")
        return RAGService(LMStudioEmbeddingProvider(), store, recorder)

    def provider_factory(recorder, config):
        provider_name = os.getenv("LLM_PROVIDER", "lmstudio")
        model_name = os.getenv("LMSTUDIO_MODEL") or os.getenv("OPENAI_MODEL")
        return get_llm_provider(provider_name, model_name, recorder=recorder, runtime_config=config)

    return AnalysisService(provider_factory=provider_factory, rag_factory=rag_factory), runtime
