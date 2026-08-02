from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "open-model-market"
if str(MODULE) not in sys.path:
    sys.path.insert(0, str(MODULE))

from text_normalization import normalize_heading_key  # noqa: E402


class CompleteCleanupRegressionTests(unittest.TestCase):
    def test_quality_tier_is_not_an_external_contract(self) -> None:
        schema = json.loads((MODULE / "execution-ticket.schema.json").read_text())
        self.assertNotIn("quality_tier", schema["properties"])
        paths = [
            MODULE / "v5_issue_ticket.py",
            MODULE / "v5_production_ticket.py",
            MODULE / "v5_ticket_gate.py",
            ROOT / ".github/workflows/execution-ticket.yml",
        ]
        for path in paths:
            self.assertNotIn("quality_tier", path.read_text(), path)
        pipeline = (MODULE / "v5_pipeline.py").read_text()
        self.assertNotIn('parser.add_argument("--quality-tier"', pipeline)

    def test_paid_acceptance_is_explicit_and_not_a_green_noop(self) -> None:
        dead = ROOT / ".github/workflows/v5-one-time-paid-claude-acceptance-20260803.yml"
        self.assertFalse(dead.exists())
        paid = (ROOT / ".github/workflows/v5-final-paid-claude-acceptance-20260803.yml").read_text()
        self.assertIn("workflow_dispatch", paid)
        self.assertIn("inputs.confirm", paid)
        self.assertNotIn("hashFiles(", paid)
        self.assertNotIn("pull_request", paid)

    def test_heading_normalization_has_one_authoritative_implementation(self) -> None:
        self.assertEqual("最终_建议", normalize_heading_key("2. **最终 建议**"))
        source = (MODULE / "text_normalization.py").read_text()
        self.assertEqual(1, source.count("def normalize_heading_key"))
        for name in (
            "v5_deterministic_answer_normalization.py",
            "v5_runtime.py",
            "v5_task_delivery_contract_impl.py",
        ):
            text = (MODULE / name).read_text()
            self.assertIn("normalize_heading_key", text)
            self.assertNotIn('re.sub(r"[`*_~]"', text)

    def test_legacy_local_planning_stack_remains_absent(self) -> None:
        forbidden = (
            "v5_value_optimizer.py",
            "v5_planner.py",
            "v5_planning_runtime.py",
            "v5_constitutional_pipeline.py",
            "v5_cross_endpoint_planner.py",
            "v5_cross_endpoint_planner_impl.py",
            "v5_operational_resilience.py",
            "v5_general_task_planning.py",
            "task_semantic_compiler.py",
            "resource_matrix.py",
            "atomic_work_graph.py",
            "team_policy.json",
        )
        for name in forbidden:
            self.assertFalse((MODULE / name).exists(), name)

    def test_quality_tier_and_obsolete_fixed_team_assets_are_absent(self) -> None:
        runtime = (MODULE / "v5_runtime.py").read_text(encoding="utf-8")
        pipeline = (MODULE / "v5_pipeline.py").read_text(encoding="utf-8")
        capabilities = (MODULE / "expert-team-capabilities.json").read_text(encoding="utf-8")
        self.assertNotIn("quality_tier", runtime)
        self.assertNotIn("quality_tier", pipeline)
        self.assertNotIn("quality_tier", capabilities)
        self.assertFalse((MODULE / "parameter_templates.json").exists())
        self.assertFalse((MODULE / "model-reliability-ledger.schema.json").exists())
        self.assertFalse(
            (ROOT / ".github/workflows/v5-one-time-paid-acceptance.yml").exists()
        )


if __name__ == "__main__":
    unittest.main()
