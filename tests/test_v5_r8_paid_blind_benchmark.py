import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_live_benchmark_r8 as r8  # noqa: E402


TASKS = r8.TASK_IDS


def record(task_id, strategy, quality, cost, *, degraded=False):
    artifacts = {}
    if strategy == "v5_joint_graph":
        artifacts = {
            "executor": "v5-r8-fault-aware",
            "completion_mode": "degraded" if degraded else "full",
        }
    return {
        "task_id": task_id,
        "strategy": strategy,
        "status": "success",
        "actual_cost_usd": cost,
        "safety_failure": False,
        "blind_quality_score": quality,
        "blind_judge_count": 2,
        "blind_judge_models": ["judge-a", "judge-b"],
        "blind_judge_providers": ["provider-a", "provider-b"],
        "blind_fatal_error": False,
        "blind_judge_disagreement_points": 5.0,
        "artifacts": artifacts,
    }


def passing_rows():
    rows = []
    for task_id in TASKS:
        rows.append(record(task_id, "v5_joint_graph", 0.82, 0.10))
        rows.append(record(task_id, "v3", 0.82, 0.12))
    return rows


class TestV5R8PaidBlindBenchmark(unittest.TestCase):
    def test_equal_quality_and_lower_cost_passes_stage_d_only(self):
        gate = r8.stage_d_gate(passing_rows())
        self.assertTrue(gate["stage_d_paid_blind_passed"])
        self.assertTrue(gate["canary_allowed"])
        self.assertFalse(gate["production_cutover_allowed"])
        self.assertFalse(gate["v3_deletion_allowed"])
        self.assertEqual(gate["blockers"], [])

    def test_installed_gate_uses_captured_original_without_recursion(self):
        r8.install_r8_stage_d()
        self.assertIs(r8.economy.economy_cutover_gate, r8.stage_d_gate)
        gate = r8.economy.economy_cutover_gate(passing_rows())
        self.assertTrue(gate["stage_d_paid_blind_passed"])
        self.assertFalse(gate["production_cutover_allowed"])

    def test_two_degraded_v5_results_block_stage_d(self):
        rows = []
        for index, task_id in enumerate(TASKS):
            rows.append(record(task_id, "v5_joint_graph", 0.85, 0.10, degraded=index < 2))
            rows.append(record(task_id, "v3", 0.80, 0.12))
        gate = r8.stage_d_gate(rows)
        self.assertFalse(gate["stage_d_paid_blind_passed"])
        self.assertIn("v5-degraded-results-exceeded-one-of-three", gate["blockers"])

    def test_higher_cost_requires_ten_percent_value_gain(self):
        rows = []
        for task_id in TASKS:
            rows.append(record(task_id, "v5_joint_graph", 0.82, 0.15))
            rows.append(record(task_id, "v3", 0.80, 0.12))
        gate = r8.stage_d_gate(rows)
        self.assertFalse(gate["stage_d_paid_blind_passed"])
        self.assertIn("v5-cost-performance-improvement-below-10-percent", gate["blockers"])

    def test_per_task_strategy_cap_is_hard(self):
        rows = []
        for index, task_id in enumerate(TASKS):
            rows.append(record(task_id, "v5_joint_graph", 0.90, 0.251 if index == 0 else 0.10))
            rows.append(record(task_id, "v3", 0.80, 0.12))
        gate = r8.stage_d_gate(rows)
        self.assertFalse(gate["stage_d_paid_blind_passed"])
        self.assertIn(
            "v5-per-task-cost-cap-exceeded:retail-expansion-unit-economics",
            gate["blockers"],
        )

    def test_r8_limits_restore_bounded_retry_and_replacement(self):
        limits = r8._r8_limits(max_nodes=16, max_model_calls=16, max_retries=0, max_replacements=0)
        self.assertEqual(limits.max_nodes, 9)
        self.assertEqual(limits.max_model_calls, 9)
        self.assertEqual(limits.max_retries, 1)
        self.assertEqual(limits.max_replacements, 2)
        self.assertEqual(limits.max_output_allowance_tokens, 10000)

    def test_strategy_exception_is_persisted_for_zero_call_diagnosis(self):
        with tempfile.TemporaryDirectory() as temp:
            try:
                raise RuntimeError("planner exploded")
            except RuntimeError as exc:
                r8._write_strategy_exception(Path(temp), {"task_id": TASKS[0]}, exc)
            report = json.loads(
                (Path(temp) / "v5-r8-strategy-exception.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["exception_type"], "RuntimeError")
            self.assertEqual(report["error"], "planner exploded")
            self.assertIsNone(report["known_model_request_count"])
            self.assertFalse(report["production_entrypoint_changed"])
            self.assertFalse(report["v3_deleted"])

    def test_workflow_is_exact_manual_unlock_single_key_and_joint_gated(self):
        workflow = (ROOT / ".github/workflows/v5-r8-stage-d-paid-blind.yml").read_text(encoding="utf-8")
        self.assertIn("issue_comment:", workflow)
        self.assertIn("github.event.issue.number == 64", workflow)
        self.assertIn("RUN_V5_R8_STAGE_D_20260731_R8H", workflow)
        self.assertNotIn("RUN_V5_R8_STAGE_D_20260731_R8B'", workflow)
        self.assertNotIn("RUN_V5_R8_STAGE_D_20260731_R8A'", workflow)
        self.assertIn("secrets.OPENROUTER_API_KEY", workflow)
        self.assertNotIn("OPENROUTER_MANAGEMENT_KEY", workflow)
        self.assertIn("Exact nine-node zero-inference planning preflight", workflow)
        self.assertIn("steps.planning.outcome == 'success'", workflow)
        self.assertIn("v5_r8_zero_call_joint_gate.py", workflow)
        self.assertIn("--max-nodes 9", workflow)
        self.assertIn("--max-cost-usd 0.25", workflow)
        self.assertIn("Production default switch allowed: `false`", workflow)
        self.assertIn("V3 deletion allowed: `false`", workflow)

    def test_wrapper_has_no_management_key_dependency(self):
        source = Path(r8.__file__).read_text(encoding="utf-8")
        self.assertNotIn("OPENROUTER_MANAGEMENT_KEY", source)
        self.assertIn("check_single_api_key", source)
        self.assertIn("production.install()", source)
        self.assertIn("_ORIGINAL_ECONOMY_GATE", source)


if __name__ == "__main__":
    unittest.main()
