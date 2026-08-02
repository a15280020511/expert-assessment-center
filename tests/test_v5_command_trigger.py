import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

import v5_issue_ticket as ticket  # noqa: E402


class V5CommandTriggerTests(unittest.TestCase):
    TASK_ID = "command-test-001"

    def prepare(self, comment_body: str):
        packet = {
            "task_id": self.TASK_ID,
            "route": "expert-team",
            "task": {"question": "验证唯一评论命令触发是否确定。"},
            "approved_budget": {
                "calls": 5,
                "maximum_recovery_calls": 1,
                "cost_policy": "unbounded_with_anomaly_guard",
                "cost_anomaly_usd": 0.20,
            },
            "private_output": False,
        }
        with tempfile.TemporaryDirectory() as folder:
            args = argparse.Namespace(
                event_path=None,
                issue_title="[execution] command trigger contract",
                issue_body=json.dumps(packet, ensure_ascii=False),
                issue_number=202,
                actor="owner",
                author_association="OWNER",
                comment_body=comment_body,
                output_dir=folder,
            )
            with mock.patch.dict(
                os.environ,
                {"GITHUB_REPOSITORY_OWNER": "owner"},
                clear=False,
            ), mock.patch.object(
                ticket,
                "duplicate_reason",
                return_value="",
            ):
                ticket.prepare(args)
            return json.loads(
                (Path(folder) / "ticket-status.json").read_text(encoding="utf-8")
            )

    def test_matching_run_command_is_accepted(self):
        status = self.prepare(f"/run-expert-team {self.TASK_ID}")
        self.assertTrue(status["accepted"], status.get("reason"))
        self.assertEqual(status["trigger_mode"], "run")
        self.assertEqual(status["execution_id"], self.TASK_ID)
        self.assertEqual(status["authoritative_trigger"], "issue_comment.created")
        self.assertEqual(status["runtime_version"], "v5-native-runtime-1")
        self.assertFalse(status["cross_task_history_used"])

    def test_missing_command_is_rejected_before_execution(self):
        status = self.prepare("")
        self.assertFalse(status["accepted"])
        self.assertIn("execution requires one explicit comment command", status["reason"])
        self.assertEqual(status["trigger_mode"], "invalid")

    def test_run_id_must_equal_ticket_task_id(self):
        status = self.prepare("/run-expert-team different-id-001")
        self.assertFalse(status["accepted"])
        self.assertIn(
            "run execution_id must exactly equal ticket task_id",
            status["reason"],
        )

    def test_malformed_run_command_is_rejected(self):
        status = self.prepare("/run-expert-team")
        self.assertFalse(status["accepted"])
        self.assertIn(
            "run command must be: /run-expert-team <ticket_task_id>",
            status["reason"],
        )

    def test_unrelated_comment_is_rejected(self):
        status = self.prepare("continue")
        self.assertFalse(status["accepted"])
        self.assertIn("execution requires one explicit comment command", status["reason"])


if __name__ == "__main__":
    unittest.main()
