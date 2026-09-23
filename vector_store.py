import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable

from chunking import CHUNKER_VERSION
from rag_models import (
    DocumentChunk,
    DocumentMetadata,
    IndexedChunk,
    RetrievedChunk,
)


INDEX_SCHEMA_VERSION = 2
APPLICATION_ROOT = Path(__file__).resolve().parent


class VectorStoreError(ValueError):
    pass


class LocalVectorStore:
    def __init__(self, directory: str | Path, embedding_model: str) -> None:
        self.directory = Path(directory)
        self.embedding_model = embedding_model
        self._items: list[IndexedChunk] = []
        self._documents: dict[str, DocumentMetadata] = {}
        self._document_hashes: set[str] = set()
        self.embedding_dimension: int | None = None
        self.load()

    @classmethod
    def from_environment(cls, embedding_model: str) -> "LocalVectorStore":
        configured = Path(
            os.getenv("RAG_INDEX_PATH", ".indai_ma/rag_index")
        )
        path = (
            configured
            if configured.is_absolute()
            else APPLICATION_ROOT / configured
        )
        return cls(path, embedding_model)

    @property
    def chunk_count(self) -> int:
        return len(self._items)

    @property
    def document_count(self) -> int:
        return len(self._documents)

    @property
    def documents(self) -> tuple[DocumentMetadata, ...]:
        return tuple(
            self._documents[key] for key in sorted(self._documents)
        )

    def has_document_hash(self, document_hash: str) -> bool:
        return document_hash in self._document_hashes

    def add(
        self,
        indexed_chunks: list[IndexedChunk],
        documents: list[DocumentMetadata],
    ) -> None:
        if not indexed_chunks:
            return
        dimensions = {len(item.embedding) for item in indexed_chunks}
        if len(dimensions) != 1 or 0 in dimensions:
            raise VectorStoreError("Los embeddings tienen dimensiones incompatibles.")
        dimension = next(iter(dimensions))
        if self.embedding_dimension not in (None, dimension):
            raise VectorStoreError(
                "El índice utiliza una dimensión de embeddings diferente."
            )
        existing_ids = {item.chunk.chunk_id for item in self._items}
        self._items.extend(
            IndexedChunk(item.chunk, _normalize(item.embedding))
            for item in indexed_chunks
            if item.chunk.chunk_id not in existing_ids
        )
        self._documents.update(
            {document.document_id: document for document in documents}
        )
        self._document_hashes.update(
            document.sha256 for document in documents
        )
        self.embedding_dimension = dimension
        self.save()

    def search(
        self, query_embedding: list[float], top_k: int = 4,
        eligible: Callable[[DocumentChunk], bool] | None = None,
    ) -> list[RetrievedChunk]:
        if top_k < 1:
            raise ValueError("top_k debe ser mayor que cero.")
        if not self._items:
            return []
        if len(query_embedding) != self.embedding_dimension:
            raise VectorStoreError(
                "La consulta y el índice tienen dimensiones incompatibles."
            )
        query = _normalize(query_embedding)
        matches = [
            RetrievedChunk(
                chunk=item.chunk,
                score=sum(a * b for a, b in zip(query, item.embedding)),
            )
            for item in self._items
            if eligible is None or eligible(item.chunk)
        ]
        matches.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
        return matches[:top_k]

    def clear(self) -> None:
        self._items = []
        self._documents = {}
        self._document_hashes = set()
        self.embedding_dimension = None
        self.save()

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "chunker_version": CHUNKER_VERSION,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "document_hashes": sorted(self._document_hashes),
            "document_count": self.document_count,
            "chunk_count": self.chunk_count,
        }
        documents = [document.as_dict() for document in self.documents]
        records = [
            {**item.chunk.as_dict(), "embedding": item.embedding}
            for item in self._items
        ]
        _atomic_jsonl_write(self.directory / "chunks.jsonl", records)
        _atomic_json_write(self.directory / "documents.json", documents)
        _atomic_json_write(self.directory / "manifest.json", manifest)

    def load(self) -> None:
        manifest_path = self.directory / "manifest.json"
        chunks_path = self.directory / "chunks.jsonl"
        documents_path = self.directory / "documents.json"
        existing = [
            path.exists()
            for path in (manifest_path, chunks_path, documents_path)
        ]
        if not any(existing):
            return
        if manifest_path.exists() and chunks_path.exists() and not documents_path.exists():
            legacy_manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            if legacy_manifest.get("schema_version") == 1:
                self._migrate_v1(legacy_manifest, chunks_path)
                return
        if not all(existing):
            raise VectorStoreError("El índice RAG persistido está incompleto.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("schema_version") != INDEX_SCHEMA_VERSION
            or manifest.get("chunker_version") != CHUNKER_VERSION
            or manifest.get("embedding_model") != self.embedding_model
        ):
            raise VectorStoreError(
                "El índice RAG no es compatible con la configuración actual."
            )
        items: list[IndexedChunk] = []
        for line in chunks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data: dict[str, Any] = json.loads(line)
            embedding = [float(value) for value in data.pop("embedding")]
            items.append(IndexedChunk(DocumentChunk(**data), embedding))
        self._items = items
        document_data = json.loads(documents_path.read_text(encoding="utf-8"))
        self._documents = {
            item["document_id"]: DocumentMetadata(**item)
            for item in document_data
        }
        self._document_hashes = {
            document.sha256 for document in self._documents.values()
        }
        self.embedding_dimension = manifest.get("embedding_dimension")
        if (
            manifest.get("document_count") != self.document_count
            or manifest.get("chunk_count") != self.chunk_count
        ):
            raise VectorStoreError(
                "Los contadores del índice RAG persistido no son válidos."
            )
        if self._items and any(
            len(item.embedding) != self.embedding_dimension
            for item in self._items
        ):
            raise VectorStoreError(
                "El índice RAG contiene embeddings incompatibles."
            )

    def reopen(self) -> "LocalVectorStore":
        return LocalVectorStore(self.directory, self.embedding_model)

    def _migrate_v1(self, manifest: dict[str, Any], chunks_path: Path) -> None:
        if (
            manifest.get("chunker_version") != CHUNKER_VERSION
            or manifest.get("embedding_model") != self.embedding_model
        ):
            raise VectorStoreError(
                "El índice RAG legado no es compatible con la configuración actual."
            )
        items: list[IndexedChunk] = []
        for line in chunks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data: dict[str, Any] = json.loads(line)
            embedding = [float(value) for value in data.pop("embedding")]
            items.append(IndexedChunk(DocumentChunk(**data), embedding))
        legacy_hashes = set(manifest.get("document_hashes", []))
        documents: dict[str, DocumentMetadata] = {}
        for document_id in {item.chunk.document_id for item in items}:
            document_items = [
                item for item in items if item.chunk.document_id == document_id
            ]
            sha256 = next(
                (value for value in legacy_hashes if value.startswith(document_id)),
                document_id,
            )
            documents[document_id] = DocumentMetadata(
                document_id=document_id,
                document_name=document_items[0].chunk.document_name,
                sha256=sha256,
                page_count=max(item.chunk.page_end for item in document_items),
                chunk_count=len(document_items),
            )
        self._items = items
        self._documents = documents
        self._document_hashes = {
            document.sha256 for document in documents.values()
        }
        self.embedding_dimension = manifest.get("embedding_dimension")
        self.save()


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise VectorStoreError("No se puede indexar un embedding nulo.")
    return [value / norm for value in vector]


def _atomic_json_write(path: Path, data: Any) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def _atomic_jsonl_write(path: Path, records: list[dict[str, Any]]) -> None:
    content = "\n".join(
        json.dumps(record, ensure_ascii=False) for record in records
    )
    _atomic_write(path, content + ("\n" if content else ""))


def _atomic_write(path: Path, content: str) -> None:
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)
