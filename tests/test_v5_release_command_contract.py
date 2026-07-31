import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote-v5-production.yml"


class TestV5ReleaseCommandContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_manual_dispatch_remains_available(self):
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("target_sha:", self.text)
        self.assertIn("- promote\n          - rollback", self.text)

    def test_issue_release_command_is_owner_only_and_title_scoped(self):
        self.assertIn("issue_comment:", self.text)
        self.assertIn("github.actor == github.repository_owner", self.text)
        self.assertIn("startsWith(github.event.issue.title, '[release]')", self.text)
        self.assertIn("/promote-v5-production ", self.text)
        self.assertIn("/rollback-v5-production ", self.text)

    def test_command_parser_requires_exact_action_and_full_sha(self):
        self.assertIn(
            'r"/(promote|rollback)-v5-production ([0-9a-f]{40})"',
            self.text,
        )
        self.assertIn("re.fullmatch", self.text)
        self.assertIn("target SHA must be exactly 40 lowercase hex characters", self.text)

    def test_release_still_runs_zero_cost_validation_before_ref_move(self):
        unit = self.text.index("name: Run static and full unit validation")
        dry = self.text.index("name: Run deterministic no-call V5 regression")
        move = self.text.index("name: Move production ref")
        self.assertLess(unit, dry)
        self.assertLess(dry, move)
        self.assertIn("--maximum-total-calls 4", self.text)
        self.assertIn("--maximum-recovery-calls 1", self.text)
        self.assertNotIn("OPENROUTER_API_KEY", self.text)

    def test_release_reuses_direction_and_rollback_guards(self):
        self.assertIn("git merge-base --is-ancestor", self.text)
        self.assertIn("--force-with-lease", self.text)
        self.assertIn("refs/heads/production", self.text)
        self.assertIn("group: v5-production-release", self.text)
        self.assertIn("cancel-in-progress: false", self.text)


if __name__ == "__main__":
    unittest.main()
