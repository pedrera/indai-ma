import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from chunking import chunk_document
from diagnostics import PerformanceRecorder
from document_ingestion import DocumentIngestionError, parse_document
from embeddings import EmbeddingProvider
from rag_models import DocumentChunk, DocumentMetadata, IndexedChunk
from rag_service import RAGService, build_rag_messages, sanitize_rag_citations
from vector_store import LocalVectorStore


class FakeEmbeddings(EmbeddingProvider):
    model = "fake-embedding"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    @staticmethod
    def _embed(text: str) -> list[float]:
        lowered = text.lower()
        return [
            float(lowered.count("flexibilidad")),
            float(lowered.count("precio")),
            1.0,
        ]


class DocumentAndChunkingTests(unittest.TestCase):
    def test_txt_document_is_parsed_and_chunked_with_traceability(self) -> None:
        content = (
            "FLEXIBILIDAD DE VOLUMEN\n\nEl contrato permite una flexibilidad "
            "del 15 por ciento para el Hospital Central."
        ).encode("utf-8")
        document = parse_document("../Contrato Hospital.txt", content)
        chunks = chunk_document(document, chunk_size=80, overlap=10)
        self.assertEqual(document.name, "Contrato Hospital.txt")
        self.assertTrue(chunks)
        self.assertTrue(all(item.document_id == document.document_id for item in chunks))
        self.assertTrue(all(item.page_start == 1 for item in chunks))
        self.assertTrue(all(item.chunk_id for item in chunks))

    def test_unsupported_document_is_rejected(self) -> None:
        with self.assertRaises(DocumentIngestionError):
            parse_document("contrato.docx", b"contenido")

    def test_pdf_without_text_reports_ocr_limitation(self) -> None:
        from pypdf import PdfWriter

        output = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(output)
        with self.assertRaisesRegex(DocumentIngestionError, "OCR"):
            parse_document("escaneado.pdf", output.getvalue())


class VectorStoreTests(unittest.TestCase):
    def test_cosine_search_returns_most_similar_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalVectorStore(directory, "fake-embedding")
            first = _chunk("a", "Flexibilidad del quince por ciento")
            second = _chunk("b", "Política de precio fijo")
            store.add(
                [
                    IndexedChunk(first, [1.0, 0.0, 0.0]),
                    IndexedChunk(second, [0.0, 1.0, 0.0]),
                ],
                [
                    DocumentMetadata("doc-a", "a.txt", "hash-a", 1, 1),
                    DocumentMetadata("doc-b", "b.txt", "hash-b", 1, 1),
                ],
            )
            matches = store.search([1.0, 0.0, 0.0], top_k=1)
            self.assertEqual(matches[0].chunk.chunk_id, "a")

            reloaded = LocalVectorStore(directory, "fake-embedding")
            self.assertEqual(reloaded.chunk_count, 2)
            self.assertEqual(reloaded.document_count, 2)
            self.assertTrue(reloaded.has_document_hash("hash-a"))
            self.assertTrue((Path(directory) / "manifest.json").exists())
            self.assertTrue((Path(directory) / "documents.json").exists())
            self.assertTrue((Path(directory) / "chunks.jsonl").exists())

            reloaded.clear()
            empty_after_restart = LocalVectorStore(
                directory, "fake-embedding"
            )
            self.assertEqual(empty_after_restart.document_count, 0)
            self.assertEqual(empty_after_restart.chunk_count, 0)


class RAGServiceTests(unittest.TestCase):
    def test_persisted_hospital_index_survives_restart_and_resolves_gas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = "fake-embedding"
            first_store = LocalVectorStore(directory, model)
            service = RAGService(FakeEmbeddings(), first_store)
            service.ingest(
                [
                    (
                        "contrato_hospital_costa_sur.txt",
                        (
                            "CONTRATO MARCO DE SUMINISTRO DE GAS NATURAL\n\n"
                            "El volumen anual contratado de gas natural es de "
                            "48 GWh. La flexibilidad contractual es del 15 %."
                        ).encode("utf-8"),
                    )
                ]
            )
            self.assertTrue((Path(directory) / "manifest.json").exists())
            self.assertTrue((Path(directory) / "documents.json").exists())
            self.assertTrue((Path(directory) / "chunks.jsonl").exists())
            self.assertGreater(first_store.chunk_count, 0)

            restarted_store = LocalVectorStore(directory, model)
            self.assertEqual(restarted_store.document_count, 1)
            self.assertGreater(restarted_store.chunk_count, 0)
            restarted_service = RAGService(
                FakeEmbeddings(), restarted_store
            )
            retrieval = restarted_service.retrieve(
                "El Hospital Costa Sur prevé consumir 4,8 GWh y tenemos "
                "4,3 GWh aprovisionados. Analiza su contrato.",
                top_k=1,
            )
            self.assertEqual(
                retrieval.gas_type_resolution.gas_type, "natural_gas"
            )
            self.assertEqual(
                retrieval.gas_type_resolution.source, "rag"
            )

    def test_ingest_retrieve_and_build_safe_traceable_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalVectorStore(directory, "fake-embedding")
            recorder = PerformanceRecorder(
                "rag-test", "lmstudio", "fake-embedding", "rag_index"
            )
            service = RAGService(FakeEmbeddings(), store, recorder)
            result = service.ingest(
                [
                    (
                        "Contrato Hospital Central.txt",
                        b"FLEXIBILIDAD\n\nFlexibilidad de volumen del 15 por ciento.",
                    )
                ]
            )
            self.assertEqual(result.document_count, 1)
            self.assertGreater(result.chunk_count, 0)
            duplicate = service.ingest(
                [
                    (
                        "Copia.txt",
                        b"FLEXIBILIDAD\n\nFlexibilidad de volumen del 15 por ciento.",
                    )
                ]
            )
            self.assertEqual(duplicate.document_count, 0)
            self.assertEqual(duplicate.duplicate_count, 1)

            retrieval = service.retrieve("flexibilidad", top_k=1)
            self.assertEqual(len(retrieval.matches), 1)
            messages = build_rag_messages(
                [{"role": "user", "content": "¿Qué permite el contrato?"}],
                retrieval,
            )
            chunk_id = retrieval.matches[0].chunk.chunk_id
            self.assertIn(chunk_id, messages[0]["content"])
            self.assertIn("datos no confiables", messages[0]["content"])
            stages = {event.stage for event in recorder.snapshot().events}
            self.assertTrue(
                {
                    "document_parsing",
                    "chunking",
                    "embedding",
                    "index_persistence",
                    "query_embedding",
                    "vector_search",
                    "retrieved_context",
                }.issubset(stages)
            )
            valid = f"Dato [{chunk_id}]"
            self.assertEqual(
                sanitize_rag_citations(valid, [retrieval.matches[0].source_dict()]),
                valid,
            )
            self.assertIn(
                "fuente no verificada",
                sanitize_rag_citations(
                    "Dato [0123456789abcdef:0123456789ab]", []
                ),
            )


def _chunk(chunk_id: str, text: str) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=chunk_id,
        document_id=f"doc-{chunk_id}",
        document_name=f"{chunk_id}.txt",
        section=None,
        page_start=1,
        page_end=1,
        ordinal=0,
        text=text,
    )


if __name__ == "__main__":
    unittest.main()
