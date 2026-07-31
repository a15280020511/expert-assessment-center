from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_execution_auditor_integrity as auditor  # noqa: E402


class V5NativeAuditContractTests(unittest.TestCase):
    def _write(self, root: Path, name: str, value) -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str):
            path.write_text(value, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _fixture(self, root: Path) -> None:
        answer = "# 完整生产报告\n\n" + ("事实、假设、推断、风险、执行步骤和否决条件。" * 30)
        self._write(
            root,
            "ticket-status.json",
            {
                "accepted": True,
                "calls": 4,
                "maximum_recovery_calls": 1,
                "maximum_initial_calls": 3,
                "cost_policy": "unbounded_with_anomaly_guard",
                "cost_anomaly_usd": 0.25,
            },
        )
        self._write(
            root,
            "production-runtime.json",
            {
                "runtime_version": "v5-native-runtime-1",
                "fallback_policy": "fail-closed-no-alternate-runtime",
                "legacy_runtime_present": False,
                "cross_task_history_used": False,
            },
        )
        self._write(
            root,
            "expert-team-result.json",
            {
                "runtime_version": "v5-native-runtime-1",
                "status": "success",
                "completion_mode": "full",
                "quality_status": "full_success",
                "final_answer": answer,
                "executor": "v5-native-execution-engine",
                "fallback_used": False,
                "legacy_runtime_present": False,
            },
        )
        self._write(
            root,
            "v5-execution-summary.json",
            {
                "status": "success",
                "completion_mode": "full",
                "quality_status": "full_success",
                "quality_integrity": {"status": "PASS"},
                "executor": "v5-native-execution-engine",
                "final_answer": answer,
                "actual_cost_usd": 0.09615135,
                "execution_budget": {
                    "calls_reserved": 4,
                    "maximum_total_calls": 4,
                    "maximum_initial_calls": 3,
                    "actual_cost_usd": 0.09615135,
                },
            },
        )
        self._write(
            root,
            "v5-execution-graph.json",
            {
                "nodes": [
                    {"node_id": "node-a"},
                    {"node_id": "node-b"},
                    {"node_id": "node-final"},
                ],
                "final_nodes": ["node-final"],
            },
        )
        self._write(
            root,
            "v5-node-results.json",
            [
                {"node_id": "node-a", "status": "success", "contract": {"required_fields_complete": True}},
                {"node_id": "node-b", "status": "success_recovered", "contract": {"required_fields_complete": True}},
                {"node_id": "node-final", "status": "success", "contract": {"required_fields_complete": True}},
            ],
        )
        self._write(
            root,
            "request-audit.json",
            {
                "status": "PASS",
                "approved_total_call_ceiling": 4,
                "expected_request_count": 4,
                "captured_request_count": 4,
                "external_tools_allowed": False,
            },
        )
        self._write(
            root,
            "call-ledger.json",
            {
                "summary": {
                    "call_count": 4,
                    "approved_total_call_ceiling": 4,
                    "approved_recovery_call_ceiling": 1,
                    "provider_actual_cost_usd": 0.09615135,
                    "substantive_provider_count": 2,
                    "substantive_providers": ["Amazon Bedrock", "OpenAI"],
                }
            },
        )
        self._write(root, "expert-team-report.md", answer)
        run_url = "https://github.com/a15280020511/expert-assessment-center/actions/runs/30619634773"
        self._write(
            root,
            "report-comments/report-comment-001.md",
            "<!-- expert-team-report-run:30619634773:part:001 -->\n"
            f"- Run: `{run_url}`\n\npublished",
        )
        self._write(
            root,
            "report-comments/report-comments-manifest.json",
            {
                "version": 2,
                "report_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                "run_url": run_url,
                "run_id": "30619634773",
                "files": ["report-comment-001.md"],
            },
        )

    def test_real_native_success_shape_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            result = auditor.audit(
                root,
                execute_outcome="success",
                publish_outcome="success",
            )
            self.assertEqual("PASS", result["status"], result["failures"])
            self.assertEqual([], result["failures"])
            self.assertEqual("NONE", result["primary_failure"]["code"])
            self.assertEqual(
                "PASS",
                result["checks"]["native_contract_status"],
            )
            self.assertEqual(3, result["checks"]["strict_node_count"])
            self.assertEqual(4, result["checks"]["model_calls"])
            self.assertAlmostEqual(
                0.09615135,
                result["checks"]["actual_cost_usd"],
            )

    def test_missing_report_run_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            manifest_path = root / "report-comments/report-comments-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["run_url"] = ""
            manifest["run_id"] = "unknown"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = auditor.audit(
                root,
                execute_outcome="success",
                publish_outcome="success",
            )
            self.assertEqual("FAIL", result["status"])
            self.assertIn(
                "published report run identity is missing or invalid",
                result["failures"],
            )

    def test_incomplete_node_contract_cannot_pass_as_full_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            rows_path = root / "v5-node-results.json"
            rows = json.loads(rows_path.read_text(encoding="utf-8"))
            rows[0]["contract"]["required_fields_complete"] = False
            rows_path.write_text(json.dumps(rows), encoding="utf-8")
            result = auditor.audit(
                root,
                execute_outcome="success",
                publish_outcome="success",
            )
            self.assertNotEqual("PASS", result["status"])
            self.assertEqual(1, result["checks"]["contract_incomplete_node_count"])

    def test_obsolete_r8_runtime_identifier_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            for filename in ("production-runtime.json", "expert-team-result.json"):
                path = root / filename
                value = json.loads(path.read_text(encoding="utf-8"))
                value["runtime_version"] = "v5-r8"
                path.write_text(json.dumps(value), encoding="utf-8")
            result = auditor.audit(
                root,
                execute_outcome="success",
                publish_outcome="success",
            )
            self.assertEqual("FAIL", result["status"])
            self.assertTrue(
                any("native runtime version" in reason for reason in result["failures"])
            )

    def test_obsolete_r8_executor_identifier_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            for filename in ("expert-team-result.json", "v5-execution-summary.json"):
                path = root / filename
                value = json.loads(path.read_text(encoding="utf-8"))
                value["executor"] = "v5-r8-fault-aware"
                path.write_text(json.dumps(value), encoding="utf-8")
            result = auditor.audit(
                root,
                execute_outcome="success",
                publish_outcome="success",
            )
            self.assertEqual("FAIL", result["status"])
            self.assertTrue(
                any("native executor" in reason for reason in result["failures"])
            )


if __name__ == "__main__":
    unittest.main()
