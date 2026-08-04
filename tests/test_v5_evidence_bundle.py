import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from artifact_manifest import write_manifest  # noqa: E402
from v5_evidence_bundle import (  # noqa: E402
    FinalStatusInputs,
    build_final_attestation_record,
    build_final_status_record,
    render_final_status_markdown,
)
from v5_run_evidence import (  # noqa: E402
    ApprovedRun,
    EvidenceBundleBuilder,
    EvidenceInputs,
)


class V5EvidenceBundleTests(unittest.TestCase):
    @staticmethod
    def independent_revalidation() -> dict[str, object]:
        return {
            "schema_version": "v5-independent-artifact-revalidation-3",
            "status": "PASS",
            "recomputed_from_primitive_evidence": True,
            "paid_acceptance_verdict_used_as_source": False,
            "actual_cost_usd": 0.002,
        }

    def seed(self, root: Path) -> None:
        governance_request = {
            "model": "~openai/gpt-latest",
            "provider": {
                "only": ["openai"],
                "order": ["openai"],
                "allow_fallbacks": False,
            },
            "request_fields": ["model", "messages", "provider"],
            "messages": [{"role": "system", "characters": 10, "sha256": "a" * 64}],
            "raw_message_content_persisted": False,
        }
        expert_request = {
            "model": "vendor/model-a",
            "provider": {
                "only": ["provider-a"],
                "order": ["provider-a"],
                "allow_fallbacks": False,
            },
        }
        node = {
            "node_id": "node-a",
            "model": "vendor/model-a",
            "provider_endpoint": "vendor/model-a@provider-a",
        }
        algorithm_absence = {
            "local_scoring_used": False,
            "optimizer_used": False,
            "cp_sat_used": False,
            "pareto_pruning_used": False,
            "heuristic_ranking_used": False,
        }
        documents = {
            "v5-runtime-config.json": {
                "runtime_version": "v5-native-runtime-1",
                "global_monkey_patching": False,
                "cross_task_history_used": False,
            },
            "catalog-snapshot.json": {"catalog_snapshot_id": "catalog-test"},
            "v5-execution-graph.json": {
                "nodes": [
                    {
                        **node,
                        "output_contract": {"final_delivery_node": True},
                    }
                ],
                "required_work": ["work-a"],
                "final_nodes": ["node-a"],
                "metadata": algorithm_absence,
            },
            "v5-node-results.json": [
                {
                    "node_id": "node-a",
                    "status": "success",
                    "model": "vendor/model-a",
                    "attempts": [],
                }
            ],
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
                "request_count": 4,
                "requests": [governance_request, governance_request, governance_request, expert_request],
            },
            "v5-selection.json": {
                "optimizer_used": False,
                "materialization": algorithm_absence,
            },
            "v5-governance-result.json": {
                "status": "PASS",
                "claude_review_count": 1,
                "claude_covers_internal_selection": True,
                "claude_covers_external_information": True,
            },
            "v5-governance-calls.json": {
                "actual_governance_calls": 3,
                "claude_red_team_calls": 1,
                "gpt_synthesis_calls": 1,
                "calls": [
                    {"requested_model": "~openai/gpt-latest", "provider": "openai"},
                    {"requested_model": "~anthropic/claude-opus-latest", "provider": "anthropic"},
                    {"requested_model": "~openai/gpt-latest", "provider": "openai"},
                ],
            },
            "ticket-status.json": {
                "task_id": "task-a",
                "task_fingerprint": "fingerprint-a",
                "calls": 5,
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
            (root / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        (root / "v5-final-report.md").write_text("# 报告\n\n" + "A" * 300, encoding="utf-8")

    def test_preupload_documents_share_one_input_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed(root)
            result = EvidenceBundleBuilder(
                EvidenceInputs.from_directory(root),
                ApprovedRun(total_calls=5, recovery_calls=1, cost_anomaly_usd=0.20),
            ).write(root, require_report=True)
            bundle = json.loads((root / "evidence-bundle.json").read_text(encoding="utf-8"))
            ledger = json.loads((root / "call-ledger.json").read_text(encoding="utf-8"))
            selection = json.loads((root / "model-selection.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "success")
            self.assertTrue(bundle["business_evidence_frozen"])
            self.assertEqual(ledger["summary"]["call_count"], 4)
            self.assertFalse(selection["optimizer_used"])
            self.assertFalse(result["cross_task_history_used"])
            self.assertTrue((root / "artifact-manifest.json").is_file())

    def test_builder_rejects_call_count_split_brain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed(root)
            summary = json.loads((root / "v5-execution-summary.json").read_text(encoding="utf-8"))
            summary["execution_budget"]["calls_reserved"] = 2
            (root / "v5-execution-summary.json").write_text(json.dumps(summary), encoding="utf-8")
            builder = EvidenceBundleBuilder(
                EvidenceInputs.from_directory(root),
                ApprovedRun(total_calls=5, recovery_calls=1, cost_anomaly_usd=None),
            )
            with self.assertRaisesRegex(RuntimeError, "disagree"):
                builder.write(root, require_report=True)

    def test_postupload_phase_only_injects_artifact_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed(root)
            EvidenceBundleBuilder(
                EvidenceInputs.from_directory(root),
                ApprovedRun(total_calls=5, recovery_calls=1, cost_anomaly_usd=0.20),
            ).write(root, require_report=True)
            independent_path = root / "independent-revalidation.json"
            independent_path.write_text(
                json.dumps(self.independent_revalidation()),
                encoding="utf-8",
            )
            status = build_final_status_record(
                FinalStatusInputs.from_directory(root),
                run_url="https://example.invalid/run/1",
                ticket_upload_outcome="success",
                audit_outcome="success",
                manifest_outcome="success",
                artifact_id="123",
                artifact_url="https://example.invalid/artifact/123",
                artifact_digest="sha256:test",
                independent_revalidation=self.independent_revalidation(),
            )
            status_md = root / "final-status.md"
            status_md.write_text(render_final_status_markdown(status), encoding="utf-8")
            write_manifest(root)
            attestation = build_final_attestation_record(
                root=root,
                primary_artifact_id="123",
                primary_artifact_digest="sha256:test",
                primary_artifact_url="https://example.invalid/artifact/123",
                audit_status="PASS",
                run_id="1",
                commit_sha="a" * 40,
                final_status_file=status_md,
                independent_revalidation_file=independent_path,
            )
            bundle = json.loads((root / "evidence-bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "PASS")
            self.assertEqual(attestation["status"], "PASS")
            self.assertEqual(attestation["evidence_input_sha256"], bundle["input_sha256"])
            self.assertEqual(attestation["primary_artifact"]["artifact_id"], 123)

    def test_positive_final_status_requires_independent_revalidation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed(root)
            EvidenceBundleBuilder(
                EvidenceInputs.from_directory(root),
                ApprovedRun(
                    total_calls=5,
                    recovery_calls=1,
                    cost_anomaly_usd=0.20,
                ),
            ).write(root, require_report=True)
            status = build_final_status_record(
                FinalStatusInputs.from_directory(root),
                run_url="https://example.invalid/run/1",
                ticket_upload_outcome="success",
                audit_outcome="success",
                manifest_outcome="success",
                artifact_id="123",
                artifact_url="https://example.invalid/artifact/123",
                artifact_digest="sha256:test",
            )
            self.assertEqual(status["status"], "FAIL")
            self.assertEqual(
                status["independent_artifact_revalidation_status"],
                "MISSING",
            )

    def test_attestation_fails_closed_when_audit_and_diagnosis_disagree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed(root)
            EvidenceBundleBuilder(
                EvidenceInputs.from_directory(root),
                ApprovedRun(total_calls=5, recovery_calls=1, cost_anomaly_usd=0.20),
            ).write(root, require_report=True)
            diagnosis = json.loads(
                (root / "execution-diagnosis.json").read_text(encoding="utf-8")
            )
            diagnosis["status"] = "DEGRADED"
            (root / "execution-diagnosis.json").write_text(
                json.dumps(diagnosis),
                encoding="utf-8",
            )
            independent_path = root / "independent-revalidation.json"
            independent_path.write_text(
                json.dumps(self.independent_revalidation()),
                encoding="utf-8",
            )
            status_md = root / "final-status.md"
            status_md.write_text("## EXECUTION_COMPLETED\n", encoding="utf-8")
            write_manifest(root)
            attestation = build_final_attestation_record(
                root=root,
                primary_artifact_id="123",
                primary_artifact_digest="sha256:test",
                primary_artifact_url="https://example.invalid/artifact/123",
                audit_status="PASS",
                run_id="1",
                commit_sha="a" * 40,
                final_status_file=status_md,
                independent_revalidation_file=independent_path,
            )
            self.assertEqual(attestation["status"], "FAIL")
            self.assertEqual(attestation["audit_status"], "PASS")
            self.assertEqual(attestation["diagnosis_status"], "DEGRADED")


if __name__ == "__main__":
    unittest.main()
