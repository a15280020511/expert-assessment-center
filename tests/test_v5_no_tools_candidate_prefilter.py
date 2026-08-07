from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_hierarchical_candidate_optimizer import (  # noqa: E402
    _partition_no_tools_routes,
    materialize_candidate_pool_selection,
)
from v5_no_tools_policy import forbidden_model_route  # noqa: E402


def candidate(model: str, index: int) -> dict[str, object]:
    return {
        "model": model,
        "company": model.split("/", 1)[0],
        "popularity_rank": index + 1,
        "official_intelligence_rank": 100 - index,
        "prompt_usd_per_million": 0.05 + index * 0.01,
        "completion_usd_per_million": 0.20 + index * 0.02,
        "request_usd": 0.0,
        "context_length": 262_144,
        "max_completion_tokens": 32_768,
    }


def packet() -> dict[str, object]:
    valid = [candidate(f"vendor-{i}/reasoner-{i}", i) for i in range(24)]
    blocked = [
        candidate("openai/gpt-5.4-nano:batch", 30),
        candidate("vendor/reasoner:online", 31),
        candidate("openrouter/auto", 32),
    ]
    return {
        "task_id": "no-tools-prefilter-regression",
        "task": {
            "question": "比较多个方案并形成条件化决策。",
            "requirements": [f"requirement-{i}" for i in range(1, 6)],
            "deliverables": ["比较", "反证", "最终建议"],
        },
        "evidence": [
            {"metric": "cost", "option": "A"},
            {"metric": "risk", "option": "B"},
            {"metric": "uncertainty", "option": "all"},
        ],
        "execution_acceptance": [
            "覆盖关键证据",
            "覆盖反例",
            "形成最终综合结论",
        ],
        "governance_model_plan": {
            "candidate_pool_authority": "decision-system-governance",
            "expert_candidate_pool": [*valid, *blocked],
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
            "tool_use_forbidden": True,
            "tools_allowed": False,
        },
    }


class NoToolsCandidatePrefilterTests(unittest.TestCase):
    def test_exact_old_batch_route_is_rejected_before_assignment(self) -> None:
        rows = [
            candidate("vendor/normal", 0),
            candidate("openai/gpt-5.4-nano:batch", 1),
        ]
        executable, rejected = _partition_no_tools_routes(rows)
        self.assertEqual(["vendor/normal"], [row["model"] for row in executable])
        self.assertEqual(
            ["openai/gpt-5.4-nano:batch"],
            [row["model"] for row in rejected],
        )

    def test_all_planned_and_standby_routes_satisfy_same_no_tools_boundary(self) -> None:
        materialized, receipt = materialize_candidate_pool_selection(packet())
        plan = materialized["governance_model_plan"]
        boundary = plan["constitutional_candidate_boundary"]
        self.assertEqual(27, boundary["governance_candidate_count"])
        self.assertEqual(24, boundary["executable_candidate_count"])
        self.assertEqual(3, boundary["rejected_candidate_count"])
        self.assertFalse(boundary["business_eligibility_gate"])
        self.assertEqual("no-tools", boundary["only_hard_model_boundary"])

        routed_rows = [
            *plan["selected_models"],
            *plan["recovery_models"],
            *plan["expert_center_ordered_standby"],
        ]
        self.assertEqual(24, len(routed_rows))
        self.assertTrue(
            all(not forbidden_model_route({"model": row["model"]}) for row in routed_rows)
        )
        rejected_models = {
            row["model"] for row in boundary["rejected_candidates"]
        }
        self.assertEqual(
            {
                "openai/gpt-5.4-nano:batch",
                "vendor/reasoner:online",
                "openrouter/auto",
            },
            rejected_models,
        )
        self.assertEqual([], plan["optimizer_audit"]["hard_model_eligibility_gates"])
        self.assertTrue(
            plan["optimizer_audit"][
                "constitutional_no_tools_route_prefilter_applied"
            ]
        )
        self.assertEqual(
            3,
            receipt["constitutional_no_tools_route_rejected_count"],
        )


if __name__ == "__main__":
    unittest.main()
