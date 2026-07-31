import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote-v5-production.yml"
VALIDATE_WORKFLOW = ROOT / ".github" / "workflows" / "validate.yml"
RUFF_CONFIG = ROOT / "ruff.toml"


class TestV5ReleaseCommandContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.validate_text = VALIDATE_WORKFLOW.read_text(encoding="utf-8")
        cls.ruff_text = RUFF_CONFIG.read_text(encoding="utf-8")

    def test_manual_dispatch_remains_available(self):
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("target_sha:", self.text)
        self.assertIn("- promote\n          - rollback", self.text)

    def test_release_pr_is_owner_only_same_repo_and_fixed_head(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn("branches: [main]", self.text)
        self.assertIn("paths: [.release-request.json]", self.text)
        self.assertIn("github.actor == github.repository_owner", self.text)
        self.assertIn(
            "startsWith(github.event.pull_request.title, '[release]')",
            self.text,
        )
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )
        self.assertIn(
            "github.event.pull_request.head.ref == 'v5-production-release'",
            self.text,
        )
        self.assertNotIn("pull_request_target:", self.text)
        self.assertNotIn("issue_comment:", self.text)

    def test_request_checkout_is_isolated_and_has_no_persisted_credentials(self):
        self.assertIn("name: Checkout isolated release request", self.text)
        self.assertIn("path: request-source", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha }}",
            self.text,
        )

    def test_request_parser_requires_exact_schema_action_and_full_sha(self):
        self.assertIn('request="request-source/.release-request.json"', self.text)
        self.assertIn(
            '(keys | sort) == ["action", "request_id", "schema_version", "target_sha"]',
            self.text,
        )
        self.assertIn('.schema_version == "v5-production-release-1"', self.text)
        self.assertIn('[[ "$target" =~ ^[0-9a-f]{40}$ ]]', self.text)
        self.assertIn("jq -e", self.text)

    def test_request_commit_is_one_file_on_exact_target_parent(self):
        self.assertIn("git -C request-source rev-parse HEAD^", self.text)
        self.assertIn('test "$parent" = "$target"', self.text)
        self.assertIn(
            "git -C request-source diff --name-only HEAD^ HEAD",
            self.text,
        )
        self.assertIn('test "$changed" = ".release-request.json"', self.text)

    def test_release_validation_is_split_without_lowering_the_gate(self):
        ruff = self.text.index("name: Run canonical Ruff checks")
        compile_sources = self.text.index("name: Compile canonical Python tree")
        unit = self.text.index("name: Run canonical full unit test suite")
        enforce = self.text.index("name: Enforce canonical validation gate")
        dry = self.text.index("name: Run deterministic no-call V5 regression")
        move = self.text.index("name: Move production ref")

        self.assertLess(ruff, compile_sources)
        self.assertLess(compile_sources, unit)
        self.assertLess(unit, enforce)
        self.assertLess(enforce, dry)
        self.assertLess(dry, move)
        self.assertNotIn("name: Run static and full unit validation", self.text)
        self.assertIn("python -m ruff check .", self.text)
        self.assertIn(
            "python -m compileall -q open-model-market tests tools",
            self.text,
        )
        self.assertIn(
            "python -m unittest discover -s tests -p 'test*.py' -v",
            self.text,
        )
        self.assertIn("--maximum-total-calls 4", self.text)
        self.assertIn("--maximum-recovery-calls 1", self.text)
        self.assertNotIn("OPENROUTER_API_KEY", self.text)

    def test_pull_request_and_release_use_identical_validation_commands(self):
        commands = (
            "python -m ruff check .",
            "python -m compileall -q open-model-market tests tools",
            "python -m unittest discover -s tests -p 'test*.py' -v",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.text.count(command), 1)
                self.assertEqual(self.validate_text.count(command), 1)

        self.assertNotIn("--select E9,F63,F7,F82", self.validate_text)
        self.assertIn('python-version: "3.12"', self.text)
        self.assertIn('python-version: "3.12"', self.validate_text)
        self.assertIn("ruff==0.16.0", (ROOT / "requirements-dev.txt").read_text())
        self.assertIn('select = ["E4", "E7", "E9", "F"]', self.ruff_text)

    def test_release_reports_all_validation_results_before_enforcement(self):
        self.assertIn("id: ruff", self.text)
        self.assertIn("id: compile", self.text)
        self.assertIn("id: unit", self.text)
        self.assertIn(
            "if: always() && steps.dependencies.outcome == 'success'",
            self.text,
        )
        self.assertIn("RUFF_OUTCOME: ${{ steps.ruff.outcome }}", self.text)
        self.assertIn("COMPILE_OUTCOME: ${{ steps.compile.outcome }}", self.text)
        self.assertIn("UNIT_OUTCOME: ${{ steps.unit.outcome }}", self.text)
        self.assertIn('test "$RUFF_OUTCOME" = "success"', self.text)
        self.assertIn('test "$COMPILE_OUTCOME" = "success"', self.text)
        self.assertIn('test "$UNIT_OUTCOME" = "success"', self.text)

    def test_validation_diagnostics_are_always_retained(self):
        self.assertIn("name: Upload release validation diagnostics", self.text)
        self.assertIn("if: always()", self.text)
        self.assertIn("release-validation-logs/ruff.log", self.text)
        self.assertIn("release-validation-logs/compileall.log", self.text)
        self.assertIn("release-validation-logs/unit-test.log", self.text)
        self.assertIn("retention-days: 14", self.text)
        self.assertIn("validation-logs/ruff.log", self.validate_text)
        self.assertIn("validation-logs/compileall.log", self.validate_text)
        self.assertIn("validation-logs/unit-test.log", self.validate_text)

    def test_release_reuses_direction_and_rollback_guards(self):
        self.assertIn("git merge-base --is-ancestor", self.text)
        self.assertIn("--force-with-lease", self.text)
        self.assertIn("refs/heads/production", self.text)
        self.assertIn("group: v5-production-release", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_untrusted_request_code_is_never_executed(self):
        self.assertIn("working-directory: release-target", self.text)
        self.assertIn(
            "ref: ${{ steps.release.outputs.target_sha }}",
            self.text,
        )
        self.assertNotIn("working-directory: request-source", self.text)
        self.assertNotIn("python request-source", self.text)


if __name__ == "__main__":
    unittest.main()
