"""合成资料上的租户检索与证据链局部验证。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_agent.contracts import (
    Document, KnowledgeSnapshot, Section, ToolResult,
)
from knowledge_agent.identity import IdentityRegistry
from knowledge_agent.knowledge import KnowledgeRepository
from knowledge_agent.retrieval import EvidenceService, LexicalSearchService
from knowledge_agent.runtime import AgentState


def docs():
    return [
        Document(
            "tenant-A", "D01", "D01-v1", "2026-03-01", "费用报销管理办法",
            (Section("4.2", "提交期限", "差旅报销应在费用发生后30天内提交。"),),
            scope=("差旅报销",), keywords=("报销", "期限", "30天"),
        ),
        Document(
            "tenant-A", "D02", "D02-v1", "2026-04-01", "费用报销补充规定",
            (Section("2.1", "提交期限", "差旅报销应在费用发生后15天内提交。"),),
            scope=("差旅报销",), keywords=("报销", "期限", "15天"),
        ),
        Document(
            "tenant-B", "D01", "D01-v1", "2026-03-01", "B租户制度",
            (Section("1", "期限", "B租户报销应在60天内提交。"),),
            scope=("差旅报销",), keywords=("报销", "期限", "60天"),
        ),
    ]


def state_for(tenant_id):
    registry = IdentityRegistry({
        "alice": {"tenant-A": ("knowledge_reader",)},
        "bob": {"tenant-B": ("knowledge_reader",)},
    })
    principal = "alice" if tenant_id == "tenant-A" else "bob"
    context = registry.resolve_identity(principal, tenant_id)
    state = AgentState("req-1", context, "报销期限多久")
    repository = KnowledgeRepository(docs())
    state.bind_snapshot(repository.load_snapshot(context))
    return state, repository


class KnowledgePathTests(unittest.TestCase):
    def test_search_is_tenant_scoped_even_when_document_ids_repeat(self):
        state, repository = state_for("tenant-A")
        result = LexicalSearchService(repository).search(
            state, "报销期限", {"scope_any": ["差旅报销"]},
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual({c.tenant_id for c in result.data["candidates"]},
                         {"tenant-A"})
        self.assertEqual({c.document_id for c in result.data["candidates"]},
                         {"D01", "D02"})

    def test_search_then_read_creates_evidence_not_from_snippet(self):
        state, repository = state_for("tenant-A")
        search = LexicalSearchService(repository).search(state, "报销期限")
        candidate = search.data["candidates"][0]
        read = EvidenceService(repository).read(state, candidate.candidate_id)
        self.assertEqual(read.status, "ok")
        self.assertEqual(len(read.evidence), 1)
        evidence = read.evidence[0]
        self.assertEqual(evidence.source_candidate_id, candidate.candidate_id)
        self.assertEqual(evidence.quote, repository.get_section(
            state.tenant_context.tenant_id, evidence.version_id, evidence.section_id,
        ).text)

    def test_unknown_candidate_does_not_fallback_to_document_lookup(self):
        state, repository = state_for("tenant-A")
        read = EvidenceService(repository).read(state, "C-not-searched")
        self.assertEqual(read.status, "not_found")
        self.assertEqual(read.evidence, ())

    def test_conflict_assessment_keeps_both_read_evidence(self):
        state, repository = state_for("tenant-A")
        service = LexicalSearchService(repository)
        search = service.search(state, "报销期限")
        evidence_service = EvidenceService(repository)
        for item in search.data["candidates"]:
            evidence_service.read(state, item.candidate_id)
        assessments = evidence_service.assess_conflicts(state)
        self.assertEqual(len(assessments), 1)
        assessment = assessments[0]
        self.assertEqual(assessment.relation, "unresolved_conflict")
        self.assertEqual(len(assessment.candidate_ids), 2)
        self.assertEqual(len(assessment.evidence_ids), 2)


if __name__ == "__main__":
    unittest.main()
