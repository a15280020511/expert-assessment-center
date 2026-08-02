from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one regex match, found {count}")
    return updated


# 6. Update old tests that encoded advisory-only recovery behavior.
path = "tests/test_v5_recovery_absolute_budget_feasibility.py"
text = read(path)
pattern = r'''    def test_candidate_within_absolute_cap_remains_available_for_live_ledger\(self\) -> None:\n.*?\n\n\nif __name__ == "__main__":'''
replacement = '''    def test_risk_adjusted_remaining_guard_excludes_unexecutable_candidate(self) -> None:\n        selected = candidate("node-selected", "google/selected", "google", 0.12)\n        recovery = candidate("node-openai", "openai/recovery", "openai", 0.25986871)\n        optimization = {\n            "selected_initial_cost_usd": 0.12,\n            "execution_graph": {\n                "nodes": [{**selected, "node_id": "node-selected"}],\n                "final_nodes": [],\n                "metadata": {"interpretation_id": "interpretation-budget"},\n            },\n        }\n        policy = CrossEndpointPlannerPolicy(\n            RuntimeConfig(2, 1, 0.35, "value")\n        )\n        with self.assertRaisesRegex(\n            V5PlanningError,\n            "Recovery reserve is not executable",\n        ):\n            policy.rebalance_recovery_pool(\n                optimization,\n                {"candidates": [selected, recovery]},\n            )\n\n    def test_risk_adjusted_candidate_within_remaining_guard_is_frozen(self) -> None:\n        selected = candidate("node-selected", "google/selected", "google", 0.12)\n        recovery = candidate("node-openai", "openai/recovery", "openai", 0.10)\n        optimization = {\n            "selected_initial_cost_usd": 0.12,\n            "execution_graph": {\n                "nodes": [{**selected, "node_id": "node-selected"}],\n                "final_nodes": [],\n                "metadata": {"interpretation_id": "interpretation-budget"},\n            },\n        }\n        policy = CrossEndpointPlannerPolicy(\n            RuntimeConfig(2, 1, 0.35, "value")\n        )\n        result = policy.rebalance_recovery_pool(\n            optimization,\n            {"candidates": [selected, recovery]},\n        )\n        row = result["execution_graph"]["metadata"]["recovery_pool"][\n            "node-selected"\n        ][0]\n        self.assertEqual("openai/recovery", row["model"])\n        self.assertFalse(row["planning_budget_advisory_only"])\n        self.assertLessEqual(row["recovery_risk_adjusted_cost_usd"], 0.23)\n        self.assertTrue(\n            result["recovery_pool_policy"][\n                "risk_adjusted_remaining_budget_enforced_at_planning"\n            ]\n        )\n\n\nif __name__ == "__main__":'''
text = regex_once(text, pattern, replacement, "update absolute budget tests")
write(path, text)

