from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_runtime_parameter_planner import (  # noqa: E402
    build_runtime_planning_context,
    discover_parameter_requirements,
)
from v5_task_work_graph import build_current_work_graph  # noqa: E402


LEGACY_PARAMETER_IDS = {
    "work_graph_load",
    "team_size",
    "role_topology",
    "prompt_token_estimate",
    "completion_token_estimate",
    "protocol_reserve",
    "dependency_fan_in",
    "model_assignment",
    "solver_time",
    "solver_seed",
    "execution_partition_count",
    "parallelism_ratio",
    "dependency_density",
    "recovery_count",
    "runtime_standby_promotion",
}


def candidates(count: int = 12) -> list[dict[str, object]]:
    return [
        {
            "model": f"vendor-{index}/reasoner-{index}",
            "popularity_rank": index,
            "official_intelligence_rank": index,
            "prompt_usd_per_million": index / 100,
            "completion_usd_per_million": index / 50,
            "context_length": 131_072 + index,
            "max_completion_tokens": 16_384 + index,
        }
        for index in range(1, count + 1)
    ]


class RuntimeGeneratedParameterTests(unittest.TestCase):
    def test_parameter_ids_are_created_after_current_decisions(self) -> None:
        packet = {
            "task": {
                "question": "比较两个路径并交叉验证后形成结论",
                "work_graph": {
                    "work_units": [
                        {"id": "root", "kind": "baseline", "text": "建立基线"},
                        {"id": "left", "kind": "left", "text": "左路径", "depends_on": ["root"]},
                        {"id": "right", "kind": "right", "text": "右路径", "depends_on": ["root"]},
                        {"id": "join", "kind": "cross-check", "text": "交叉验证", "depends_on": ["left", "right"]},
                    ]
                },
            }
        }
        graph = build_current_work_graph(packet)
        requirements = discover_parameter_requirements(graph, candidates())
        self.assertTrue(requirements["parameter_ids_are_generated_after_decision_discovery"])
        self.assertFalse(requirements["fixed_parameter_template_used"])
        self.assertFalse(requirements["fixed_business_parameter_catalog_used"])
        self.assertGreater(len(requirements["required_decisions"]), 0)
        self.assertEqual(
            len(requirements["required_decisions"]),
            len(requirements["parameter_specs"]),
        )
        for spec in requirements["parameter_specs"]:
            self.assertRegex(spec["parameter_id"], r"^p-[0-9a-f]{16}$")
            self.assertNotIn(spec["parameter_id"], LEGACY_PARAMETER_IDS)
            self.assertTrue(spec["dynamic"])
            self.assertFalse(spec["fixed_default_used"])
            self.assertTrue(spec["derived_from"])
            self.assertTrue(spec["consumed_by"])
            self.assertTrue(spec["provenance"])
            self.assertTrue(spec["resolver"])

    def test_different_current_graphs_generate_different_parameter_identities(self) -> None:
        simple = build_runtime_planning_context(
            {"task": {"question": "给出判断"}}, candidates()
        )
        branched = build_runtime_planning_context(
            {
                "task": {
                    "work_graph": {
                        "work_units": [
                            {"id": "a", "text": "A"},
                            {"id": "b", "text": "B", "depends_on": ["a"]},
                            {"id": "c", "text": "C", "depends_on": ["a"]},
                        ]
                    }
                }
            },
            candidates(),
        )
        simple_ids = set(simple["parameter_requirements"]["required_parameter_ids"])
        branched_ids = set(branched["parameter_requirements"]["required_parameter_ids"])
        self.assertNotEqual(simple_ids, branched_ids)
        self.assertEqual(
            "PASS",
            branched["resolved_parameters"]["parameter_coverage_audit"]["status"],
        )
        self.assertEqual(
            0,
            branched["resolved_parameters"]["parameter_coverage_audit"]["fixed_business_parameter_count"],
        )
        self.assertTrue(branched["parameter_ids_generated_after_decision_discovery"])
        self.assertFalse(branched["fixed_business_objective_coefficients_used"])

    def test_active_modules_do_not_contain_old_scoring_coefficients(self) -> None:
        scoring = (MARKET / "v5_runtime_role_scoring.py").read_text(encoding="utf-8")
        planner = (MARKET / "v5_runtime_parameter_planner.py").read_text(encoding="utf-8")
        for literal in ("0.38", "0.22", "0.32", "0.18", "0.12", "0.35", "0.25"):
            self.assertNotIn(literal, scoring)
            self.assertNotIn(literal, planner)
        self.assertIsNone(re.search(r"parameter_id\s*[:=].*(team_size|recovery_count|parallelism_ratio|dependency_density)", planner))

    def test_reasoning_effort_is_relative_to_current_role_demand(self) -> None:
        context = build_runtime_planning_context(
            {
                "task": {
                    "work_graph": {
                        "work_units": [
                            {"id": "a", "kind": "small", "text": "A"},
                            {"id": "b", "kind": "large", "text": "B" * 200, "depends_on": ["a"]},
                            {"id": "c", "kind": "terminal", "text": "C" * 500, "depends_on": ["b"]},
                        ]
                    }
                }
            },
            candidates(),
        )
        roles = context["role_plan"]
        efforts = {row["reasoning_effort"] for row in roles}
        self.assertTrue(efforts.issubset({"low", "medium", "high"}))
        self.assertTrue(all(row["reasoning_effort_source"] == "current-role-demand-extrema" for row in roles))
        self.assertTrue(all(not row.get("metric_role_id") for row in roles))


if __name__ == "__main__":
    unittest.main()
