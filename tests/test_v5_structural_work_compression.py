from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_task_work_graph import build_current_work_graph  # noqa: E402


class StructuralWorkCompressionTests(unittest.TestCase):
    def test_multi_project_evidence_becomes_four_branches_not_ticket_field_count(self) -> None:
        evidence = []
        for project, income, cost in (
            ("甲", 145000, 98000),
            ("乙", 132000, 76000),
            ("丙", 118000, 61000),
            ("丁", 158000, 116000),
        ):
            evidence.extend(
                [
                    {"project": project, "metric": "investment", "value": 1000000, "unit": "CNY"},
                    {"project": project, "metric": "income", "value": income, "unit": "CNY"},
                    {"project": project, "metric": "cost", "value": cost, "unit": "CNY"},
                    {"project": project, "metric": "downside", "value": 0.75, "unit": "ratio"},
                ]
            )
        evidence.extend(
            [
                {"metric": "shared_overhead", "value": 18000, "unit": "CNY", "scope": "selected"},
                {"metric": "horizon", "value": 24, "unit": "months", "scope": "all"},
            ]
        )
        packet = {
            "task": {
                "question": "独立比较甲乙丙丁四个项目后综合决策",
                "requirements": [f"requirement-{i}" for i in range(25)],
                "required_outputs": [f"output-{i}" for i in range(10)],
            },
            "execution_acceptance": [f"acceptance-{i}" for i in range(30)],
            "evidence": evidence,
        }
        graph = build_current_work_graph(packet)
        compression = graph["structural_compression"]
        self.assertEqual("current-ticket-evidence-clusters", compression["mode"])
        self.assertEqual("project", compression["anchor_key"])
        self.assertEqual(4, compression["analysis_branch_count"])
        self.assertEqual(2, compression["shared_evidence_count"])
        self.assertFalse(compression["requirements_create_work_units"])
        self.assertFalse(compression["acceptance_create_work_units"])
        self.assertFalse(compression["deliverables_create_work_units"])
        # Four independent branches plus one final integration node.
        self.assertEqual(5, graph["work_unit_count"])
        self.assertEqual(4, graph["maximum_parallel_width"])
        self.assertEqual(2, graph["maximum_depth"])
        self.assertEqual(4, graph["dependency_edge_count"])
        self.assertFalse(graph["relatedness_is_dependency"])

    def test_high_cardinality_singleton_ids_do_not_create_one_expert_per_row(self) -> None:
        evidence = [
            {"record_id": f"r-{index}", "metric": "value", "value": index, "unit": "count"}
            for index in range(100)
        ]
        graph = build_current_work_graph(
            {
                "task": {"question": "分析这批记录并给出结论"},
                "evidence": evidence,
            }
        )
        compression = graph["structural_compression"]
        self.assertEqual("single-analysis-branch", compression["mode"])
        self.assertEqual(1, compression["analysis_branch_count"])
        self.assertEqual(1, graph["work_unit_count"])
        self.assertEqual(1, graph["maximum_parallel_width"])

    def test_explicit_user_work_graph_is_not_compressed(self) -> None:
        packet = {
            "task": {
                "work_graph": {
                    "work_units": [
                        {"id": "a", "objective": "independent A"},
                        {"id": "b", "objective": "independent B"},
                        {"id": "c", "objective": "synthesize", "depends_on": ["a", "b"]},
                    ]
                }
            }
        }
        graph = build_current_work_graph(packet)
        self.assertEqual("ticket-explicit-work-units", graph["structural_compression"]["mode"])
        self.assertFalse(graph["structural_compression"]["structural_compression_applied"])
        self.assertIn({"from": "a", "to": "c"}, graph["dependency_edges"])
        self.assertIn({"from": "b", "to": "c"}, graph["dependency_edges"])


if __name__ == "__main__":
    unittest.main()
