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

import v5_executor as executor  # noqa: E402
import v5_issue_ticket as ticket  # noqa: E402
import v5_production_hardening  # noqa: E402


class V5P0GovernanceTests(unittest.TestCase):
    def test_global_total_call_cap_includes_recovery(self):
        v5_production_hardening.install()
        budget = executor.ExecutionBudget(
            max_planned_calls=16,
            max_retries=0,
            max_replacements=2,
            max_budget_usd=None,
        )
        self.assertEqual(budget.maximum_total_calls, 16)
        self.assertEqual(budget.maximum_initial_calls, 14)
        for index in range(14):
            allowed, reason = budget.reserve("initial", 0.0, f"n{index}")
            self.assertTrue(allowed, reason)
        allowed, reason = budget.reserve("initial", 0.0, "n14")
        self.assertFalse(allowed)
        self.assertEqual(reason, "initial-call-cap-reserved-for-recovery")
        self.assertTrue(budget.reserve("replacement", 0.0, "r1")[0])
        self.assertTrue(budget.reserve("replacement", 0.0, "r2")[0])
        self.assertFalse(budget.reserve("replacement", 0.0, "r3")[0])
        snapshot = budget.snapshot()
        self.assertEqual(snapshot["calls_reserved"], 16)
        self.assertEqual(snapshot["maximum_total_calls"], 16)

    def _prepare(self, approved_budget):
        packet = {
            "task_id": "p0-budget-001",
            "route": "expert-team",
            "task": {"question": "审计一个自包含的软件治理方案。"},
            "approved_budget": approved_budget,
            "quality_tier": "value",
            "private_output": False,
        }
        with tempfile.TemporaryDirectory() as folder:
            args = argparse.Namespace(
                event_path=None,
                issue_title="[execution] P0 budget contract",
                issue_body=json.dumps(packet, ensure_ascii=False),
                issue_number=101,
                actor="owner",
                author_association="OWNER",
                comment_body="",
                output_dir=folder,
            )
            with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner"}, clear=False), \
                 mock.patch.object(ticket.hardened.base, "duplicate_reason", return_value=""):
                ticket.prepare(args)
            return json.loads((Path(folder) / "ticket-status.json").read_text(encoding="utf-8"))

    def test_ticket_budget_is_not_silently_rewritten(self):
        status = self._prepare({
            "calls": 8,
            "maximum_recovery_calls": 2,
            "cost_policy": "unbounded_with_anomaly_guard",
            "cost_anomaly_usd": 1.5,
        })
        self.assertTrue(status["accepted"], status.get("reason"))
        self.assertEqual(status["calls"], 8)
        self.assertEqual(status["maximum_recovery_calls"], 2)
        self.assertEqual(status["maximum_initial_calls"], 6)
        self.assertEqual(status["cost_anomaly_usd"], 1.5)

    def test_ticket_rejects_recovery_outside_total(self):
        status = self._prepare({
            "calls": 4,
            "maximum_recovery_calls": 4,
            "cost_policy": "unbounded_with_anomaly_guard",
        })
        self.assertFalse(status["accepted"])
        self.assertIn("leave at least one initial call", status["reason"])

    def test_workflow_has_serialized_admission_and_execution(self):
        text = (ROOT / ".github" / "workflows" / "execution-ticket.yml").read_text(encoding="utf-8")
        self.assertIn("group: expert-production-admission", text)
        self.assertIn("group: expert-production-global", text)
        self.assertIn("v5_admission_lock.py", text)
        self.assertIn("EXECUTION_BUSY", text)
        self.assertNotIn('TOTAL_MODEL_CALLS: "16"', text)
        self.assertIn("--maximum-total-calls", text)
        self.assertIn("--maximum-recovery-calls", text)

    def test_final_attestation_follows_primary_artifact_and_final_status(self):
        text = (ROOT / ".github" / "workflows" / "execution-ticket.yml").read_text(encoding="utf-8")
        primary = text.index("name: Upload primary ticket artifacts")
        final = text.index("name: Render authoritative V5 final status")
        attest = text.index("name: Generate post-upload final attestation")
        proof = text.index("name: Upload final attestation artifact")
        publish = text.index("name: Publish authoritative V5 final status")
        self.assertLess(primary, final)
        self.assertLess(final, attest)
        self.assertLess(attest, proof)
        self.assertLess(proof, publish)
        self.assertIn("v5_final_attestation.py", text)

    def test_stale_v3_contract_and_paths_are_removed(self):
        schema = (MARKET / "execution-ticket.schema.json").read_text(encoding="utf-8")
        config = (MARKET / "config.json").read_text(encoding="utf-8")
        report = (MARKET / "publish_report.py").read_text(encoding="utf-8")
        self.assertNotIn("a15280020511/test", schema)
        self.assertNotIn("fixed 3+1", schema)
        self.assertNotIn("max_cost_usd", schema)
        self.assertNotIn("resource_requirements.py", config)
        self.assertNotIn("resource_plan_optimizer.py", config)
        self.assertNotIn("resource_runtime_compat.py", config)
        self.assertNotIn("三名专家原始回答", report)


if __name__ == "__main__":
    unittest.main()
