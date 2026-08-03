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

    def test_obsolete_date_bound_paid_acceptance_is_absent(self) -> None:
        obsolete = ROOT / ".github/workflows/v5-final-paid-claude-acceptance-20260803.yml"
        self.assertFalse(obsolete.exists())


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

    def test_one_time_diagnostics_and_stale_evidence_are_absent(self) -> None:
        forbidden = (
            ROOT / ".github/workflows/v5-governance-endpoint-diagnostic-20260803.yml",
            ROOT / ".github/workflows/v5-live-catalog-diagnostic-20260803.yml",
            MODULE / "v5_preflight_simulation.py",
            ROOT / "tests/test_v5_preflight_simulation.py",
            MODULE / "live-recovery-budget-fix-evidence.json",
            MODULE / "v4-contract-isolation-fix-evidence.json",
            MODULE / "v5-closed-world-numeric-prompt-fix-evidence.json",
            MODULE / "v5-deterministic-answer-normalization-fix-evidence.json",
            MODULE / "v5_live_benchmark_suite.json",
        )
        existing = [str(path.relative_to(ROOT)) for path in forbidden if path.exists()]
        self.assertEqual([], existing)

    def test_provider_lock_has_one_authoritative_implementation(self) -> None:
        definitions = []
        for path in MODULE.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "def canonical_provider_lock(" in text:
                definitions.append(path.name)
            self.assertNotIn("def _canonical_provider_lock(", text, path.name)
        self.assertEqual(["v5_provider_lock.py"], definitions)

    def test_native_auditor_contains_no_r8_compatibility_runtime(self) -> None:
        for name in (
            "v5_execution_auditor.py",
            "v5_execution_auditor_integrity.py",
        ):
            text = (MODULE / name).read_text(encoding="utf-8")
            self.assertNotIn("v5-r8", text, name)
            self.assertNotIn("R8 fault-aware", text, name)

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
