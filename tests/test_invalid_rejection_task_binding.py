from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIMARY = (ROOT / ".github" / "workflows" / "execution-ticket.yml").read_text(
    encoding="utf-8"
)
SECONDARY = ROOT / ".github" / "workflows" / "invalid-ticket-rejection.yml"


class InvalidRejectionTaskBindingTests(unittest.TestCase):
    def test_secondary_invalid_ticket_admission_workflow_is_absent(self) -> None:
        self.assertFalse(
            SECONDARY.exists(),
            "A second /run admission workflow can race the authoritative dynamic execution chain",
        )

    def test_primary_chain_is_the_only_owner_command_admission_path(self) -> None:
        self.assertIn("issue_comment:", PRIMARY)
        self.assertIn("github.actor == github.repository_owner", PRIMARY)
        self.assertIn("/run-expert-team", PRIMARY)
        self.assertIn("/retry-expert-team", PRIMARY)
        self.assertIn("v5_price_ranked_issue_ticket.py prepare", PRIMARY)
        self.assertIn("v5_price_ranked_issue_ticket.py render", PRIMARY)

    def test_primary_chain_hydrates_transport_before_admission(self) -> None:
        fetch = PRIMARY.index("Fetch all governance candidate transport comments")
        prepare = PRIMARY.index("Build task-dynamic expert plan")
        publish = PRIMARY.index("Publish dynamic admission receipt")
        self.assertLess(fetch, prepare)
        self.assertLess(prepare, publish)
        self.assertIn("--comments-path ticket-artifacts/issue-comments.json", PRIMARY)
        self.assertIn("gh api --paginate --slurp", PRIMARY)

    def test_primary_rejection_and_execution_share_same_validator_checkout(self) -> None:
        self.assertIn("name: Checkout current main", PRIMARY)
        self.assertIn("ref: main", PRIMARY)
        self.assertNotIn("ref: production", PRIMARY)
        self.assertEqual(PRIMARY.count("v5_price_ranked_issue_ticket.py prepare"), 1)


if __name__ == "__main__":
    unittest.main()
