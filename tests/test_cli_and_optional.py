import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_agent.embedding import CudaEmbedding
from knowledge_agent.contracts import AgentError
from knowledge_agent.cli import main


class CliAndOptionalTests(unittest.TestCase):
    def test_cli_writes_json_for_offline_greeting(self):
        with tempfile.TemporaryDirectory() as directory:
            from io import StringIO
            from contextlib import redirect_stdout
            output = StringIO()
            with redirect_stdout(output):
                code = main(["--question", "你好", "--trace-root", directory])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["answer_kind"], "nonfactual")
            self.assertTrue(list(Path(directory).rglob("*.jsonl")))

    def test_real_embedding_requires_fixed_revision_before_loading(self):
        with self.assertRaises(AgentError) as raised:
            CudaEmbedding("fixture-v1")
        self.assertEqual(raised.exception.code, "index_version_mismatch")

    def test_cli_cannot_grant_membership_from_tenant_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            from io import StringIO
            from contextlib import redirect_stdout
            output = StringIO()
            with redirect_stdout(output):
                code = main(["--question", "报销期限多久", "--tenant", "tenant-B",
                             "--data", "must-not-load.jsonl", "--trace-root", directory])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "invalid_request")
            self.assertEqual(result["error_code"], "access_denied")
            self.assertIsNone(result["tenant_id"])

    def test_cli_loads_faq_configuration_for_conflict_path(self):
        with tempfile.TemporaryDirectory() as directory:
            from io import StringIO
            from contextlib import redirect_stdout
            output = StringIO()
            with redirect_stdout(output):
                code = main([
                    "--question", "报销期限多久",
                    "--data", str(Path(__file__).parents[1] / "data" / "documents.jsonl"),
                    "--faqs", str(Path(__file__).parents[1] / "config" / "demo-faqs.json"),
                    "--trace-root", directory,
                ])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "needs_clarification")
            self.assertEqual(len(result["conflict_assessments"]), 1)
            self.assertEqual(
                {citation["version_id"] for citation in result["citations"]},
                {"D01-v1", "D02-v1"},
            )


if __name__ == "__main__":
    unittest.main()
