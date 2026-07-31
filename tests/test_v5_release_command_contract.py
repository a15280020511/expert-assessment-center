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

    def test_release_push_is_owner_only_and_fixed_branch_scoped(self):
        self.assertIn("push:", self.text)
        self.assertIn("branches: [v5-production-release]", self.text)
        self.assertIn("github.actor == github.repository_owner", self.text)
        self.assertIn(
            "github.ref == 'refs/heads/v5-production-release'",
            self.text,
        )
        self.assertIn("github.event.deleted == false", self.text)
        self.assertNotIn("issue_comment:", self.text)
        self.assertNotIn("pull_request_target:", self.text)

    def test_request_parser_requires_exact_schema_action_and_full_sha(self):
        self.assertIn('Path(".release-request.json")', self.text)
        self.assertIn(
            'expected = {"schema_version", "action", "target_sha", "request_id"}',
            self.text,
        )
        self.assertIn('"v5-production-release-1"', self.text)
        self.assertIn("set(request) != expected", self.text)
        self.assertIn(
            "target SHA must be exactly 40 lowercase hex characters",
            self.text,
        )

    def test_request_commit_is_one_file_on_exact_target_parent(self):
        self.assertIn('["git", "rev-parse", "HEAD^"]', self.text)
        self.assertIn("parent != target", self.text)
        self.assertIn(
            '["git", "diff", "--name-only", "HEAD^", "HEAD"]',
            self.text,
        )
        self.assertIn('changed != [".release-request.json"]', self.text)
        self.assertIn("persist-credentials: false", self.text)

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

    def test_only_declared_target_is_checked_out_for_validation(self):
        self.assertIn(
            "ref: ${{ steps.release.outputs.target_sha }}",
            self.text,
        )
        self.assertNotIn("github.event.pull_request.head.sha", self.text)


if __name__ == "__main__":
    unittest.main()
