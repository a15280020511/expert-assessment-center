import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_execution_auditor as auditor


class V5ProductionCutoverTests(unittest.TestCase):
    def _write(self, root: Path, name: str, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str):
            path.write_text(value, encoding="utf-8")
        else:
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def _fixture(self, root: Path):
        report = "# V5生产报告\n\n" + ("完整结论、约束、风险、实施方案和否决条件。" * 20)
        self._write(root, "ticket-status.json", {"accepted": True, "task_id": "task-v5-production"})
        self._write(root, "production-runtime.json", {
            "fallback_policy": "fail-closed-no-v3-fallback",
            "v3_preserved_for_manual_rollback": True,
        })
        self._write(root, "expert-team-result.json", {
            "runtime_version": "v5-r8",
            "status": "success",
            "completion_mode": "complete",
            "final_answer": report,
            "executor": "v5-r8-fault-aware",
            "v3_fallback_used": False,
        })
        self._write(root, "v5-execution-summary.json", {
            "status": "success",
            "completion_mode": "complete",
            "executor": "v5-r8-fault-aware",
            "final_answer": report,
            "actual_cost_usd": 0.12,
            "execution_budget": {"calls_reserved": 5, "actual_cost_usd": 0.12},
        })
        self._write(root, "v5-execution-graph.json", {
            "nodes": [
                {"node_id": f"node-{index}", "model": f"model-{index}", "provider_endpoint": f"model-{index}@provider-{index}"}
                for index in range(5)
            ],
            "final_nodes": ["node-4"],
        })
        self._write(root, "request-audit.json", {
            "status": "PASS",
            "expected_request_count": 5,
            "captured_request_count": 5,
            "external_tools_allowed": False,
        })
        self._write(root, "call-ledger.json", {
            "summary": {
                "call_count": 5,
                "provider_actual_cost_usd": 0.12,
                "substantive_provider_count": 5,
                "substantive_providers": [f"provider-{index}" for index in range(5)],
            }
        })
        self._write(root, "expert-team-report.md", report)
        self._write(root, "report-comments/report-comment-001.md", "published")
        self._write(root, "report-comments/report-comments-manifest.json", {
            "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
            "files": ["report-comment-001.md"],
        })

    def test_complete_v5_fixture_passes_native_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            result = auditor.audit(root, execute_outcome="success", publish_outcome="success")
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["checks"]["model_calls"], 5)
            self.assertEqual(result["checks"]["node_count"], 5)

    def test_v3_fallback_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            result_path = root / "expert-team-result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["v3_fallback_used"] = True
            result_path.write_text(json.dumps(result), encoding="utf-8")
            audited = auditor.audit(root, execute_outcome="success", publish_outcome="success")
            self.assertEqual(audited["status"], "FAIL")
            self.assertTrue(any("fallback" in reason.casefold() for reason in audited["failures"]))

    def test_call_ceiling_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            summary_path = root / "v5-execution-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["execution_budget"]["calls_reserved"] = 17
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            audited = auditor.audit(root, execute_outcome="success", publish_outcome="success")
            self.assertEqual(audited["status"], "FAIL")
            self.assertTrue(any("outside the production bound" in reason for reason in audited["failures"]))


if __name__ == "__main__":
    unittest.main()
