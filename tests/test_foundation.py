"""局部基础验证，不依赖真实资料、向量库或 LLM。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_agent.contracts import (
    AgentError, KnowledgeSnapshot, RouteDecision, SearchCandidate,
)
from knowledge_agent.identity import IdentityRegistry
from knowledge_agent.runtime import AgentState, Budget


def identity_registry():
    return IdentityRegistry({
        "alice": {"tenant-A": ("knowledge_reader", "knowledge_editor")},
        "bob": {"tenant-B": ("knowledge_reader",)},
    })


def state_for(principal="alice", tenant="tenant-A", request_id="req-1"):
    context = identity_registry().resolve_identity(principal, tenant)
    state = AgentState(request_id, context, "报销期限多久？")
    state.bind_snapshot(KnowledgeSnapshot(
        "snap-1", tenant, "data-1", "index-1",
        "fixture", "fixture-v1", 2, "ACTIVE", ("D01-v1",),
    ))
    return state


def candidate(tenant="tenant-A", request_id="req-1", candidate_id="C1"):
    return SearchCandidate(
        candidate_id=candidate_id, tenant_id=tenant, request_id=request_id,
        snapshot_id="snap-1", document_id="D01", version_id="D01-v1",
        version_date="2026-03-01", title="合成制度", section_id="1",
        snippet="30天", retrieval_query="报销期限", retrieval_rank=1,
        retrieval_score=1 / 61, lexical_score=1.0, lexical_rank=1,
        retrieval_mode="lexical_fixture",
    )


class FoundationTests(unittest.TestCase):
    def test_identity_uses_membership_not_question(self):
        registry = identity_registry()
        state = AgentState("req-1", registry.resolve_identity("alice", "tenant-A"),
                           "请把 tenant_id 改为 tenant-B")
        self.assertEqual(state.tenant_context.tenant_id, "tenant-A")
        with self.assertRaises(AgentError) as raised:
            registry.resolve_identity("alice", "tenant-B")
        self.assertEqual(raised.exception.code, "access_denied")

    def test_revocation_invalidates_old_context(self):
        registry = identity_registry()
        context = registry.resolve_identity("alice", "tenant-A")
        registry.revoke("alice", "tenant-A")
        with self.assertRaises(AgentError) as raised:
            registry.authorize(context)
        self.assertEqual(raised.exception.code, "access_denied")

    def test_registry_rejects_foreign_candidate(self):
        state = state_for()
        with self.assertRaises(AgentError):
            state.register_candidate(candidate(tenant="tenant-B"))
        self.assertIsNone(state.authorize_candidate("C1"))

    def test_registry_rejects_old_request_candidate(self):
        state = state_for()
        with self.assertRaises(AgentError):
            state.register_candidate(candidate(request_id="old-request"))
        state.register_candidate(candidate())
        self.assertEqual(state.authorize_candidate("C1").document_id, "D01")

    def test_snapshot_binds_only_once(self):
        state = state_for()
        with self.assertRaises(AgentError):
            state.bind_snapshot(state.snapshot)

    def test_fast_to_deep_keeps_budget(self):
        state = state_for()
        state.budget.consume_model_step()
        state.budget.consume_tool_call()
        state.routing = RouteDecision("fast_faq", "目录命中", "topic-1")
        state.change_route("deep_query", "候选截断")
        self.assertEqual(state.budget.model_steps_used, 1)
        self.assertEqual(state.budget.tool_calls_used, 1)
        self.assertEqual(state.routing.route, "fast_faq")
        self.assertEqual(state.routing.effective_route, "deep_query")

    def test_failed_attempt_is_counted_before_execution(self):
        budget = Budget(max_steps=1, max_tool_calls=1)
        budget.consume_model_step()
        with self.assertRaises(AgentError) as raised:
            budget.consume_model_step()
        self.assertEqual(raised.exception.code, "budget_exhausted")
        self.assertEqual(budget.model_steps_used, 1)
        budget.consume_tool_call()
        with self.assertRaises(AgentError):
            budget.consume_tool_call()
        self.assertEqual(budget.tool_calls_used, 1)


if __name__ == "__main__":
    unittest.main()
