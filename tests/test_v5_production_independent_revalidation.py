from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))


class V5ProductionIndependentRevalidationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = (ROOT / ".github/workflows/execution-ticket.yml").read_text(
            encoding="utf-8"
        )

    def test_primary_artifact_is_downloaded_and_recomputed_before_publication(self) -> None:
        workflow = self.workflow
        independent = workflow.index(
            "Independently revalidate uploaded primary artifact"
        )
        publication = workflow.index("Publish report only after audit and artifact freeze")
        self.assertLess(independent, publication)
        self.assertIn("v5_independent_artifact_revalidation.py", workflow)
        self.assertIn("uploaded-primary-artifact.zip", workflow)
        self.assertIn("--expected-artifact-digest", workflow)
        self.assertIn("--expected-sha", workflow)
        self.assertIn("--expected-run-id", workflow)

    def test_publication_and_final_job_require_v3_pass(self) -> None:
        workflow = self.workflow
        self.assertIn("steps.independent.outcome == 'success'", workflow)
        self.assertIn("steps.independent.outputs.status == 'PASS'", workflow)
        self.assertIn('test "${{ steps.independent.outputs.status }}" = "PASS"', workflow)
        self.assertIn("independent-artifact-revalidation-failed", workflow)

    def test_final_attestation_binds_independent_evidence(self) -> None:
        workflow = self.workflow
        self.assertIn("v5-independent-artifact-revalidation-3", workflow)
        self.assertIn('attestation["independent_artifact_revalidation"]', workflow)
        self.assertIn('"evidence_sha256"', workflow)
        self.assertIn("independent-revalidation.json", workflow)
        self.assertIn("independent-artifact-revalidation -> final-status", workflow)


if __name__ == "__main__":
    unittest.main()
