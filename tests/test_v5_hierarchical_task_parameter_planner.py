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
        "task_id": (
            "hierarchical-fixture-complex"
            if complex_task
            else "hierarchical-fixture-simple"
        ),
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
        self.assertEqual(simple["planner_authority"], "v5_dynamic_parameter_graph")
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

    def test_required_parameter_set_is_task_derived_and_effective(self) -> None:
        candidates = _candidates()
        simple_decomposition = decompose_task(_packet(complex_task=False))
        complex_decomposition = decompose_task(_packet(complex_task=True))
        simple = discover_parameter_requirements(simple_decomposition, candidates)
        complex_value = discover_parameter_requirements(
            complex_decomposition,
            candidates,
        )
        self.assertFalse(simple["fixed_parameter_template_used"])
        self.assertFalse(complex_value["fixed_parameter_template_used"])
        self.assertFalse(complex_value["legacy_fixed_parameter_catalog_used"])
        self.assertNotIn("dependency_density", simple["required_parameter_ids"])
        self.assertIn("dependency_density", complex_value["required_parameter_ids"])
        self.assertNotIn("parallelism_ratio", simple["required_parameter_ids"])
        self.assertIn("parallelism_ratio", complex_value["required_parameter_ids"])
        self.assertGreater(
            complex_value["required_parameter_count"],
            simple["required_parameter_count"],
        )
        self.assertTrue(
            all(row.get("consumed_by") for row in complex_value["parameter_specs"])
        )

    def test_parameter_values_and_team_shape_change_with_task(self) -> None:
        candidates = _candidates()
        simple = build_hierarchical_planning_context(
            _packet(complex_task=False), candidates
        )
        complex_value = build_hierarchical_planning_context(
            _packet(complex_task=True), candidates
        )
        self.assertEqual(simple["planning_sequence"][-1], "ortools-model-assignment")
        self.assertEqual(
            simple["planner_authority"],
            "v5_dynamic_parameter_graph",
        )
        self.assertFalse(simple["legacy_fixed_parameter_catalog_used"])
        self.assertFalse(simple["legacy_fixed_role_grammar_used"])
        self.assertTrue(
            simple["resolved_parameters"][
                "parameter_values_derived_from_current_task"
            ]
        )
        self.assertTrue(
            complex_value["resolved_parameters"][
                "parameter_values_derived_from_current_task"
            ]
        )
        self.assertGreaterEqual(
            complex_value["primary_expert_count"],
            simple["primary_expert_count"],
        )
        self.assertGreaterEqual(
            complex_value["recovery_count"],
            simple["recovery_count"],
        )
        simple_values = simple["resolved_parameters"]["parameter_values"]
        complex_values = complex_value["resolved_parameters"]["parameter_values"]
        self.assertNotEqual(
            simple["decomposition"]["work_unit_count"],
            complex_value["decomposition"]["work_unit_count"],
        )
        self.assertNotEqual(
            simple["resolved_parameters"]["role_topology"],
            complex_value["resolved_parameters"]["role_topology"],
        )
        self.assertNotEqual(set(simple_values), set(complex_values))
        self.assertEqual(
            0,
            complex_value["resolved_parameters"]["parameter_coverage_audit"][
                "unconsumed_parameter_count"
            ],
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
        self.assertEqual(plan["planning_sequence"][-1], "ortools-model-assignment")
        self.assertEqual(
            plan["runtime_replanning"]["stage"],
            "runtime-feedback-replanning",
        )
        self.assertEqual(audit["hard_model_eligibility_gates"], [])
        self.assertFalse(audit["fixed_parameter_template_used"])
        self.assertFalse(audit["fixed_parameter_values_used"])
        self.assertFalse(audit["fixed_role_grammar_used"])
        self.assertFalse(audit["company_uniqueness_constraint_used"])
        self.assertFalse(audit["top50_membership_constraint_used"])
        self.assertFalse(audit["budget_constraint_used"])
        self.assertEqual(plan["provider_routing_mode"], "unrestricted-openrouter")
        self.assertTrue(plan["tool_use_forbidden"])
        self.assertFalse(plan["tools_allowed"])
        self.assertEqual(plan["only_hard_model_boundary"], "no-tools")
        self.assertEqual(receipt["planning_sequence"], plan["planning_sequence"])
        self.assertEqual(receipt["runtime_replanning"], plan["runtime_replanning"])
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
