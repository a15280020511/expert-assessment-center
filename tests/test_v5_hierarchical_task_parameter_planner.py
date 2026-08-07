from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_hierarchical_candidate_optimizer import (  # noqa: E402
    materialize_candidate_pool_selection,
)
from v5_hierarchical_task_planner import (  # noqa: E402
    build_hierarchical_planning_context,
    decompose_task,
    discover_parameter_requirements,
)


def _candidate(index: int) -> dict[str, object]:
    return {
        "model": f"vendor-{index % 4}/reasoner-{index}",
        "company": f"vendor-{index % 4}",
        "popularity_rank": index + 1,
        "official_intelligence_rank": 20 - index,
        "prompt_usd_per_million": 0.05 + index * 0.01,
        "completion_usd_per_million": 0.20 + index * 0.02,
        "request_usd": 0.0,
        "context_length": 131_072 + index * 1024,
        "max_completion_tokens": 16_384 + index * 128,
    }


def _candidates(count: int = 20) -> list[dict[str, object]]:
    return [_candidate(index) for index in range(count)]


def _packet(*, complex_task: bool) -> dict[str, object]:
    task: dict[str, object] = {
        "question": "比较方案A/B/C并给出条件化建议。",
        "language": "zh-CN",
    }
    packet: dict[str, object] = {
        "task_id": "hierarchical-fixture-complex" if complex_task else "hierarchical-fixture-simple",
        "task": task,
        "governance_model_plan": {
            "candidate_pool_authority": "decision-system-governance",
            "expert_candidate_pool": _candidates(),
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
            "tool_use_forbidden": True,
            "tools_allowed": False,
        },
    }
    if complex_task:
        task["requirements"] = [f"requirement-{index}" for index in range(1, 7)]
        task["deliverables"] = ["预算建议", "可靠性建议", "综合建议"]
        packet["evidence"] = [
            {"option": "A", "metric": "cost"},
            {"option": "B", "metric": "duration"},
            {"option": "C", "metric": "failure"},
            {"option": "all", "metric": "uncertainty"},
        ]
        packet["execution_acceptance"] = [
            "覆盖所有方案",
            "覆盖全部约束",
            "解释不确定性",
            "给出条件化建议",
            "形成最终综合结论",
        ]
    return packet


class HierarchicalTaskParameterPlannerTests(unittest.TestCase):
    def test_task_is_decomposed_before_parameter_discovery(self) -> None:
        simple = decompose_task(_packet(complex_task=False))
        complex_value = decompose_task(_packet(complex_task=True))
        self.assertTrue(simple["finite_acyclic_by_construction"])
        self.assertTrue(complex_value["finite_acyclic_by_construction"])
        self.assertGreater(
            complex_value["work_unit_count"],
            simple["work_unit_count"],
        )
        self.assertGreater(
            complex_value["dependency_edge_count"],
            simple["dependency_edge_count"],
        )
        self.assertGreaterEqual(
            complex_value["maximum_depth"],
            simple["maximum_depth"],
        )

    def test_required_parameter_set_is_task_derived(self) -> None:
        candidates = _candidates()
        simple_decomposition = decompose_task(_packet(complex_task=False))
        complex_decomposition = decompose_task(_packet(complex_task=True))
        simple = discover_parameter_requirements(simple_decomposition, candidates)
        complex_value = discover_parameter_requirements(complex_decomposition, candidates)
        self.assertFalse(simple["fixed_parameter_template_used"])
        self.assertFalse(complex_value["fixed_parameter_template_used"])
        self.assertNotIn("evidence_pressure", simple["required_parameter_ids"])
        self.assertIn("evidence_pressure", complex_value["required_parameter_ids"])
        self.assertNotIn("validation_depth", simple["required_parameter_ids"])
        self.assertIn("validation_depth", complex_value["required_parameter_ids"])
        self.assertGreater(
            complex_value["required_parameter_count"],
            simple["required_parameter_count"],
        )

    def test_parameter_values_and_team_shape_change_with_task(self) -> None:
        candidates = _candidates()
        simple = build_hierarchical_planning_context(
            _packet(complex_task=False), candidates
        )
        complex_value = build_hierarchical_planning_context(
            _packet(complex_task=True), candidates
        )
        self.assertEqual(
            simple["planning_sequence"],
            [
                "task-decomposition",
                "parameter-requirement-discovery",
                "parameter-value-resolution",
                "team-and-role-derivation",
                "ortools-model-assignment",
            ],
        )
        self.assertTrue(
            simple["resolved_parameters"]["parameter_values_derived_from_current_task"]
        )
        self.assertTrue(
            complex_value["resolved_parameters"]["parameter_values_derived_from_current_task"]
        )
        self.assertGreaterEqual(
            complex_value["primary_expert_count"],
            simple["primary_expert_count"],
        )
        self.assertGreaterEqual(
            complex_value["recovery_count"],
            simple["recovery_count"],
        )
        self.assertNotEqual(
            complex_value["resolved_parameters"]["work_graph_load"],
            simple["resolved_parameters"]["work_graph_load"],
        )
        self.assertNotEqual(
            complex_value["resolved_parameters"]["role_topology"],
            simple["resolved_parameters"]["role_topology"],
        )

    def test_optimizer_receipt_proves_hierarchical_order_and_open_gates(self) -> None:
        materialized, receipt = materialize_candidate_pool_selection(
            _packet(complex_task=True)
        )
        plan = materialized["governance_model_plan"]
        audit = plan["optimizer_audit"]
        self.assertTrue(plan["expert_center_hierarchical_planning_completed"])
        self.assertTrue(plan["task_decomposition_completed"])
        self.assertTrue(plan["parameter_requirement_discovery_completed"])
        self.assertTrue(plan["parameter_values_resolved_before_model_assignment"])
        self.assertEqual(
            plan["planning_sequence"][-1],
            "ortools-model-assignment",
        )
        self.assertEqual(audit["hard_model_eligibility_gates"], [])
        self.assertFalse(audit["fixed_parameter_template_used"])
        self.assertFalse(audit["fixed_parameter_values_used"])
        self.assertFalse(audit["company_uniqueness_constraint_used"])
        self.assertFalse(audit["top50_membership_constraint_used"])
        self.assertFalse(audit["budget_constraint_used"])
        self.assertEqual(plan["provider_routing_mode"], "unrestricted-openrouter")
        self.assertTrue(plan["tool_use_forbidden"])
        self.assertFalse(plan["tools_allowed"])
        self.assertEqual(plan["only_hard_model_boundary"], "no-tools")
        self.assertEqual(receipt["planning_sequence"], plan["planning_sequence"])
        self.assertEqual(receipt["model_calls"], 0)

    def test_domain_words_do_not_create_keyword_routing(self) -> None:
        packet = _packet(complex_task=True)
        task = packet["task"]
        assert isinstance(task, dict)
        task["question"] = "法律商业数学任务，仍只按结构信号规划。"
        context = build_hierarchical_planning_context(packet, _candidates())
        self.assertFalse(context["semantic_keyword_routing_used"])
        self.assertFalse(context["decomposition"]["semantic_keyword_routing_used"])
        self.assertFalse(
            context["parameter_requirements"]["semantic_keyword_routing_used"]
        )


if __name__ == "__main__":
    unittest.main()
