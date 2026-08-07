from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_execution_transport import (  # noqa: E402
    ACTIVE_TRANSPORT,
    classify_model_route,
    filter_executable_candidates,
)
from v5_hierarchical_candidate_optimizer import (  # noqa: E402
    materialize_candidate_pool_selection,
)


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
    def test_batch_is_transport_incompatible_not_a_business_gate(self) -> None:
        value = classify_model_route("openai/gpt-5.4-nano:batch")
        self.assertFalse(value["executable"])
        self.assertEqual("execution-transport", value["boundary"])
        self.assertEqual(
            "batch-route-requires-openrouter-async-batch-transport",
            value["reason"],
        )
        base = classify_model_route("openai/gpt-5.4-nano")
        self.assertTrue(base["executable"])

    def test_no_tools_and_exact_identity_routes_are_separate_structural_reasons(self) -> None:
        online = classify_model_route("vendor/model:online")
        pseudo = classify_model_route("openrouter/auto")
        self.assertEqual("no-tools", online["boundary"])
        self.assertEqual("exact-model-identity", pseudo["boundary"])

    def test_filter_keeps_base_model_and_records_batch_exclusion(self) -> None:
        rows, audit = filter_executable_candidates(
            [
                candidate("openai/gpt-5.4-nano:batch", 1),
                candidate("openai/gpt-5.4-nano", 2),
                candidate("vendor/reasoner", 3),
                candidate("vendor/reasoner:online", 4),
            ]
        )
        self.assertEqual(
            ["openai/gpt-5.4-nano", "vendor/reasoner"],
            [row["model"] for row in rows],
        )
        self.assertEqual(ACTIVE_TRANSPORT, rows[0]["execution_transport"])
        self.assertEqual(4, audit["governance_candidate_count"])
        self.assertEqual(2, audit["executable_candidate_count"])
        self.assertEqual(2, audit["structurally_excluded_route_count"])
        self.assertFalse(audit["business_model_gate_used"])
        self.assertFalse(audit["provider_gate_used"])
        self.assertFalse(audit["batch_route_supported_by_active_transport"])
        self.assertTrue(audit["batch_base_models_remain_eligible_when_present"])

    def test_materialized_plan_never_assigns_sync_incompatible_route(self) -> None:
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
        audit = receipt["execution_transport_compatibility"]
        self.assertEqual(8, audit["governance_candidate_count"])
        self.assertEqual(7, audit["executable_candidate_count"])
        self.assertEqual(1, audit["structurally_excluded_route_count"])
        self.assertEqual(
            "structural-execution-transport-compatibility",
            receipt["planning_sequence"][0],
        )
        self.assertEqual("ortools-model-assignment", receipt["planning_sequence"][-1])
        self.assertEqual([], receipt["optimizer_audit"]["hard_model_eligibility_gates"])
        self.assertTrue(receipt["structural_execution_transport_boundary"])


if __name__ == "__main__":
    unittest.main()
