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

    def test_release_pr_is_owner_only_same_repo_and_production_scoped(self):
        self.assertIn("pull_request_target:", self.text)
        self.assertIn("branches: [production]", self.text)
        self.assertIn("github.actor == github.repository_owner", self.text)
        self.assertIn(
            "startsWith(github.event.pull_request.title, '[release]')",
            self.text,
        )
        self.assertIn(
            "github.event.pull_request.base.ref == 'production'",
            self.text,
        )
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )
        self.assertNotIn("issue_comment:", self.text)

    def test_pr_parser_requires_exact_action_and_full_sha(self):
        self.assertIn(
            'r"(?m)^Action: `(promote|rollback)`\\s*$"',
            self.text,
        )
        self.assertIn(
            'r"(?m)^Target: `([0-9a-f]{40})`\\s*$"',
            self.text,
        )
        self.assertIn("len(actions) != 1 or len(targets) != 1", self.text)
        self.assertIn(
            "target SHA must be exactly 40 lowercase hex characters",
            self.text,
        )

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

    def test_pr_head_is_never_checked_out_with_write_token(self):
        self.assertIn(
            "ref: ${{ steps.release.outputs.target_sha }}",
            self.text,
        )
        self.assertNotIn("github.event.pull_request.head.sha", self.text)


if __name__ == "__main__":
    unittest.main()
