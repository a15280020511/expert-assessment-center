from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_parameter_design_planner import (  # noqa: E402
    build_runtime_planning_context,
    design_required_parameters,
)
from v5_runtime_parameter_planner import discover_required_decisions  # noqa: E402
from v5_task_work_graph import build_current_work_graph  # noqa: E402


ALLOWED_CLASSES = {
    "constitutional_invariant",
    "infrastructure_invariant",
    "current_task_derived",
    "current_run_feedback_derived",
}


def candidates(count: int = 10) -> list[dict[str, object]]:
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


class ParameterDesignMetaTests(unittest.TestCase):
    def test_design_occurs_after_decision_discovery_and_before_resolution(self) -> None:
        packet = {
            "task": {
                "question": "比较两条独立路径后综合",
                "work_graph": {
                    "work_units": [
                        {"id": "a", "kind": "branch", "text": "A"},
                        {"id": "b", "kind": "branch", "text": "B"},
                        {
                            "id": "join",
                            "kind": "integration",
                            "text": "综合",
                            "depends_on": ["a", "b"],
                        },
                    ]
                },
            }
        }
        context = build_runtime_planning_context(packet, candidates())
        sequence = context["planning_sequence"]
        self.assertLess(
            sequence.index("required-decision-discovery"),
            sequence.index("parameter-design-meta-layer"),
        )
        self.assertLess(
            sequence.index("parameter-design-meta-layer"),
            sequence.index("generated-parameter-instance-construction"),
        )
        self.assertLess(
            sequence.index("generated-parameter-instance-construction"),
            sequence.index("current-signal-resolution-and-optuna"),
        )
        self.assertTrue(context["parameter_design_completed_before_value_resolution"])
        self.assertTrue(context["parameter_specs_constructed_from_design"])
        self.assertEqual(context["parameter_design_audit"]["status"], "PASS")

    def test_every_parameter_design_dimension_is_classified(self) -> None:
        packet = {
            "task": {
                "work_graph": {
                    "work_units": [
                        {"id": "root", "text": "root"},
                        {"id": "left", "text": "left", "depends_on": ["root"]},
                        {"id": "right", "text": "right", "depends_on": ["root"]},
                    ]
                }
            }
        }
        graph = build_current_work_graph(packet)
        decisions = discover_required_decisions(graph, candidates())
        profile = {"pressure": {"overall": 50}}
        design = design_required_parameters(graph, decisions, candidates(), profile)
        self.assertEqual(design["status"], "PASS")
        self.assertEqual(design["design_count"], len(decisions))
        self.assertEqual(design["unclassified_dimension_count"], 0)
        for row in design["designs"]:
            self.assertEqual(
                set(row["dimensions"]),
                {
                    "value_type",
                    "domain",
                    "resolver",
                    "dependencies",
                    "consumer_binding",
                    "recompute_trigger",
                },
            )
            for dimension in row["dimensions"].values():
                self.assertIn(dimension["classification"], ALLOWED_CLASSES)
                self.assertTrue(dimension["reason"])

    def test_parameter_specs_are_constructed_from_design(self) -> None:
        context = build_runtime_planning_context(
            {"task": {"question": "给出判断"}},
            candidates(),
        )
        requirements = context["parameter_requirements"]
        self.assertTrue(
            requirements["parameter_design_completed_before_parameter_instantiation"]
        )
        self.assertTrue(requirements["parameter_specs_constructed_from_design"])
        self.assertTrue(requirements["parameter_ids_are_generated_after_parameter_design"])
        for spec in requirements["parameter_specs"]:
            self.assertTrue(spec["parameter_spec_constructed_from_design"])
            self.assertIn("parameter_design", spec)
            self.assertEqual(
                spec["parameter_design"]["decision_id"],
                spec["decision_id"],
            )
            self.assertEqual(
                spec["domain"],
                spec["parameter_design"]["dimensions"]["domain"]["effective"],
            )

    def test_active_optimizer_uses_parameter_design_planner(self) -> None:
        source = (MARKET / "v5_hierarchical_candidate_optimizer.py").read_text(
            encoding="utf-8"
        )
        design_source = (MARKET / "v5_parameter_design_planner.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from v5_parameter_design_planner import", source)
        self.assertNotIn(
            "from v5_runtime_parameter_planner import (\n    PRINCIPLES",
            source,
        )
        self.assertIn("parameter_design_completed_before_parameter_resolution", source)
        self.assertIn("build_parameter_requirements_from_design", design_source)
        self.assertNotIn("base.discover_parameter_requirements(", design_source)


if __name__ == "__main__":
    unittest.main()