path = "tests/test_v5_critical_delivery_reliability.py"
text = read(path)
text = text.replace(
    "def test_critical_recovery_retains_rows_for_live_budget_admission(self)",
    "def test_critical_recovery_ranks_cost_effective_rows_within_guard(self)",
    1,
)
text = replace_once(
    text,
    '        self.assertEqual("z-ai/glm", models[0])\n',
    '        self.assertEqual("qwen/qwen-small", models[0])\n',
    "update critical first candidate",
)
text = replace_once(
    text,
    '        self.assertNotIn("qwen/qwen-small", models)\n',
    '        self.assertNotIn("qwen/qwen-plus", models)\n',
    "update company dedupe assertion",
)
text = replace_once(
    text,
    '''        self.assertTrue(\n            policy_evidence["planning_estimated_budget_advisory_only"]\n        )\n''',
    '''        self.assertFalse(\n            policy_evidence["planning_estimated_budget_advisory_only"]\n        )\n''',
    "update advisory assertion",
)
text = replace_once(
    text,
    '''        self.assertTrue(\n            policy_evidence[\n                "recovery_candidates_retained_for_live_ledger_admission"\n            ]\n        )\n''',
    '''        self.assertFalse(\n            policy_evidence[\n                "recovery_candidates_retained_for_live_ledger_admission"\n            ]\n        )\n''',
    "update retained assertion",
)
pattern = r'''    def test_v3_regression_live_ledger_can_admit_retained_recovery\(self\) -> None:\n.*?\n    def test_global_recovery_company_allocation_prioritizes_final_node'''
replacement = '''    def test_live_ledger_regression_is_blocked_at_planning_when_guard_is_insufficient(self) -> None:\n        config = RuntimeConfig(\n            total_call_limit=4,\n            recovery_call_limit=1,\n            cost_anomaly_usd=0.008,\n            quality_tier="value",\n            tools_allowed=False,\n            provider_lock_required=True,\n        )\n        policy = CrossEndpointPlannerPolicy(config)\n        selected_rows = [\n            candidate(\n                "node-qwen",\n                "qwen/qwen3.5-9b",\n                "siliconflow/fp8",\n                work_id="work-qwen",\n                functions=("analysis",),\n                cost=0.0017,\n                quality=0.70,\n                failure=0.03,\n            ),\n            candidate(\n                "node-deepseek",\n                "deepseek/deepseek-v4-flash",\n                "deepinfra/fp4",\n                work_id="work-final",\n                functions=("synthesis",),\n                cost=0.0022,\n                quality=0.76,\n                failure=0.02,\n            ),\n            candidate(\n                "node-openai",\n                "openai/gpt-oss-120b",\n                "groq/fp8",\n                work_id="work-openai",\n                functions=("analysis",),\n                cost=0.001897,\n                quality=0.74,\n                failure=0.02,\n            ),\n        ]\n        recovery = candidate(\n            "node-mistral-recovery",\n            "mistralai/mistral-small",\n            "mistral",\n            work_id="work-qwen",\n            functions=("analysis",),\n            cost=0.003,\n            quality=0.72,\n            failure=0.02,\n        )\n        optimization = {\n            "selected_initial_cost_usd": 0.005797,\n            "execution_graph": {\n                "nodes": [\n                    {**row, "node_id": row["candidate_id"]}\n                    for row in selected_rows\n                ],\n                "final_nodes": ["node-deepseek"],\n                "metadata": {\n                    "interpretation_id": "interpretation-critical"\n                },\n            },\n        }\n        with self.assertRaises(V5PlanningError):\n            policy.rebalance_recovery_pool(\n                optimization,\n                {"candidates": [*selected_rows, recovery]},\n            )\n\n    def test_global_recovery_company_allocation_prioritizes_final_node'''
text = regex_once(text, pattern, replacement, "replace live ledger regression")
write(path, text)


