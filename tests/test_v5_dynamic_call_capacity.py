from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

import v5_dynamic_pipeline_core as dynamic_core  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "governance-ticket.json"
WORKFLOW = ROOT / ".github" / "workflows" / "execution-ticket.yml"


class DynamicCallCapacityTests(unittest.TestCase):
    def test_legacy_cli_call_fields_do_not_control_active_capacity(self) -> None:
        ticket = json.loads(FIXTURE.read_text(encoding="utf-8"))
        plan = dynamic_core.pipeline.validate_governance_model_plan(ticket)
        standby = plan.get("expert_center_ordered_standby")
        standby_count = len(standby) if isinstance(standby, list) else int(
            plan.get("expert_center_ordered_standby_count") or 0
        )
        expected = (
            int(plan["expert_count"])
            + int(plan["recovery_count"])
            + standby_count,
            int(plan["recovery_count"]),
        )
        args = SimpleNamespace(
            governance_plan_file=str(FIXTURE),
            output_dir=str(FIXTURE.parent),
            maximum_total_calls=1,
            maximum_recovery_calls=0,
            cost_anomaly_usd=None,
            max_completion_tokens=None,
        )
        self.assertEqual(expected, dynamic_core._dynamic_validate_budget(args))  # noqa: SLF001
        self.assertNotEqual(
            (args.maximum_total_calls, args.maximum_recovery_calls),
            expected,
        )

    def test_production_workflow_does_not_forward_ticket_call_ceilings(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("--maximum-total-calls", workflow)
        self.assertNotIn("--maximum-recovery-calls", workflow)
        self.assertNotIn("jq -r '.calls'", workflow)
        self.assertNotIn("jq -r '.maximum_recovery_calls'", workflow)


if __name__ == "__main__":
    unittest.main()
