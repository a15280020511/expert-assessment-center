import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_issue_ticket as issue_ticket  # noqa: E402


class PrivateOutputMessageTests(unittest.TestCase):
    def test_true_private_output_has_only_the_accurate_rejection_reason(self):
        payload = {
            "task_id": "private-message-0001",
            "route": "expert-team",
            "task": {"question": "test"},
            "approved_budget": {
                "calls": 4,
                "maximum_recovery_calls": 0,
                "cost_policy": "unbounded_with_anomaly_guard",
            },
            "private_output": True,
        }
        with tempfile.TemporaryDirectory() as temp:
            args = argparse.Namespace(
                event_path=None,
                issue_title="[execution] private",
                issue_body=json.dumps(payload),
                issue_number=20,
                actor="owner",
                author_association="OWNER",
                comment_body="/run-expert-team private-message-0001",
                output_dir=temp,
            )
            with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner"}, clear=False), \
                 mock.patch.object(issue_ticket, "duplicate_reason", return_value=""):
                issue_ticket.prepare(args)
            status = json.loads((Path(temp) / "ticket-status.json").read_text(encoding="utf-8"))
        self.assertFalse(status["accepted"])
        self.assertNotIn("must be boolean", status["reason"])
        self.assertIn("no private delivery channel", status["reason"])


if __name__ == "__main__":
    unittest.main()
