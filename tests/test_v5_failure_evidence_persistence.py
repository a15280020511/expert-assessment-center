from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_constitutional_runtime import ConstitutionalExecutionEngine  # noqa: E402
from v5_runtime import ExecutionEngine  # noqa: E402


class FailureEvidencePersistenceTests(unittest.TestCase):
    def test_runtime_failure_still_persists_constitutional_evidence(self) -> None:
        task = "仅依据题面，不得编造。北门外地面干燥。"
        report = "事实：北门外地面干燥。"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "v5-node-results.json").write_text(
                json.dumps(
                    [
                        {
                            "node_id": "node-a",
                            "status": "failed",
                            "selected_model": "openai/model-a",
                            "attempts": [
                                {
                                    "attempt_kind": "initial",
                                    "status": "call_failed",
                                    "model": "openai/model-a",
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (root / "v5-execution-summary.json").write_text(
                json.dumps({"final_answer": report}),
                encoding="utf-8",
            )
            engine = ConstitutionalExecutionEngine.__new__(
                ConstitutionalExecutionEngine
            )
            with patch.object(
                ExecutionEngine,
                "execute_graph",
                side_effect=RuntimeError("coverage gate failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "coverage gate failed"):
                    engine.execute_graph(
                        original_task=task,
                        output_dir=root,
                    )

            evidence = json.loads(
                (root / "evidence-integrity.json").read_text(encoding="utf-8")
            )
            company = json.loads(
                (root / "actual-model-company-audit.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(evidence["status"], "PASS")
            self.assertTrue(evidence["written_after_execution_failure"])
            self.assertEqual(company["status"], "PASS")
            self.assertTrue(company["failed_calls_are_included"])
            self.assertEqual(len(company["all_called_models"]), 1)


if __name__ == "__main__":
    unittest.main()
