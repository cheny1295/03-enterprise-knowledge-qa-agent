"""只测应用接线；替身向量不证明 Qdrant/CUDA 或检索质量。"""

import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_agent.contracts import AgentError
from knowledge_agent.embedding import KnowledgeChunk, MODEL_ID
from knowledge_agent.identity import IdentityRegistry
from knowledge_agent.ingestion import IngestionService
from knowledge_agent.knowledge import KnowledgeRepository, load_documents
from knowledge_agent.models import OfflineModel
from knowledge_agent.service import ApplicationService
from knowledge_agent.vector_store import HybridSearchService


class StubEmbedding:
    model_id = MODEL_ID
    revision = "a" * 40
    dimension = 512

    def chunks(self, documents, snapshot_id):
        return [
            KnowledgeChunk(
                str(uuid4()), doc.tenant_id, snapshot_id, doc.document_id,
                doc.version_id, section.section_id, uuid4().hex, 0, len(section.text),
                section.text,
                {"tenant_id": doc.tenant_id, "snapshot_id": snapshot_id,
                 "document_id": doc.document_id, "version_id": doc.version_id,
                 "section_id": section.section_id},
            )
            for doc in documents for section in doc.sections
        ]

    def embed_documents(self, texts):
        return [[1.0] + [0.0] * 511 for text in texts]

    def embed_query(self, query):
        return [1.0] + [0.0] * 511


class StubVectorStore:
    def __init__(self):
        self.chunks = []
        self.last_query = None

    def upsert(self, chunks, vectors):
        self.chunks.extend(chunks)

    def count(self, tenant_id, snapshot_id):
        return sum(c.tenant_id == tenant_id and c.snapshot_id == snapshot_id
                   for c in self.chunks)

    def query(self, tenant_id, snapshot_id, vector, filters, *, revision, index_version):
        self.last_query = tenant_id, snapshot_id, filters, revision, index_version
        return [
            ((c.document_id, c.version_id, c.section_id), 1.0)
            for c in self.chunks
            if c.tenant_id == tenant_id and c.snapshot_id == snapshot_id
        ], False


class HybridWiringTests(unittest.TestCase):
    def setUp(self):
        self.identities = IdentityRegistry({
            "editor": {"tenant-A": ("knowledge_reader", "knowledge_editor")},
        })
        self.context = self.identities.resolve_identity("editor", "tenant-A")
        self.documents = load_documents(
            Path(__file__).resolve().parents[1] / "data" / "single-document.jsonl",
        )

    def test_ready_publish_reload_and_hybrid_request_with_stubs(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = KnowledgeRepository()
            store = StubVectorStore()
            embedding = StubEmbedding()
            ingestion = IngestionService(
                directory, self.identities, repository, embedding, store,
            )
            build = ingestion.ingest(self.context, self.documents)
            self.assertEqual(build["publication_state"], "READY")
            ingestion.publish(self.context, build["snapshot_id"])
            reloaded = KnowledgeRepository()
            IngestionService(
                directory, self.identities, reloaded, embedding, store,
            ).load_active(self.context)
            result, trace = ApplicationService(
                self.identities, reloaded, OfflineModel(),
                search_service=HybridSearchService(reloaded, embedding, store),
            ).ask({"question": "报销期限多久"}, "editor", "tenant-A")
            self.assertEqual(result.status, "completed")
            self.assertEqual(store.last_query[:2], ("tenant-A", build["snapshot_id"]))
            self.assertEqual(store.last_query[2], {"status": ["active"]})
            self.assertEqual(trace[-1]["embedding_calls_used"], 1)

    def test_failed_build_does_not_replace_active_snapshot(self):
        class BrokenEmbedding(StubEmbedding):
            def embed_documents(self, texts):
                raise AgentError("embedding_unavailable", "替身编码失败。")

        with tempfile.TemporaryDirectory() as directory:
            repository = KnowledgeRepository(self.documents)
            previous = repository.load_snapshot(self.context).snapshot_id
            ingestion = IngestionService(
                directory, self.identities, repository, BrokenEmbedding(), StubVectorStore(),
            )
            build = ingestion.ingest(self.context, self.documents)
            self.assertEqual(build["publication_state"], "FAILED")
            self.assertEqual(build["error_code"], "embedding_unavailable")
            self.assertEqual(repository.load_snapshot(self.context).snapshot_id, previous)


if __name__ == "__main__":
    unittest.main()