# 7. Add focused regressions for the production failure.
Path("tests/test_v5_recovery_guard_production_regression.py").write_text('''from __future__ import annotations\n\nimport sys\nimport unittest\nfrom pathlib import Path\nfrom types import SimpleNamespace\n\nROOT = Path(__file__).resolve().parents[1]\nsys.path.insert(0, str(ROOT / "open-model-market"))\n\nfrom execution_graph import ExecutionGraph  # noqa: E402\nfrom v5_cross_endpoint_planner import CrossEndpointPlannerPolicy  # noqa: E402\nfrom v5_operational_resilience import contract_visible_token_floor  # noqa: E402\nfrom v5_runtime import BudgetController, RuntimeConfig  # noqa: E402\n\n\ndef row(candidate_id: str, model: str, company: str, cost: float, quality: float) -> dict:\n    return {\n        "candidate_id": candidate_id,\n        "interpretation_id": "i",\n        "coverage_keys": ["final#0"],\n        "assigned_work": ["final"],\n        "functions": ["synthesis"],\n        "model": model,\n        "model_company": company,\n        "provider_endpoint": f"{model}@{company}",\n        "provider_slug": company,\n        "estimated_cost": cost,\n        "estimated_quality": quality,\n        "quality_uncertainty": 0.10,\n        "failure_probability": 0.025,\n        "parameter_profile": {\n            "model_company": company,\n            "p95_token_usage_multiplier": 1.18,\n            "structured_p95_token_usage_multiplier": 1.22,\n            "operational_serviceability": {"estimated_deadline_ratio": 0.66},\n        },\n        "professional_capabilities": {},\n        "prompt_profile": {},\n        "reasoning_profile": {},\n        "output_contract": {},\n        "request_config": {},\n        "independence_groups": [],\n    }\n\n\nclass RecoveryGuardProductionRegressionTests(unittest.TestCase):\n    def test_expensive_recovery_is_excluded_and_cheaper_value_candidate_ranks_first(self) -> None:\n        config = RuntimeConfig(5, 1, 0.35, "value")\n        policy = CrossEndpointPlannerPolicy(config)\n        selected = row("selected", "z-ai/glm", "zhipu", 0.015, 0.80)\n        anthropic = row("anthropic", "anthropic/opus", "anthropic", 0.27289742, 0.851337)\n        google = row("google", "google/flash", "google", 0.04837589, 0.773448)\n        qwen = row("qwen", "qwen/max", "alibaba", 0.05178836, 0.73699)\n        optimization = {\n            "selected_initial_cost_usd": 0.041668,\n            "execution_graph": {\n                "nodes": [{**selected, "node_id": "selected"}],\n                "final_nodes": ["selected"],\n                "metadata": {"interpretation_id": "i"},\n            },\n        }\n        result = policy.rebalance_recovery_pool(\n            optimization,\n            {"candidates": [selected, anthropic, google, qwen]},\n        )\n        pool = result["execution_graph"]["metadata"]["recovery_pool"]["selected"]\n        models = [candidate["model"] for candidate in pool]\n        self.assertNotIn("anthropic/opus", models)\n        self.assertEqual("google/flash", models[0])\n        self.assertGreater(\n            result["recovery_pool_policy"]["budget_excluded_by_node"]["selected"],\n            0,\n        )\n\n    def test_runtime_revalidates_frozen_recovery_multiplier(self) -> None:\n        config = RuntimeConfig(2, 1, 0.35, "value")\n        graph = ExecutionGraph(\n            nodes=(), edges=(), execution_stages=(), entry_nodes=(), final_nodes=(),\n            required_work=(), estimated_quality=0.0, quality_floor=0.0,\n            estimated_total_cost=0.0, metadata={},\n        )\n        budget = BudgetController(config, graph)\n        ok, reason = budget.reserve("initial", 0.01, "initial")\n        self.assertTrue(ok, reason)\n        budget.reconcile(0.01)\n        ok, reason = budget.reserve(\n            "replacement",\n            0.27289742,\n            "final",\n            risk_multiplier=1.18 * 1.22,\n        )\n        self.assertFalse(ok)\n        self.assertEqual("risk-adjusted-cost-anomaly-limit-exhausted", reason)\n\n    def test_explicit_contract_uses_completion_token_floor_for_deadline(self) -> None:\n        candidate = SimpleNamespace(\n            parameter_profile={\n                "explicit_output_contract_expected": True,\n                "estimated_completion_usage_tokens": 9665,\n            }\n        )\n        tokens, applied, floor = contract_visible_token_floor(candidate, 2746)\n        self.assertEqual(9665, tokens)\n        self.assertTrue(applied)\n        self.assertEqual(9665, floor)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")

Path("tests/test_v5_closed_world_display_and_compaction.py").write_text('''from __future__ import annotations\n\nimport sys\nimport unittest\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\nsys.path.insert(0, str(ROOT / "open-model-market"))\n\nfrom v5_constitutional_runtime import ConstitutionalPromptPolicy  # noqa: E402\nfrom v5_task_constraints import (  # noqa: E402\n    closed_world_numeric_prompt,\n    compile_task_constraints,\n)\n\n\nclass ClosedWorldDisplayAndCompactionTests(unittest.TestCase):\n    def test_prompt_preserves_original_chinese_units(self) -> None:\n        task = "闭卷，不得编造。只有2名值守；库存表6顶，现场5顶。"\n        prompt = closed_world_numeric_prompt(task, compile_task_constraints(task))\n        self.assertIn("2名", prompt)\n        self.assertIn("6顶", prompt)\n        self.assertIn("5顶", prompt)\n        self.assertNotIn("2:people", prompt)\n        self.assertNotIn("6:item", prompt)\n\n    def test_upstream_compaction_removes_only_duplicate_raw_mirror(self) -> None:\n        contract = {\n            "validated_claims": ["事实A"],\n            "conclusions": ["结论B"],\n            "raw_fields": {"validated_claims": "事实A", "conclusions": "结论B"},\n            "schema_version": "v5-node-result-1",\n        }\n        compact = ConstitutionalPromptPolicy._compact_upstream_contract(contract)\n        self.assertNotIn("raw_fields", compact)\n        self.assertEqual(["事实A"], compact["validated_claims"])\n        self.assertEqual(["结论B"], compact["conclusions"])\n        self.assertEqual("v5-node-result-1", compact["schema_version"])\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")

print("patch applied")
