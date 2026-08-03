import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))
import v5_issue_ticket as issue_ticket  # noqa: E402


def packet(task_id="unique-task-0001", question="分析一个独特问题", calls=6, recovery=2, anomaly=1.0):
    budget = {
        "calls": calls,
        "maximum_recovery_calls": recovery,
        "cost_policy": "unbounded_with_anomaly_guard",
    }
    if anomaly is not None:
        budget["cost_anomaly_usd"] = anomaly
    return {
        "task_id": task_id,
        "route": "expert-team",
        "task": {"question": question, "requirements": ["中文"]},
        "approved_budget": budget,
    }


class TicketSecurityTests(unittest.TestCase):
    def prepare(self, payload, *, actor="owner", association="OWNER", comment_body=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        args = argparse.Namespace(
            event_path=None,
            issue_title="[execution] test",
            issue_body=json.dumps(payload),
            issue_number=20,
            actor=actor,
            author_association=association,
            comment_body=(comment_body if comment_body is not None else f"/run-expert-team {payload.get('task_id', 'invalid-task')}"),
            output_dir=temp.name,
        )
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner"}, clear=False), \
             mock.patch.object(issue_ticket, "duplicate_reason", return_value=""):
            issue_ticket.prepare(args)
        status = json.loads((Path(temp.name) / "ticket-status.json").read_text())
        task_text = (Path(temp.name) / "task.txt").read_text() if (Path(temp.name) / "task.txt").exists() else ""
        return status, task_text

    def test_only_owner_can_trigger(self):
        status, _ = self.prepare(packet(), actor="outsider", association="NONE")
        self.assertFalse(status["accepted"])
        self.assertIn("only the repository owner", status["reason"])

    def test_task_id_is_required_and_strict(self):
        status, _ = self.prepare(packet(task_id="bad"))
        self.assertFalse(status["accepted"])
        self.assertIn("task_id", status["reason"])

    def test_valid_ticket_is_accepted_and_contains_delegation_boundary(self):
        status, task_text = self.prepare(packet())
        self.assertTrue(status["accepted"])
        self.assertEqual(status["analysis_owner"], "github-v5-gpt-claude-expert-graph")
        self.assertIn("GPT latest直接拆解并提出专家图", task_text)
        self.assertIn("Claude Opus latest只执行一次红队审查", task_text)
        self.assertIn("不得在专家结果产生前替代专家分析", task_text)

    def test_multiple_schema_errors_are_reported_together(self):
        payload = {
            "task_id": "valid-task-0001",
            "route": "wrong",
            "task": {"requirements": "not-an-array", "instructions": "unsupported"},
            "evidence": "raw string is forbidden",
            "approved_budget": {"calls": 1, "max_rounds": 2},
        }
        status, _ = self.prepare(payload)
        self.assertFalse(status["accepted"])
        self.assertIn("route must be expert-team", status["reason"])
        self.assertIn("Unknown task fields", status["reason"])
        self.assertIn("task.question is required", status["reason"])
        self.assertIn("task.requirements must be an array", status["reason"])
        self.assertIn("evidence must be an object or an array", status["reason"])
        self.assertIn("approved_budget", status["reason"])
        self.assertIn("maximum_recovery_calls is required", status["reason"])
        self.assertGreaterEqual(len(status["errors"]), 8)

        raw_messages = "; ".join(
            error.message for error in issue_ticket.TICKET_VALIDATOR.iter_errors(payload)
        )
        self.assertIn("cost_policy", raw_messages)

    def test_evidence_array_is_rendered_and_string_is_rejected(self):
        payload = packet()
        payload["evidence"] = [
            {
                "source_level": "A",
                "source": "Official source",
                "url": "https://example.com",
                "note": "Supplied evidence text",
            }
        ]
        status, task_text = self.prepare(payload)
        self.assertTrue(status["accepted"])
        self.assertIn("级别=A", task_text)
        self.assertIn("Supplied evidence text", task_text)

        payload["evidence"] = "silent loss must not happen"
        status, _ = self.prepare(payload)
        self.assertFalse(status["accepted"])
        self.assertIn("evidence must be an object or an array", status["reason"])

    def test_duplicate_task_is_rejected(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        args = argparse.Namespace(
            event_path=None,
            issue_title="[execution] test",
            issue_body=json.dumps(packet()),
            issue_number=20,
            actor="owner",
            author_association="OWNER",
            comment_body="/run-expert-team unique-task-0001",
            output_dir=temp.name,
        )
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner"}, clear=False), \
             mock.patch.object(issue_ticket, "duplicate_reason", return_value="duplicate task fingerprint; previously submitted in Issue #14"):
            issue_ticket.prepare(args)
        status = json.loads((Path(temp.name) / "ticket-status.json").read_text())
        self.assertFalse(status["accepted"])
        self.assertIn("duplicate task", status["reason"])

    def test_nan_and_excessive_calls_rejected(self):
        payload = packet()
        payload["approved_budget"]["cost_anomaly_usd"] = float("nan")
        body = json.dumps(payload, allow_nan=True)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        args = argparse.Namespace(
            event_path=None,
            issue_title="[execution] test",
            issue_body=body,
            issue_number=20,
            actor="owner",
            author_association="OWNER",
            comment_body="/run-expert-team unique-task-0001",
            output_dir=temp.name,
        )
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner"}, clear=False):
            issue_ticket.prepare(args)
        status = json.loads((Path(temp.name) / "ticket-status.json").read_text())
        self.assertFalse(status["accepted"])
        self.assertIn("Non-finite", status["reason"])
        status, _ = self.prepare(packet(calls=17, recovery=2))
        self.assertFalse(status["accepted"])

    def test_same_semantic_task_has_same_fingerprint(self):
        one = packet(task_id="unique-task-0001")
        two = packet(task_id="unique-task-0002")
        self.assertEqual(issue_ticket.task_fingerprint(one), issue_ticket.task_fingerprint(two))

    def test_fingerprint_ignores_evidence_requirements_order_and_punctuation(self):
        one = packet(task_id="unique-task-0001", question="分析：同一个问题！")
        one["objective"] = "核验 事实"
        one["task"]["requirements"] = ["中文", "给出风险"]
        one["evidence"] = [{"source": "A", "url": "https://one.example"}]
        two = packet(task_id="unique-task-0002", question="分析 同一个问题")
        two["objective"] = "核验　事实"
        two["task"]["requirements"] = ["给出风险", "中文"]
        two["evidence"] = [{"source": "B", "url": "https://two.example", "note": "不同备注"}]
        self.assertEqual(issue_ticket.task_fingerprint(one), issue_ticket.task_fingerprint(two))

    def test_prior_accepted_issue_blocks_duplicate_new_issue(self):
        prior = packet(task_id="prior-task-0001", question="不要重复提交这个任务")
        rows = [{"number": 19, "title": "[execution] prior", "body": json.dumps(prior)}]
        current = packet(task_id="new-task-0002", question="不要重复提交这个任务")
        fingerprint = issue_ticket.task_fingerprint(current)

        def comments(_repo, number):
            return ["## EXECUTION_ACCEPTED"] if number == 19 else []

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "x"}, clear=False), \
             mock.patch.object(issue_ticket, "_issue_comments", side_effect=comments), \
             mock.patch.object(issue_ticket, "_api_json", return_value=rows):
            reason = issue_ticket.duplicate_reason(
                "owner/repo", 20, current["task_id"], fingerprint, is_retry=False, retry_id=""
            )
        self.assertIn("previously submitted in Issue #19", reason)

    def test_prior_rejected_only_issue_does_not_poison_fingerprint(self):
        prior = packet(task_id="prior-task-0001", question="格式修好以后允许重提")
        rows = [{"number": 19, "title": "[execution] prior", "body": json.dumps(prior)}]
        current = packet(task_id="new-task-0002", question="格式修好以后允许重提")
        fingerprint = issue_ticket.task_fingerprint(current)

        def comments(_repo, number):
            return ["## EXECUTION_REJECTED"] if number == 19 else []

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "x"}, clear=False), \
             mock.patch.object(issue_ticket, "_issue_comments", side_effect=comments), \
             mock.patch.object(issue_ticket, "_api_json", return_value=rows):
            reason = issue_ticket.duplicate_reason(
                "owner/repo", 20, current["task_id"], fingerprint, is_retry=False, retry_id=""
            )
        self.assertEqual(reason, "")

    def test_failed_issue_allows_unique_controlled_retry(self):
        comments = [
            "## EXECUTION_ACCEPTED",
            "## EXECUTION_FAILED",
        ]
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "x"}, clear=False), \
             mock.patch.object(issue_ticket, "_issue_comments", return_value=comments), \
             mock.patch.object(issue_ticket, "_api_json", return_value=[]):
            reason = issue_ticket.duplicate_reason(
                "owner/repo", 20, "unique-task-0001", "fingerprint", is_retry=True, retry_id="retry-0001"
            )
        self.assertEqual(reason, "")

    def test_rejected_issue_allows_body_fix_and_controlled_retry(self):
        comments = ["## EXECUTION_REJECTED"]
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "x"}, clear=False), \
             mock.patch.object(issue_ticket, "_issue_comments", return_value=comments), \
             mock.patch.object(issue_ticket, "_api_json", return_value=[]):
            reason = issue_ticket.duplicate_reason(
                "owner/repo", 20, "unique-task-0001", "fingerprint", is_retry=True, retry_id="retry-0001"
            )
        self.assertEqual(reason, "")

        status, _ = self.prepare(packet(), comment_body="/retry-expert-team retry-0001")
        self.assertTrue(status["accepted"])
        self.assertTrue(status["is_retry"])
        self.assertEqual(status["retry_id"], "retry-0001")

    def test_completed_or_reused_retry_is_rejected(self):
        completed = ["## EXECUTION_ACCEPTED", "## EXECUTION_COMPLETED"]
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "x"}, clear=False), \
             mock.patch.object(issue_ticket, "_issue_comments", return_value=completed):
            reason = issue_ticket.duplicate_reason(
                "owner/repo", 20, "unique-task-0001", "fingerprint", is_retry=True, retry_id="retry-0001"
            )
        self.assertIn("already completed", reason)

        reused = [
            "## EXECUTION_ACCEPTED",
            "## EXECUTION_FAILED",
            "## EXECUTION_RETRY_ACCEPTED\nRETRY_ID: `retry-0001`",
        ]
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "x"}, clear=False), \
             mock.patch.object(issue_ticket, "_issue_comments", return_value=reused):
            reason = issue_ticket.duplicate_reason(
                "owner/repo", 20, "unique-task-0001", "fingerprint", is_retry=True, retry_id="retry-0001"
            )
        self.assertIn("already used", reason)

    def test_retry_command_requires_unique_id(self):
        status, _ = self.prepare(packet(), comment_body="/retry-expert-team")
        self.assertFalse(status["accepted"])
        self.assertIn("retry command must be", status["reason"])


if __name__ == "__main__":
    unittest.main()
