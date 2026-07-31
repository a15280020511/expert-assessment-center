import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_evidence_bundle import (  # noqa: E402
    ApprovedRun,
    EvidenceBundleBuilder,
    EvidenceInputs,
    FinalStatusInputs,
    build_final_attestation_record,
    build_final_status_record,
    render_final_status_markdown,
)


class V5EvidenceBundleTests(unittest.TestCase):
    def seed(self, root: Path) -> None:
        request = {
            "model": "vendor/model-a",
            "provider": {
                "only": ["provider-a"],
                "order": ["provider-a"],
                "allow_fallbacks": False,
            },
            "max_completion_tokens": 4096,
        }
        node = {
            "node_id": "node-a",
            "model": "vendor/model-a",
            "provider_endpoint": "vendor/model-a@provider-a",
            "parameter_profile": {
                "estimated_completion_usage_tokens": 3072,
                "output_allowance_is_cost_assumption": False,
            },
        }
        attempt = {
            "attempt_index": 1,
            "attempt_kind": "initial",
            "provider_endpoint": "vendor/model-a@provider-a",
            "request": request,
            "status": "passed",
            "answer": "A" * 300,
            "quality_score": 0.8,
            "gate_reasons": [],
            "usage": {"cost": 0.002},
            "response_id": "response-a",
            "response_provider": "provider-a",
        }
        documents = {
            "v5-runtime-config.json": {
                "runtime_version": "v5-native-runtime-1",
                "global_monkey_patching": False,
                "cross_task_history_used": False,
            },
            "catalog-snapshot.json": {
                "catalog_snapshot_id": "catalog-test",
                "cross_task_history_used": False,
            },
            "v5-execution-graph.json": {
                "nodes": [node],
                "required_work": ["work-a"],
                "final_nodes": ["node-a"],
            },
            "v5-node-results.json": [{
                "node_id": "node-a",
                "status": "success",
                "attempts": [attempt],
            }],
            "v5-execution-summary.json": {
                "status": "success",
                "completion_mode": "full",
                "quality_status": "full_success",
                "final_answer": "A" * 300,
                "actual_cost_usd": 0.002,
                "executor": "v5-native-execution-engine",
                "execution_budget": {
                    "calls_reserved": 1,
                    "retries_reserved": 0,
                    "replacements_reserved": 0,
                    "recovery_calls_reserved": 0,
                },
                "quality_integrity": {"status": "PASS"},
            },
            "v5-request-audit.json": {
                "status": "PASS",
                "request_count": 1,
                "requests": [request],
                "dynamic_output_allowance_sent": True,
                "bounded_output_allowance_sent": True,
                "artificial_token_ceiling_sent": False,
                "quality_integrity_status": "PASS",
            },
            "v5-optimization.json": {
                "selected_interpretation": "interpretation-a",
            },
            "ticket-status.json": {
                "task_id": "task-a",
                "task_fingerprint": "fingerprint-a",
                "calls": 4,
                "maximum_recovery_calls": 1,
            },
            "execution-diagnosis.json": {
                "status": "PASS",
                "failures": [],
                "degradations": [],
                "primary_failure": {},
            },
        }
        for name, value in documents.items():
            (root / name).write_text(
                json.dumps(value, ensure_ascii=False),
                encoding="utf-8",
            )
        (root / "v5-final-report.md").write_text("# 报告\n\n" + "A" * 300, encoding="utf-8")

    def test_preupload_documents_share_one_input_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed(root)
            inputs = EvidenceInputs.from_directory(root)
            builder = EvidenceBundleBuilder(
                inputs,
                ApprovedRun(total_calls=4, recovery_calls=1, cost_anomaly_usd=0.20),
            )
            result = builder.write(root, require_report=True)
            bundle = json.loads((root / "evidence-bundle.json").read_text(encoding="utf-8"))
            summary = json.loads((root / "execution-summary.json").read_text(encoding="utf-8"))
            normalized = json.loads((root / "expert-team-result.json").read_text(encoding="utf-8"))
            ledger = json.loads((root / "call-ledger.json").read_text(encoding="utf-8"))
            selection = json.loads((root / "model-selection.json").read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "success")
            self.assertTrue(bundle["business_evidence_frozen"])
            self.assertEqual(summary["evidence_input_sha256"], bundle["input_sha256"])
            self.assertEqual(normalized["evidence_input_sha256"], bundle["input_sha256"])
            self.assertEqual(ledger["summary"]["call_count"], 1)
            self.assertEqual(ledger["summary"]["provider_actual_cost_usd"], 0.002)
            self.assertFalse(selection["cross_task_history_used"])
            self.assertEqual(selection["catalog_snapshot_id"], "catalog-test")
            self.assertTrue((root / "artifact-manifest.json").is_file())
            self.assertTrue((root / "expert-team-report.md").is_file())

    def test_builder_rejects_call_count_split_brain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed(root)
            summary = json.loads((root / "v5-execution-summary.json").read_text(encoding="utf-8"))
            summary["execution_budget"]["calls_reserved"] = 5
            (root / "v5-execution-summary.json").write_text(json.dumps(summary), encoding="utf-8")
            builder = EvidenceBundleBuilder(
                EvidenceInputs.from_directory(root),
                ApprovedRun(total_calls=4, recovery_calls=1, cost_anomaly_usd=None),
            )
            with self.assertRaisesRegex(RuntimeError, "exceeded approved total paid-call ceiling"):
                builder.write(root, require_report=True)

    def test_postupload_phase_only_injects_artifact_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed(root)
            EvidenceBundleBuilder(
                EvidenceInputs.from_directory(root),
                ApprovedRun(total_calls=4, recovery_calls=1, cost_anomaly_usd=0.20),
            ).write(root, require_report=True)
            status_inputs = FinalStatusInputs.from_directory(root)
            status = build_final_status_record(
                status_inputs,
                run_url="https://example.invalid/run/1",
                ticket_upload_outcome="success",
                audit_outcome="success",
                manifest_outcome="success",
                artifact_id="123",
                artifact_url="https://example.invalid/artifact/123",
                artifact_digest="sha256:test",
            )
            status_json = root / "final-status.json"
            status_json.write_text(json.dumps(status), encoding="utf-8")
            status_md = root / "final-status.md"
            status_md.write_text(render_final_status_markdown(status), encoding="utf-8")
            attestation = build_final_attestation_record(
                root=root,
                primary_artifact_id="123",
                primary_artifact_digest="sha256:test",
                primary_artifact_url="https://example.invalid/artifact/123",
                audit_status="PASS",
                run_id="1",
                commit_sha="a" * 40,
                final_status_file=status_md,
            )

            bundle = json.loads((root / "evidence-bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "PASS")
            self.assertTrue(status["business_evidence_frozen"])
            self.assertEqual(status["evidence_input_sha256"], bundle["input_sha256"])
            self.assertEqual(attestation["evidence_input_sha256"], bundle["input_sha256"])
            self.assertTrue(attestation["business_evidence_frozen_before_upload"])
            self.assertEqual(attestation["primary_artifact"]["artifact_id"], 123)
            self.assertEqual(attestation["version"], 2)


if __name__ == "__main__":
    unittest.main()
