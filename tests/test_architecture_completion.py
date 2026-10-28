"""本轮核心缺口的局部验证；不连接 CUDA、Qdrant 或外部模型。"""

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_agent.cli import main
from knowledge_agent.contracts import AgentError, CandidateResult
from knowledge_agent.ingestion import IngestionService, read_json, tenant_folder
from knowledge_agent.knowledge import KnowledgeRepository
from knowledge_agent.models import OfflineModel
from knowledge_agent.results import ResultAssembler
from knowledge_agent.retrieval import EvidenceService, LexicalSearchService, tool_result
from knowledge_agent.service import ApplicationService
from knowledge_agent.tools import ToolRouter
from test_foundation import identity_registry
from test_hybrid_wiring import StubEmbedding, StubVectorStore
from test_knowledge_path import docs, state_for


class CompletionTests(unittest.TestCase):
    def service(self, documents=None):
        return ApplicationService(
            identity_registry(), KnowledgeRepository(documents or docs()), OfflineModel(),
        )

    def test_explicit_filters_are_applied_and_recorded(self):
        result, trace = self.service().ask({
            "question": "报销期限多久", "filters": {"document_ids": [" D01 "]},
        }, "alice", "tenant-A")
        self.assertEqual(result.status, "completed")
        self.assertEqual({c["document_id"] for c in result.citations}, {"D01"})
        event = next(e for e in trace if e["event_type"] == "filters_confirmed")
        self.assertEqual(event["filter_sources"]["document_ids"], "request.filters")
        self.assertEqual(event["confirmed_filters"]["document_ids"], ["D01"])

    def test_history_resolves_short_followup_but_assistant_answer_is_not_evidence(self):
        result, _ = self.service([docs()[0]]).ask({
            "question": "多久",
            "conversation_history": [
                {"role": "user", "content": "差旅报销申请期限"},
                {"role": "assistant", "content": "必须在999天内提交。"},
            ],
        }, "alice", "tenant-A")
        self.assertEqual(result.status, "completed")
        self.assertIn("30天", result.answer)
        self.assertNotIn("999", result.answer)
        missing, _ = self.service().ask({"question": "多久"}, "alice", "tenant-A")
        self.assertEqual(missing.status, "needs_clarification")
        self.assertIsNone(missing.snapshot_id)

    def test_historical_filters_need_explicit_date_and_use_closed_interval(self):
        historical = replace(docs()[0], status="withdrawn",
                             effective_from="2026-03-01", effective_to="2026-03-31")
        for filters, historical_flag in [
            ({"status": ["withdrawn"]}, False),
            ({"status": ["withdrawn"]}, True),
            ({"version_date_from": "2026-04-01", "version_date_to": "2026-03-01"}, False),
            ({"scope_any": "差旅报销"}, False),
        ]:
            with self.subTest(filters=filters):
                rejected, _ = self.service([historical]).ask({
                    "question": "报销期限", "filters": filters, "historical": historical_flag,
                }, "alice", "tenant-A")
                self.assertEqual(rejected.status, "invalid_request")
        accepted, _ = self.service([historical]).ask({
            "question": "历史报销期限", "historical": True,
            "filters": {"status": ["withdrawn"], "effective_on": "2026-03-31"},
        }, "alice", "tenant-A")
        self.assertEqual(accepted.status, "completed")

    def test_explicit_source_relations_choose_winner_but_keep_both_citations(self):
        left, right = docs()[:2]
        for newer in (replace(right, supersedes=(left.version_id,)),
                      replace(right, priority=2)):
            older = replace(left, priority=1) if newer.priority else left
            with self.subTest(relation=newer.supersedes or "priority"):
                result, _ = self.service([older, newer]).ask(
                    {"question": "报销期限多久"}, "alice", "tenant-A",
                )
                self.assertEqual(result.status, "completed")
                self.assertIn("15天", result.answer)
                self.assertNotIn("30天", result.answer)
                self.assertEqual(len(result.citations), 2)
                self.assertEqual(len(result.claims), 1)
        # 仅版本日期更新不构成覆盖关系。
        unresolved, _ = self.service().ask({"question": "报销期限多久"}, "alice", "tenant-A")
        self.assertEqual(unresolved.status, "needs_clarification")

    def test_completed_requires_all_recalled_sources_read_and_no_truncation(self):
        state, repository = state_for("tenant-A")
        search = LexicalSearchService(repository).search(state, "报销期限")
        state.search_coverage = search.data["coverage"]
        evidence = EvidenceService(repository).read(
            state, search.data["candidates"][0].candidate_id,
        ).evidence[0]
        candidate = CandidateResult(
            "completed", evidence.quote,
            [{"text": evidence.quote, "evidence_ids": [evidence.evidence_id]}],
            stop_reason="finished_with_evidence",
        )
        with self.assertRaises(AgentError):
            ResultAssembler(repository).assemble(state, candidate)
        single = [replace(docs()[0], document_id=f"D{i}", version_id=f"V{i}")
                  for i in range(11)]
        result, trace = self.service(single).ask(
            {"question": "报销期限"}, "alice", "tenant-A",
        )
        coverage = [e["result_summary"]["coverage"] for e in trace
                    if e["event_type"] == "tool_result"
                    and e["tool_name"] == "search_documents"][0]
        self.assertTrue(coverage["candidate_truncated"])
        self.assertNotEqual(result.status, "completed")

    def test_unrelated_success_does_not_recover_failed_read(self):
        state, repository = state_for("tenant-A")
        router = ToolRouter(LexicalSearchService(repository), EvidenceService(repository))
        router.execute(state, {"type": "search_documents", "query": "报销期限"})
        router.execute(state, {"type": "read_document", "candidate_id": "C-unknown"})
        router.execute(state, {"type": "search_documents", "query": "报销期限"})
        action = OfflineModel().decide(state)
        self.assertEqual(action["status"], "execution_error")
        # 同一来源重新召回的新 candidate_id 可以恢复旧 Read；未知 ID 不能借此恢复。
        class FlakyRead(EvidenceService):
            failed = False

            def read(self, state, candidate_id):
                if not self.failed:
                    self.failed = True
                    return tool_result(state, "read_document", "error", {}, error={
                        "code": "timeout", "message": "替身读取超时。", "retryable": True,
                    })
                return super().read(state, candidate_id)

        retry_state, repository = state_for("tenant-A")
        retry_router = ToolRouter(LexicalSearchService(repository), FlakyRead(repository))
        first = retry_router.execute(
            retry_state, {"type": "search_documents", "query": "报销期限"},
        ).data["candidates"][0]
        retry_router.execute(retry_state, {
            "type": "read_document", "candidate_id": first.candidate_id,
        })
        refreshed = retry_router.execute(
            retry_state, {"type": "search_documents", "query": "报销期限"},
        ).data["candidates"][0]
        retry_router.execute(retry_state, {
            "type": "read_document", "candidate_id": refreshed.candidate_id,
        })
        self.assertEqual(retry_state.unrecovered_errors, {})

    def test_erase_blocks_old_publish_and_can_resume_cleanup_after_restart(self):
        from knowledge_agent.identity import IdentityRegistry

        class Store(StubVectorStore):
            fail_delete = True

            def delete_snapshot(self, tenant_id, snapshot_id):
                if self.fail_delete:
                    raise AgentError("vector_store_error", "局部替身清理失败。")
                self.chunks = [c for c in self.chunks
                               if (c.tenant_id, c.snapshot_id) != (tenant_id, snapshot_id)]

        identities = IdentityRegistry({
            "editor": {"tenant-A": ("knowledge_reader", "knowledge_editor")},
        })
        context = identities.resolve_identity("editor", "tenant-A")
        with tempfile.TemporaryDirectory() as directory:
            store = Store()
            repository = KnowledgeRepository()
            service = IngestionService(directory, identities, repository, StubEmbedding(), store)
            build = service.ingest(context, docs()[:2])
            service.publish(context, build["snapshot_id"])
            ready = service.ingest(context, docs()[:2])
            # 内存安装拒绝时不得先切持久 ACTIVE 引用。
            with patch.object(repository, "install", side_effect=AgentError(
                    "index_version_mismatch", "替身安装拒绝。")):
                with self.assertRaises(AgentError):
                    service.publish(context, ready["snapshot_id"])
            folder = tenant_folder(Path(directory) / "knowledge", "tenant-A")
            self.assertEqual(
                read_json(folder / "active.json")["snapshot_id"], build["snapshot_id"],
            )
            with self.assertRaises(AgentError):
                service.delete(context, "D01", erase=True)
            context = identities.resolve_identity("editor", "tenant-A")
            with self.assertRaises(AgentError):
                service.publish(context, build["snapshot_id"])
            store.fail_delete = False
            restarted = IngestionService(
                directory, identities, KnowledgeRepository(), StubEmbedding(), store,
            )
            result = restarted.delete(context, "D01", erase=True)
            self.assertIsNone(result["error_code"])
            restarted.load_active(context)
            active = restarted.repository.load_snapshot(context)
            self.assertEqual(
                {d.document_id for d in restarted.repository.documents("tenant-A", active.snapshot_id)},
                {"D02"},
            )
            folder = tenant_folder(Path(directory) / "knowledge", "tenant-A")
            self.assertFalse((folder / "erase.json").exists())
            self.assertFalse((folder / (build["snapshot_id"] + ".json")).exists())
            self.assertIn(build["snapshot_id"], read_json(folder / "blocked.json"))

    def test_cli_startup_failure_uses_validated_result_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            output = StringIO()
            with patch.dict("os.environ", {
                "AGENT_LLM_BASE_URL": "", "AGENT_LLM_MODEL": "", "AGENT_LLM_API_KEY": "",
            }), redirect_stdout(output):
                code = main(["--question", "报销期限", "--mode", "model",
                             "--trace-root", directory])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "execution_error")
            self.assertEqual(result["error_code"], "provider_unavailable")
            self.assertIsNone(result["tenant_id"])
            self.assertEqual(result["citations"], [])


if __name__ == "__main__":
    unittest.main()
