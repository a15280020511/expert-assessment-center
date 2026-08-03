import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from publish_report import write_failure_skip_manifest  # noqa: E402
from v5_evidence_bundle import build_final_attestation_record  # noqa: E402


class TestV5FailureEvidenceChain(unittest.TestCase):
    def seed_failure(self, root: Path) -> Path:
        (root / "artifact-manifest.json").write_text(
            json.dumps({"files": []}), encoding="utf-8"
        )
        (root / "evidence-bundle.json").write_text(
            json.dumps({
                "input_sha256": "a" * 64,
                "business_evidence_frozen": True,
            }),
            encoding="utf-8",
        )
        (root / "execution-diagnosis.json").write_text(
            json.dumps({
                "status": "FAIL",
                "primary_failure": {
                    "code": "BUDGET_INSUFFICIENT_COST",
                    "stage": "planning",
                },
            }),
            encoding="utf-8",
        )
        (root / "expert-team-result.json").write_text(
            json.dumps({"status": "failed"}), encoding="utf-8"
        )
        final_status = root / "final-status.md"
        final_status.write_text("## EXECUTION_FAILED\n", encoding="utf-8")
        return final_status

    def test_failed_execution_skips_report_publication_without_publisher_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed_failure(root)
            manifest = write_failure_skip_manifest(
                root,
                root / "v5-final-report.md",
                root / "report-comments",
                run_url="https://github.com/example/repo/actions/runs/123",
                max_chars=50_000,
            )
            self.assertEqual(
                manifest["publication_status"], "skipped_failed_execution"
            )
            self.assertEqual(manifest["comment_count"], 0)
            self.assertEqual(
                manifest["report_comment_preparation_status"],
                "NOT_APPLICABLE",
            )
            self.assertFalse(manifest["issue_context_required"])
            self.assertIsNone(manifest["report_sha256"])
            self.assertTrue(
                (root / "report-comments" / "report-comments-manifest.json").is_file()
            )

    def test_failed_execution_attestation_does_not_require_business_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            final_status = self.seed_failure(root)
            attestation = build_final_attestation_record(
                root=root,
                primary_artifact_id="42",
                primary_artifact_digest="b" * 64,
                primary_artifact_url="https://example.invalid/artifacts/42",
                audit_status="FAIL",
                run_id="123",
                commit_sha="c" * 40,
                final_status_file=final_status,
            )
            self.assertEqual(attestation["status"], "FAIL")
            self.assertFalse(attestation["report_required"])
            self.assertFalse(attestation["report_present"])
            self.assertIsNone(attestation["report_sha256"])
            self.assertEqual(attestation["diagnosis_status"], "FAIL")

    def test_pass_attestation_still_requires_business_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            final_status = self.seed_failure(root)
            with self.assertRaisesRegex(
                RuntimeError,
                "successful or degraded execution requires a report",
            ):
                build_final_attestation_record(
                    root=root,
                    primary_artifact_id="42",
                    primary_artifact_digest="b" * 64,
                    primary_artifact_url="https://example.invalid/artifacts/42",
                    audit_status="PASS",
                    run_id="123",
                    commit_sha="c" * 40,
                    final_status_file=final_status,
                )

    def test_fail_attestation_requires_diagnosis(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            final_status = self.seed_failure(root)
            (root / "execution-diagnosis.json").unlink()
            with self.assertRaisesRegex(
                RuntimeError,
                "failed execution requires deterministic diagnosis evidence",
            ):
                build_final_attestation_record(
                    root=root,
                    primary_artifact_id="42",
                    primary_artifact_digest="b" * 64,
                    primary_artifact_url="https://example.invalid/artifacts/42",
                    audit_status="FAIL",
                    run_id="123",
                    commit_sha="c" * 40,
                    final_status_file=final_status,
                )


if __name__ == "__main__":
    unittest.main()
