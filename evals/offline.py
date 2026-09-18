"""Real local retrieval with fixture embeddings; never contacts a model server."""
from pathlib import Path
from tempfile import TemporaryDirectory
from rag_service import RAGService
from vector_store import LocalVectorStore
from supervisor import Supervisor


class FixtureEmbeddings:
    model = 'evaluation-fixture-v1'
    def embed_documents(self, texts):
        return [[1., 1.] for _ in texts]
    def embed_query(self, text):
        return [1., 1.]


class OfflineEnvironment:
    def __enter__(self):
        self.directory = TemporaryDirectory()
        self.stores = {}
        for fixture in ('contract', 'injection', 'multi_contract'):
            store = LocalVectorStore(Path(self.directory.name)/fixture, FixtureEmbeddings.model)
            service = RAGService(FixtureEmbeddings(), store)
            base_fixture = 'contract' if fixture == 'multi_contract' else fixture
            content = (Path(__file__).parent/'fixtures'/f'{base_fixture}.txt').read_bytes()
            files = [('contrato_hospital_costa_sur.txt', content)]
            if fixture == 'multi_contract':
                second = (Path(__file__).parent/'fixtures'/'industrias_source.txt')
                if second.exists():
                    files.append(('contrato_industrias_mediterraneo_2026.txt', second.read_bytes()))
            service.ingest(files)
            self.stores[fixture] = store
        return self

    def supervisor(self, case, recorder):
        if case.fixture == 'multi_contract':
            service = RAGService(FixtureEmbeddings(), self.stores[case.fixture], recorder)
            # The offline fixture harness supplies the complete deterministic
            # evidence set; production retrieval configuration is unchanged.
            service.retrieve = lambda question, top_k=None: RAGService.retrieve(service, question, top_k=100)
            return Supervisor(recorder=recorder, rag_factory=lambda r: service)
        return Supervisor(recorder=recorder,
            rag_factory=lambda r: RAGService(FixtureEmbeddings(), self.stores[case.fixture], r))

    def __exit__(self, *args):
        self.directory.cleanup()
