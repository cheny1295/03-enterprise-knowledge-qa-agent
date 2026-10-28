"""最小 example 的一组聚焦断言，不替代真实模型和后端验收。"""

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "minimal"))

from run import WORKSPACE, load_repository, run_all
from knowledge_agent.configuration import load_identities
from knowledge_agent.knowledge import KnowledgeRepository, load_documents
from knowledge_agent.models import OfflineModel
from knowledge_agent.service import ApplicationService


class MinimalExampleTests(unittest.TestCase):
    def test_six_required_scenarios_write_matching_material(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = run_all(root)
            self.assertEqual(
                [item["case"] for item in summary],
                [
                    "01-normal",
                    "02-conflict",
                    "03-outside",
                    "04-paraphrase",
                    "05-history",
                    "06-tool-failure",
                ],
            )
            self.assertEqual(
                [item["status"] for item in summary],
                [
                    "completed",
                    "needs_clarification",
                    "insufficient_evidence",
                    "completed",
                    "completed",
                    "execution_error",
                ],
            )
            for item in summary:
                output_path = root / item["output"]
                trace_path = root / item["trace"]
                self.assertTrue(output_path.is_file())
                self.assertTrue(trace_path.is_file())
                output = json.loads(output_path.read_text(encoding="utf-8"))
                self.assertEqual(output["output"]["status"], item["status"])
                events = [
                    json.loads(line)
                    for line in trace_path.read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual(
                    {event["request_id"] for event in events},
                    {output["output"]["request_id"]},
                )
                self.assertEqual(events[-1]["status"], item["status"])
                self.assertLessEqual(events[-1]["model_steps_used"], 6)
                self.assertLessEqual(events[-1]["tool_calls_used"], 4)
                decisions = [e for e in events if e["event_type"] == "decision"]
                self.assertEqual(decisions[0]["action_type"], "search_documents")
                self.assertEqual(decisions[-1]["action_type"], "finish")
                self.assertTrue(decisions[-1]["input_observation_ids"])
                self.assertTrue(decisions[-1]["decision_basis_summary"])
                for citation in output["output"]["citations"]:
                    self.assertIn(citation["quote"], {
                        section.text
                        for document in load_documents(
                            WORKSPACE / "data" / (
                                "single-document.jsonl" if output["fixture"] == "single"
                                else "documents.jsonl"
                            )
                        )
                        if document.tenant_id == output["tenant"]
                        and document.document_id == citation["document_id"]
                        and document.version_id == citation["version_id"]
                        for section in document.sections
                        if section.section_id == citation["section_id"]
                    })

            normal = json.loads(
                (root / "01-normal" / "output.json").read_text(encoding="utf-8")
            )["output"]
            self.assertIn("30天", normal["answer"])
            conflict = json.loads(
                (root / "02-conflict" / "output.json").read_text(encoding="utf-8")
            )["output"]
            self.assertEqual(conflict["status"], "needs_clarification")
            self.assertEqual(
                {citation["document_id"] for citation in conflict["citations"]},
                {"D01", "D02"},
            )
            self.assertTrue(conflict["follow_up_question"])
            self.assertTrue(conflict["gaps"])
            self.assertEqual(
                {citation["quote"] for citation in conflict["citations"]},
                {"差旅报销应在费用发生后30天内提交。", "差旅报销应在费用发生后15天内提交。"},
            )
            outside = json.loads(
                (root / "03-outside" / "output.json").read_text(encoding="utf-8")
            )["output"]
            self.assertEqual(outside["citations"], [])
            self.assertTrue(outside["gaps"])
            for name in ("04-paraphrase", "05-history"):
                output = json.loads(
                    (root / name / "output.json").read_text(encoding="utf-8")
                )["output"]
                self.assertEqual(output["answer"], normal["answer"])
            failure = json.loads(
                (root / "06-tool-failure" / "output.json").read_text(encoding="utf-8")
            )["output"]
            self.assertEqual(failure["error_code"], "source_unavailable")

    def test_replacing_source_changes_answer_and_citation(self):
        document = load_documents(WORKSPACE / "data" / "single-document.jsonl")[0]
        changed = replace(
            document,
            sections=(replace(
                document.sections[0], text="差旅报销应在费用发生后45天内提交。",
            ),),
            keywords=("报销", "期限", "45天"),
        )
        identities = load_identities(WORKSPACE / "config" / "demo-identities.json")
        result, _ = ApplicationService(
            identities, KnowledgeRepository([changed]), OfflineModel(),
        ).ask({"question": "报销期限多久", "mode": "mock"}, "demo-user", "tenant-A")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer, changed.sections[0].text)
        self.assertEqual(result.citations[0]["quote"], changed.sections[0].text)
        self.assertNotIn("30天", result.answer)

    def test_exhausted_model_or_tool_budget_returns_incomplete(self):
        identities = load_identities(WORKSPACE / "config" / "demo-identities.json")
        for limits in ({"max_steps": 2}, {"max_tool_calls": 1}):
            with self.subTest(limits=limits):
                result, events = ApplicationService(
                    identities, load_repository("single"), OfflineModel(), **limits,
                ).ask({"question": "报销期限多久", "mode": "mock"}, "demo-user", "tenant-A")
                self.assertEqual(result.status, "incomplete")
                self.assertEqual(result.stop_reason, "budget_exhausted")
                self.assertTrue(result.gaps)
                self.assertEqual(events[-1]["tool_calls_used"], 1)
                self.assertLessEqual(
                    events[-1]["model_steps_used"], limits.get("max_steps", 6),
                )


if __name__ == "__main__":
    stream = StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MinimalExampleTests)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = "Offline Mock only; no external LLM, CUDA or Qdrant.\n\n" + stream.getvalue()
    (WORKSPACE / "examples" / "minimal" / "test-results.txt").write_text(
        report, encoding="utf-8",
    )
    print(report, end="")
    raise SystemExit(0 if result.wasSuccessful() else 1)
