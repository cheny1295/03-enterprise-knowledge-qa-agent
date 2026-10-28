"""只覆盖路由/结果的关键局部分支，不是全部验收矩阵。"""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_agent.contracts import AgentError, CandidateResult, FaqEntry
from knowledge_agent.knowledge import KnowledgeRepository
from knowledge_agent.models import OfflineModel
from knowledge_agent.results import ResultAssembler
from knowledge_agent.routing import prepare_route
from knowledge_agent.service import ApplicationService
from knowledge_agent.tools import validate_action
from test_foundation import identity_registry
from test_knowledge_path import docs, state_for


class UnavailableModel(OfflineModel):
    def classify(self, question, history):
        raise AgentError("provider_unavailable", "模型服务暂不可用。")


class RoutingAndResultTests(unittest.TestCase):
    def test_pure_greeting_needs_no_snapshot_and_no_model_step(self):
        service = ApplicationService(identity_registry(), KnowledgeRepository(), OfflineModel())
        result, trace = service.ask({"question": "你好"}, "alice", "tenant-A")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer_kind, "nonfactual")
        self.assertIsNone(result.snapshot_id)
        self.assertEqual(trace[-1]["model_steps_used"], 0)
        self.assertEqual(trace[-1]["tool_calls_used"], 0)

    def test_greeting_with_knowledge_request_does_not_use_template(self):
        state, repository = state_for("tenant-A")
        state.question = "你好，报销期限多久"
        preliminary = prepare_route(state, OfflineModel())
        self.assertIsNone(preliminary)
        self.assertEqual(state.intent_assessment.intent, "knowledge_question")
        self.assertEqual(state.budget.model_steps_used, 1)

    def test_provider_failure_is_not_insufficient_evidence_or_mock_downgrade(self):
        service = ApplicationService(identity_registry(), KnowledgeRepository(), UnavailableModel())
        result, trace = service.ask({"question": "报销期限多久"}, "alice", "tenant-A")
        self.assertEqual(result.status, "execution_error")
        self.assertEqual(result.error_code, "provider_unavailable")
        self.assertEqual(trace[-1]["tool_calls_used"], 0)

    def test_unknown_or_extra_model_action_fields_are_rejected(self):
        with self.assertRaises(AgentError):
            validate_action({"type": "read_document", "candidate_id": "C1",
                             "tenant_id": "tenant-B"})
        with self.assertRaises(AgentError):
            validate_action({"type": "delete_index"})

    def test_candidate_id_cannot_be_cited_as_evidence(self):
        state, repository = state_for("tenant-A")
        candidate = CandidateResult(
            "completed", "报销期限30天",
            [{"text": "报销期限30天", "evidence_ids": ["C-unread"]}],
            stop_reason="finished_with_evidence",
        )
        with self.assertRaises(AgentError) as raised:
            ResultAssembler(repository).assemble(state, candidate)
        self.assertEqual(raised.exception.code, "invalid_result")

    def test_faq_keeps_search_read_loop_and_current_tenant_citations(self):
        document = docs()[0]
        document = replace(document, sections=(
            replace(document.sections[0], faq_topic_ids=("deadline",)),
        ))
        faq = FaqEntry(
            "tenant-A", "", "deadline", "报销期限多久", (), (),
            (("D01", "D01-v1", "4.2"),), True,
        )
        service = ApplicationService(
            identity_registry(), KnowledgeRepository([document], [faq]), OfflineModel(),
        )
        result, trace = service.ask({"question": "报销期限多久"}, "alice", "tenant-A")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.routing.effective_route, "fast_faq")
        self.assertEqual(len(result.citations), 1)
        self.assertEqual(
            [e["action_type"] for e in trace if e["event_type"] == "decision"],
            ["search_documents", "read_document", "finish"],
        )
        self.assertEqual(trace[-1]["model_steps_used"], 4)

    def test_conflict_returns_clarification_with_both_citations(self):
        service = ApplicationService(
            identity_registry(), KnowledgeRepository(docs()), OfflineModel(),
        )
        result, _ = service.ask({"question": "报销期限多久"}, "alice", "tenant-A")
        self.assertEqual(result.status, "needs_clarification")
        self.assertEqual(len(result.citations), 2)
        self.assertEqual(result.conflict_assessments[0].relation, "unresolved_conflict")


if __name__ == "__main__":
    unittest.main()
