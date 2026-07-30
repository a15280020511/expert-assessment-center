import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_r8_zero_call_joint_gate as gate  # noqa: E402


def node(node_id, work_id, model, *, allowance=6000, usage=4800):
    return {
        "node_id": node_id,
        "assigned_work": [work_id],
        "model": model,
        "provider_endpoint": f"{model}@provider",
        "estimated_cost_usd": 0.02,
        "recommended_output_allowance_tokens": allowance,
        "estimated_completion_usage_tokens": usage,
        "output_allowance_is_cost_assumption": False,
        "cost_estimation_policy": gate.EXPECTED_COST_POLICY,
    }


def task_payload(*, cost=0.18, allowance=6000, usage=4800, second_model="vendor/model-b"):
    evidence_work = "evidence-work"
    nodes = [
        node("n1", evidence_work, "vendor/model-a", allowance=allowance, usage=usage),
        node("n2", evidence_work, second_model, allowance=allowance, usage=usage),
    ]
    nodes.extend(
        node(f"n{index}", f"work-{index}", "vendor/model-a")
        for index in range(3, 10)
    )
    plan = {
        "feasible": True,
        "max_nodes": 9,
        "max_budget_usd": None,
        "selected_node_count": 9,
        "estimated_total_cost_usd": cost,
        "selected_nodes": nodes,
        "independence_policy": {
            "hard_model_diversity_scope": "explicit-independence-groups-only",
            "constraints": [
                {
                    "work_id": evidence_work,
                    "copies": 2,
                    "different_model_required": True,
                }
            ],
        },
    }
    return {
        "model_inference_calls": 0,
        "actual_cost_usd": 0.0,
        "tiers": [
            {
                "tier": {"name": "strict-economy"},
                "node_limit_attempts": [plan],
            }
        ],
    }


def write_fixture(root: Path, **kwargs):
    for task_id in gate.DEFAULT_TASK_IDS:
        path = root / "tasks" / task_id
        path.mkdir(parents=True, exist_ok=True)
        (path / "zero-call-task-diagnostic.json").write_text(
            json.dumps(task_payload(**kwargs)),
            encoding="utf-8",
        )


def runtime_pass(*args, **kwargs):
    return {
        "status": "pass",
        "passed": True,
        "selected_candidate_ids": [],
        "selected_node_count": 9,
        "selected_models": ["vendor/model-a", "vendor/model-b"],
        "selected_provider_endpoints": ["vendor/model-a@provider"],
        "estimated_total_cost_usd": 0.18,
        "runtime_preflight": {"status": "pass", "blockers": []},
        "model_inference_calls": 0,
        "actual_cost_usd": 0.0,
    }


class TestV5R8ZeroCallJointGate(unittest.TestCase):
    def setUp(self):
        self.runtime_patch = patch.object(gate, "_exact_runtime_preflight", side_effect=runtime_pass)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)

    def test_three_exact_nine_node_plans_unlock_paid_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            write_fixture(Path(temp))
            report = gate.evaluate(temp, max_nodes=9, max_cost_usd=0.25)
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["paid_inference_allowed"])
        self.assertTrue(report["runtime_preflight_parity_verified"])
        self.assertEqual(len(report["tasks"]), 3)
        self.assertTrue(all(row["allowance_usage_verified"] for row in report["tasks"]))
        self.assertFalse(report["production_entrypoint_changed"])
        self.assertFalse(report["v3_deleted"])

    def test_runtime_preflight_blocker_stops_paid_inference(self):
        self.runtime_patch.stop()
        self.runtime_patch = patch.object(
            gate,
            "_exact_runtime_preflight",
            return_value={
                **runtime_pass(),
                "status": "rejected",
                "passed": False,
                "runtime_preflight": {
                    "status": "rejected",
                    "blockers": ["preflight-risk-adjusted-cost-above-hard-budget"],
                },
            },
        )
        self.runtime_patch.start()
        with tempfile.TemporaryDirectory() as temp:
            write_fixture(Path(temp))
            report = gate.evaluate(temp)
        self.assertFalse(report["paid_inference_allowed"])
        self.assertTrue(any("runtime-preflight" in item for item in report["blockers"]))

    def test_over_budget_plan_blocks_paid_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            write_fixture(Path(temp), cost=0.25000001)
            report = gate.evaluate(temp, max_nodes=9, max_cost_usd=0.25)
        self.assertFalse(report["paid_inference_allowed"])
        self.assertTrue(any("estimated-cost" in item for item in report["blockers"]))

    def test_usage_above_allowance_blocks_paid_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            write_fixture(Path(temp), allowance=4000, usage=4800)
            report = gate.evaluate(temp)
        self.assertFalse(report["paid_inference_allowed"])
        self.assertTrue(any("invalid-allowance-usage" in item for item in report["blockers"]))

    def test_explicit_independence_reusing_model_blocks_paid_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            write_fixture(Path(temp), second_model="vendor/model-a")
            report = gate.evaluate(temp)
        self.assertFalse(report["paid_inference_allowed"])
        self.assertTrue(any("distinct-models=1<copies=2" in item for item in report["blockers"]))

    def test_missing_task_evidence_blocks_paid_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_id = gate.DEFAULT_TASK_IDS[0]
            path = root / "tasks" / task_id
            path.mkdir(parents=True, exist_ok=True)
            (path / "zero-call-task-diagnostic.json").write_text(
                json.dumps(task_payload()),
                encoding="utf-8",
            )
            report = gate.evaluate(root)
        self.assertFalse(report["paid_inference_allowed"])
        self.assertIn("task-evidence-count=1!=required=3", report["blockers"])


if __name__ == "__main__":
    unittest.main()
