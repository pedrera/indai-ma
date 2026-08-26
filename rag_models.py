from dataclasses import asdict, dataclass, field
from typing import Any

from gas_type_resolution import GasTypeResolution


@dataclass(frozen=True)
class DocumentPage:
    page_number: int
    text: str


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    name: str
    sha256: str
    pages: list[DocumentPage]


@dataclass(frozen=True)
class DocumentMetadata:
    document_id: str
    document_name: str
    sha256: str
    page_count: int
    chunk_count: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    document_id: str
    document_name: str
    section: str | None
    page_start: int
    page_end: int
    ordinal: int
    text: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IndexedChunk:
    chunk: DocumentChunk
    embedding: list[float]


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: DocumentChunk
    score: float

    def source_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk.chunk_id,
            "document_name": self.chunk.document_name,
            "section": self.chunk.section,
            "page_start": self.chunk.page_start,
            "page_end": self.chunk.page_end,
            "score": self.score,
        }


@dataclass(frozen=True)
class RetrievalResult:
    matches: list[RetrievedChunk]
    embedding_model: str
    gas_type_resolution: GasTypeResolution | None = None


@dataclass(frozen=True)
class IngestionResult:
    document_count: int
    chunk_count: int
    duplicate_count: int = 0
    document_names: list[str] = field(default_factory=list)
