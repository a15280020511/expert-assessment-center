import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import final_status  # noqa: E402
import reasoning_policy  # noqa: E402


class UnboundedInferenceTests(unittest.TestCase):
    def test_apply_plan_removes_all_request_token_ceilings(self):
        model = SimpleNamespace(
            supported_parameters=["max_tokens", "max_completion_tokens", "reasoning", "verbosity", "temperature"],
            reasoning={"supports_max_tokens": True},
        )
        plan = reasoning_policy.InferencePlan(
            effort="low",
            max_tokens=100000,
            reasoning_tokens=0,
            temperature=0.06,
            reasoning_supported=True,
            external_tools_allowed=False,
            rationale=(),
        )
        payload = {
            "max_tokens": 10000,
            "max_completion_tokens": 10000,
            "reasoning": {"max_tokens": 1000},
        }
        reasoning_policy.apply_plan(payload, plan, model)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("max_completion_tokens", payload)
        self.assertEqual(payload["reasoning"], {"exclude": True, "effort": "low"})
        self.assertEqual(payload["verbosity"], "low")


class FinalStatusTests(unittest.TestCase):
    def _render(self, status):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ticket-status.json").write_text(
                json.dumps({"task_id": "status-task", "task_fingerprint": "abc", "calls": 6}),
                encoding="utf-8",
            )
            (root / "execution-diagnosis.json").write_text(
                json.dumps({"status": status, "failures": [], "degradations": ["judge report is partial"] if status == "DEGRADED" else [], "primary_failure": {"code": "NONE"}}),
                encoding="utf-8",
            )
            (root / "call-ledger.json").write_text(
                json.dumps({"summary": {"call_count": 4, "cost_evidence_status": "known", "provider_actual_cost_usd": 0.1, "conservative_cost_usd": 0.1}}),
                encoding="utf-8",
            )
            args = [
                "final_status.py",
                "--output-dir", temp,
                "--audit-outcome", "success",
                "--manifest-outcome", "success",
                "--ticket-upload-outcome", "success",
                "--state-upload-outcome", "success",
            ]
            old = sys.argv
            sys.argv = args
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    final_status.main()
                return output.getvalue()
            finally:
                sys.argv = old

    def test_degraded_has_distinct_heading(self):
        text = self._render("DEGRADED")
        self.assertTrue(text.startswith("## EXECUTION_DEGRADED"))
        self.assertIn("不得表述为完整正常PASS", text)
        self.assertIn("Hard monetary ceiling: `none`", text)

    def test_pass_has_completed_heading(self):
        text = self._render("PASS")
        self.assertTrue(text.startswith("## EXECUTION_COMPLETED"))


if __name__ == "__main__":
    unittest.main()
