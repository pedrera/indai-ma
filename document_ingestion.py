import hashlib
from io import BytesIO
from pathlib import Path

from rag_models import DocumentPage, SourceDocument


SUPPORTED_DOCUMENT_EXTENSIONS = {".txt", ".pdf"}


class DocumentIngestionError(ValueError):
    pass


def parse_document(name: str, content: bytes) -> SourceDocument:
    safe_name = Path(name).name
    extension = Path(safe_name).suffix.lower()
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise DocumentIngestionError(
            f"Formato no soportado para {safe_name}. Usa TXT o PDF."
        )
    if not content:
        raise DocumentIngestionError(f"El documento {safe_name} está vacío.")

    digest = hashlib.sha256(content).hexdigest()
    pages = (
        _parse_txt(content)
        if extension == ".txt"
        else _parse_pdf(content, safe_name)
    )
    if not any(page.text.strip() for page in pages):
        raise DocumentIngestionError(
            f"No se ha podido extraer texto de {safe_name}. "
            "Los PDF escaneados requieren OCR."
        )
    return SourceDocument(
        document_id=digest[:16],
        name=safe_name,
        sha256=digest,
        pages=pages,
    )


def _parse_txt(content: bytes) -> list[DocumentPage]:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return [DocumentPage(1, content.decode(encoding))]
        except UnicodeDecodeError:
            continue
    raise DocumentIngestionError(
        "No se ha podido determinar la codificación del documento TXT."
    )


def _parse_pdf(content: bytes, name: str) -> list[DocumentPage]:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise DocumentIngestionError(
            "La extracción PDF requiere instalar la dependencia pypdf."
        ) from error
    try:
        reader = PdfReader(BytesIO(content))
        return [
            DocumentPage(index, page.extract_text() or "")
            for index, page in enumerate(reader.pages, start=1)
        ]
    except Exception as error:
        raise DocumentIngestionError(
            f"No se ha podido leer el PDF {name}."
        ) from error
