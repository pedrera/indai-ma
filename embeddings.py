import os
from abc import ABC, abstractmethod

from openai import OpenAI
from dotenv import load_dotenv


load_dotenv()


DEFAULT_EMBEDDING_MODEL = "text-embedding-nomic-embed-text-v1.5"
EMBEDDING_BATCH_SIZE = 32


def get_embedding_model_name() -> str:
    return (
        os.getenv("RAG_EMBEDDING_MODEL", "").strip()
        or DEFAULT_EMBEDDING_MODEL
    )


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
    ) -> None:
        base_url = (base_url or os.getenv("LMSTUDIO_BASE_URL", "")).strip()
        if not base_url:
            raise ValueError(
                "LMSTUDIO_BASE_URL es necesaria para generar embeddings."
            )
        self.model = model or get_embedding_model_name()
        self.client = OpenAI(
            base_url=base_url,
            api_key="lm-studio",
            max_retries=0,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + EMBEDDING_BATCH_SIZE]
            response = self.client.embeddings.create(
                model=self.model,
                input=[f"search_document: {text}" for text in batch],
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(list(item.embedding) for item in ordered)
        _validate_vectors(vectors, len(texts))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("La consulta RAG no puede estar vacía.")
        response = self.client.embeddings.create(
            model=self.model,
            input=[f"search_query: {text}"],
        )
        vectors = [list(item.embedding) for item in response.data]
        _validate_vectors(vectors, 1)
        return vectors[0]


def _validate_vectors(vectors: list[list[float]], expected: int) -> None:
    if len(vectors) != expected or not vectors:
        raise ValueError("LM Studio devolvió un número inesperado de embeddings.")
    dimension = len(vectors[0])
    if dimension == 0 or any(len(vector) != dimension for vector in vectors):
        raise ValueError("Los embeddings devueltos tienen dimensiones inválidas.")
