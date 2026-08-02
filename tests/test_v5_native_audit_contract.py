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

    def _write_execution_fixture(self, root: Path, answer: str) -> None:
        self._write(
            root,
            "ticket-status.json",
            {
                "accepted": True,
                "calls": 7,
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
                {
                    "node_id": "node-a",
                    "status": "success",
                    "contract": {"required_fields_complete": True},
                },
                {
                    "node_id": "node-b",
                    "status": "success_recovered",
                    "contract": {"required_fields_complete": True},
                },
                {
                    "node_id": "node-final",
                    "status": "success",
                    "contract": {"required_fields_complete": True},
                },
            ],
        )

    def _write_constraint_fixture(self, root: Path) -> None:
        constraints = {
            "schema_version": "v5-task-constraints-1",
            "degradation_authorization": "default_denied",
            "allow_degraded_success": False,
            "external_tools_allowed": False,
            "external_facts_allowed": True,
            "unsupported_precise_quantities_allowed": True,
            "source_attribution_required": False,
            "fact_provenance_required": False,
            "fail_closed": True,
            "matched_prohibitions": [],
            "matched_permissions": [],
            "policy": "explicit-deny-overrides-allow-default-deny",
        }
        self._write(root, "task-constraints.json", constraints)
        self._write(
            root,
            "evidence-integrity.json",
            {
                "schema_version": "v5-evidence-integrity-1",
                "status": "PASS",
                "constraints": constraints,
                "violations": [],
                "fact_truth_not_inferred_from_structure": True,
                "upstream_model_claims_are_not_promoted_to_user_facts": True,
            },
        )

    def _write_company_fixture(self, root: Path) -> None:
        successful = [
            {"node_id": "node-a", "model": "openai/model-a", "company": "openai"},
            {
                "node_id": "node-b",
                "model": "anthropic/model-b",
                "company": "anthropic",
            },
            {
                "node_id": "node-final",
                "model": "google/model-c",
                "company": "google",
            },
        ]
        called = [
            {
                "node_id": "node-a",
                "attempt_kind": "initial",
                "model": "openai/model-a",
                "company": "openai",
                "status": "passed",
            },
            {
                "node_id": "node-b",
                "attempt_kind": "initial",
                "model": "anthropic/model-b",
                "company": "anthropic",
                "status": "call_failed",
            },
            {
                "node_id": "node-b",
                "attempt_kind": "recovery",
                "model": "anthropic/model-b",
                "company": "anthropic",
                "status": "passed",
            },
            {
                "node_id": "node-final",
                "attempt_kind": "initial",
                "model": "google/model-c",
                "company": "google",
                "status": "passed",
            },
        ]
        self._write(
            root,
            "actual-model-company-audit.json",
            {
                "status": "PASS",
                "policy": "recompute-from-all-actual-called-models",
                "successful_node_models": successful,
                "all_called_models": called,
                "duplicate_called_companies_across_nodes": {},
                "duplicate_successful_companies": {},
                "unresolved_called_companies": [],
                "same_node_retry_is_not_a_second_expert": True,
                "failed_calls_are_included": True,
                "cross_task_history_used": False,
            },
        )

    def _write_request_fixture(self, root: Path) -> None:
        self._write(
            root,
            "request-audit.json",
            {
                "status": "PASS",
                "approved_total_call_ceiling": 7,
                "request_count": 7,
                "governance_request_count": 3,
                "expert_request_count": 4,
                "requests": [{} for _ in range(7)],
                "external_tools_allowed": False,
                "provider_fallback_allowed": False,
            },
        )
        self._write(
            root,
            "call-ledger.json",
            {
                "summary": {
                    "call_count": 7,
                    "governance_calls": 3,
                    "expert_calls": 4,
                    "approved_total_call_ceiling": 7,
                    "approved_recovery_call_ceiling": 1,
                    "provider_actual_cost_usd": 0.09615135,
                    "substantive_provider_count": 2,
                    "substantive_providers": ["Amazon Bedrock", "OpenAI"],
                }
            },
        )

    def _write_publication_fixture(self, root: Path, answer: str) -> None:
        self._write(root, "expert-team-report.md", answer)
        run_url = (
            "https://github.com/a15280020511/expert-assessment-center/"
            "actions/runs/30619634773"
        )
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

    def _fixture(self, root: Path) -> None:
        answer = "# 完整生产报告\n\n" + (
            "事实、假设、推断、风险、执行步骤和否决条件。" * 30
        )
        self._write_execution_fixture(root, answer)
        self._write_constraint_fixture(root)
        self._write_company_fixture(root)
        self._write_request_fixture(root)
        self._write_publication_fixture(root, answer)

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
            self.assertEqual(7, result["checks"]["model_calls"])
            self.assertEqual(3, result["checks"]["governance_model_calls"])
            self.assertEqual(4, result["checks"]["expert_model_calls"])
            self.assertEqual(
                "PASS",
                result["checks"]["actual_model_company_audit_status"],
            )
            self.assertEqual(
                4,
                result["checks"]["actual_called_model_count"],
            )
            self.assertTrue(result["checks"]["failed_calls_are_included"])
            self.assertAlmostEqual(
                0.09615135,
                result["checks"]["actual_cost_usd"],
            )

    def test_missing_report_run_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            manifest_path = (
                root / "report-comments/report-comments-manifest.json"
            )
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            manifest["run_url"] = ""
            manifest["run_id"] = "unknown"
            manifest_path.write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
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
            rows_path.write_text(
                json.dumps(rows),
                encoding="utf-8",
            )
            result = auditor.audit(
                root,
                execute_outcome="success",
                publish_outcome="success",
            )
            self.assertNotEqual("PASS", result["status"])
            self.assertEqual(
                1,
                result["checks"]["contract_incomplete_node_count"],
            )

    def test_duplicate_actual_company_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            path = root / "actual-model-company-audit.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["status"] = "FAIL"
            for row in value["all_called_models"]:
                if row["node_id"] == "node-b":
                    row["company"] = "openai"
                    row["model"] = "openai/model-b"
            value["successful_node_models"][1]["company"] = "openai"
            value["successful_node_models"][1]["model"] = "openai/model-b"
            value["duplicate_called_companies_across_nodes"] = {
                "openai": ["node-a", "node-b"]
            }
            value["duplicate_successful_companies"] = {
                "openai": ["node-a", "node-b"]
            }
            path.write_text(json.dumps(value), encoding="utf-8")
            result = auditor.audit(
                root,
                execute_outcome="success",
                publish_outcome="success",
            )
            self.assertEqual("FAIL", result["status"])
            self.assertIn(
                "a model company was reused across different nodes",
                result["failures"],
            )

    def test_obsolete_r8_runtime_identifier_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            for filename in (
                "production-runtime.json",
                "expert-team-result.json",
            ):
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
                any(
                    "native runtime version" in reason
                    for reason in result["failures"]
                )
            )

    def test_obsolete_r8_executor_identifier_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            for filename in (
                "expert-team-result.json",
                "v5-execution-summary.json",
            ):
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
                any(
                    "native executor" in reason
                    for reason in result["failures"]
                )
            )


if __name__ == "__main__":
    unittest.main()
