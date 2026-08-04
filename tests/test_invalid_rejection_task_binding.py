from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "invalid-ticket-rejection.yml"
).read_text(encoding="utf-8")


class InvalidRejectionTaskBindingTests(unittest.TestCase):
    def test_rejection_is_bound_to_exact_command_task_id(self) -> None:
        required = (
            "Publish one task-bound trusted zero-call rejection",
            "unable to derive task identity from expert command",
            "- Task ID: \\`${taskId}\\`",
            "comment.body?.includes(taskLine)",
            "## EXECUTION_REJECTED",
        )
        missing = [item for item in required if item not in WORKFLOW]
        self.assertEqual(missing, [])

    def test_rejection_workflow_has_no_model_secret(self) -> None:
        self.assertNotIn("secrets.OPENROUTER_API_KEY", WORKFLOW)
        self.assertNotIn("secrets.ANTHROPIC_API_KEY", WORKFLOW)
        self.assertNotIn("secrets.OPENAI_API_KEY", WORKFLOW)
        self.assertIn('test -z "${OPENROUTER_API_KEY:-}"', WORKFLOW)

    def test_rejection_retry_is_bounded_by_comment_identity(self) -> None:
        self.assertIn("alreadyRejected", WORKFLOW)
        self.assertIn("per_page: 100", WORKFLOW)
        self.assertEqual(WORKFLOW.count("issues.createComment"), 1)


if __name__ == "__main__":
    unittest.main()
