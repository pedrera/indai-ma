import hashlib
import re

from rag_models import DocumentChunk, SourceDocument


CHUNKER_VERSION = 2
DEFAULT_CHUNK_SIZE = 1600
DEFAULT_CHUNK_OVERLAP = 240


def chunk_document(
    document: SourceDocument,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[DocumentChunk]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Configuración de chunking inválida.")
    chunks: list[DocumentChunk] = []
    ordinal = 0
    current_section: str | None = None
    for page in document.pages:
        text = _normalize_text(page.text)
        if not text:
            continue
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text)]
        buffer = ""
        for paragraph in paragraphs:
            if not paragraph:
                continue
            if _looks_like_heading(paragraph):
                if buffer:
                    chunks.append(
                        _make_chunk(
                            document, page.page_number, ordinal, buffer,
                            current_section,
                        )
                    )
                    ordinal += 1
                    buffer = ""
                current_section = paragraph[:160]
            for piece in _split_long_text(paragraph, chunk_size):
                candidate = f"{buffer}\n\n{piece}".strip() if buffer else piece
                if buffer and len(candidate) > chunk_size:
                    chunks.append(
                        _make_chunk(
                            document, page.page_number, ordinal, buffer,
                            current_section,
                        )
                    )
                    ordinal += 1
                    buffer = _overlap_tail(buffer, overlap)
                    candidate = f"{buffer}\n\n{piece}".strip()
                buffer = candidate
        if buffer:
            chunks.append(
                _make_chunk(
                    document, page.page_number, ordinal, buffer,
                    current_section,
                )
            )
            ordinal += 1
    return chunks


def _normalize_text(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _looks_like_heading(text: str) -> bool:
    return (
        len(text) <= 120
        and "\n" not in text
        and (
            bool(re.match(r"^\d+(?:\.\d+)*[.)]?\s+", text))
            # A prose introduction ending in ':' belongs to its current section.
            # Unnumbered headings still require the existing uppercase signal.
            or (len(text.split()) <= 10 and text.upper() == text)
        )
    )


def _split_long_text(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    pieces: list[str] = []
    remaining = text
    while len(remaining) > size:
        split_at = max(
            remaining.rfind(". ", 0, size),
            remaining.rfind("; ", 0, size),
            remaining.rfind(" ", 0, size),
        )
        split_at = split_at + 1 if split_at >= size // 2 else size
        pieces.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def _overlap_tail(text: str, overlap: int) -> str:
    if overlap == 0:
        return ""
    tail = text[-overlap:]
    first_space = tail.find(" ")
    return tail[first_space + 1 :] if first_space >= 0 else tail


def _make_chunk(
    document: SourceDocument,
    page: int,
    ordinal: int,
    text: str,
    section: str | None,
) -> DocumentChunk:
    identity = (
        f"{document.document_id}:{CHUNKER_VERSION}:{ordinal}:{text}"
    ).encode("utf-8")
    chunk_id = f"{document.document_id}:{hashlib.sha256(identity).hexdigest()[:12]}"
    return DocumentChunk(
        chunk_id=chunk_id,
        document_id=document.document_id,
        document_name=document.name,
        section=section,
        page_start=page,
        page_end=page,
        ordinal=ordinal,
        text=text,
    )
