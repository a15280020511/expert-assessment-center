from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_execution_transport import (  # noqa: E402
    ACTIVE_TRANSPORT,
    classify_transport,
    partition_sync_transport,
)
from v5_hierarchical_candidate_optimizer import (  # noqa: E402
    materialize_candidate_pool_selection,
)
from v5_no_tools_policy import forbidden_model_route  # noqa: E402


def candidate(model: str, rank: int) -> dict[str, object]:
    return {
        "model": model,
        "company": model.split("/", 1)[0],
        "popularity_rank": rank,
        "official_intelligence_rank": rank,
        "prompt_usd_per_million": 0.1 + rank / 100,
        "completion_usd_per_million": 0.2 + rank / 100,
        "context_length": 131072,
        "max_completion_tokens": 16384,
    }


class ExecutionTransportTests(unittest.TestCase):
    def test_batch_is_transport_incompatible_not_no_tools(self) -> None:
        self.assertEqual("", forbidden_model_route({"model": "openai/gpt-5.4-nano:batch"}))
        value = classify_transport("openai/gpt-5.4-nano:batch")
        self.assertFalse(value["executable"])
        self.assertEqual("execution-transport", value["boundary"])
        self.assertEqual(
            "batch-route-requires-openrouter-async-batch-transport",
            value["reason"],
        )
        self.assertTrue(classify_transport("openai/gpt-5.4-nano")["executable"])

    def test_sync_partition_keeps_base_model_and_records_batch_exclusion(self) -> None:
        rows, rejected, audit = partition_sync_transport(
            [
                candidate("openai/gpt-5.4-nano:batch", 1),
                candidate("openai/gpt-5.4-nano", 2),
                candidate("vendor/reasoner", 3),
            ]
        )
        self.assertEqual(
            ["openai/gpt-5.4-nano", "vendor/reasoner"],
            [row["model"] for row in rows],
        )
        self.assertEqual(ACTIVE_TRANSPORT, rows[0]["execution_transport"])
        self.assertEqual(1, len(rejected))
        self.assertEqual(3, audit["input_candidate_count"])
        self.assertEqual(2, audit["executable_candidate_count"])
        self.assertEqual(1, audit["rejected_candidate_count"])
        self.assertFalse(audit["business_eligibility_gate"])
        self.assertFalse(audit["provider_gate"])
        self.assertFalse(audit["batch_route_supported_by_active_transport"])
        self.assertTrue(audit["batch_base_model_remains_eligible_when_present"])

    def test_materialized_plan_never_assigns_batch_but_preserves_current_role_scoring(self) -> None:
        models = [
            candidate("openai/gpt-5.4-nano:batch", 1),
            candidate("openai/gpt-5.4-nano", 2),
            candidate("vendor-a/reasoner-a", 3),
            candidate("vendor-b/reasoner-b", 4),
            candidate("vendor-c/reasoner-c", 5),
            candidate("vendor-d/reasoner-d", 6),
            candidate("vendor-e/reasoner-e", 7),
            candidate("vendor-f/reasoner-f", 8),
        ]
        packet = {
            "task_id": "transport-fixture",
            "task": {
                "question": "比较当前证据并形成条件化结论。",
                "requirements": ["检查证据", "检查反例"],
                "deliverables": ["结论"],
            },
            "evidence": [{"metric": "x", "value": 1}],
            "execution_acceptance": ["完整交付"],
            "governance_model_plan": {
                "candidate_pool_authority": "decision-system-governance",
                "expert_candidate_pool": models,
                "provider_routing_mode": "unrestricted-openrouter",
                "provider_restrictions_applied": False,
                "tool_use_forbidden": True,
                "tools_allowed": False,
            },
        }
        materialized, receipt = materialize_candidate_pool_selection(packet)
        plan = materialized["governance_model_plan"]
        all_runtime_models = [
            *[row["model"] for row in plan["selected_models"]],
            *[row["model"] for row in plan["recovery_models"]],
            *[row["model"] for row in plan["expert_center_ordered_standby"]],
        ]
        self.assertNotIn("openai/gpt-5.4-nano:batch", all_runtime_models)
        self.assertIn("openai/gpt-5.4-nano", all_runtime_models)
        self.assertEqual(8, plan["governance_candidate_count"])
        self.assertEqual(8, plan["expert_center_no_tools_candidate_count"])
        self.assertEqual(7, plan["expert_center_executable_candidate_count"])
        transport = receipt["execution_transport_boundary"]
        self.assertEqual(8, transport["input_candidate_count"])
        self.assertEqual(7, transport["executable_candidate_count"])
        self.assertEqual(1, transport["rejected_candidate_count"])
        self.assertEqual(
            [
                "constitutional-no-tools-route-boundary",
                "structural-execution-transport-compatibility",
            ],
            receipt["planning_sequence"][:2],
        )
        self.assertEqual("ortools-model-assignment", receipt["planning_sequence"][-1])
        audit = receipt["optimizer_audit"]
        self.assertEqual([], audit["hard_model_eligibility_gates"])
        self.assertEqual("no-tools", audit["only_hard_model_boundary"])
        self.assertTrue(audit["structural_execution_transport_boundary"])
        self.assertFalse(audit["fixed_metric_role_grammar_used"])
        self.assertFalse(audit["metric_role_adapter_used"])
        self.assertEqual(
            "current-generated-role-structural-signals",
            audit["role_metric_mode"],
        )
        solver = audit["dynamic_solver_profile"]
        self.assertEqual(
            "task-derived-scaled-deterministic-time",
            solver["search_budget_mode"],
        )
        self.assertFalse(solver["wall_clock_stop_condition_used"])
        self.assertEqual(1, solver["num_search_workers"])
        self.assertEqual(0.1, solver["deterministic_budget_floor"])
        self.assertEqual(2.0, solver["deterministic_budget_ceiling"])
        self.assertGreaterEqual(solver["max_deterministic_time"], 0.1)
        self.assertLessEqual(solver["max_deterministic_time"], 2.0)
        self.assertGreaterEqual(solver["reference_difficulty_budget"], 2.0)


if __name__ == "__main__":
    unittest.main()
