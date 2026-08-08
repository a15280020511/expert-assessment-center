from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_task_work_graph import build_current_work_graph  # noqa: E402


class CurrentTaskWorkGraphIndependenceTests(unittest.TestCase):
    def test_structural_similarity_stays_non_blocking(self) -> None:
        packet = {
            "task": {
                "question": "比较甲乙两个方案并给出结论",
                "requirements": [
                    "比较甲方案成本和收益",
                    "比较乙方案成本和收益",
                ],
                "required_outputs": ["最终比较结论"],
            },
            "evidence": [
                {"site": "甲", "metric": "cost", "value": 10},
                {"site": "乙", "metric": "cost", "value": 12},
            ],
            "execution_acceptance": ["输出完整比较"],
        }
        graph = build_current_work_graph(packet)
        self.assertFalse(graph["relatedness_is_dependency"])
        self.assertEqual(
            "ticket-explicit-or-final-integration-only",
            graph["dependency_policy"],
        )
        integration = [
            row for row in graph["work_units"] if row["source_kind"] == "integration"
        ]
        self.assertEqual(1, len(integration))
        integration_id = integration[0]["unit_id"]
        hard_edges = graph["dependency_edges"]
        self.assertTrue(hard_edges)
        self.assertTrue(all(edge["to"] == integration_id for edge in hard_edges))
        self.assertGreater(graph["maximum_parallel_width"], 1)
        self.assertLessEqual(graph["maximum_depth"], 2)

    def test_ticket_explicit_dependency_is_preserved(self) -> None:
        packet = {
            "task": {
                "work_graph": {
                    "work_units": [
                        {"id": "a", "objective": "先计算基础量"},
                        {"id": "b", "objective": "使用基础量做结论", "depends_on": ["a"]},
                    ]
                }
            }
        }
        graph = build_current_work_graph(packet)
        self.assertIn({"from": "a", "to": "b"}, graph["dependency_edges"])
        self.assertEqual(2, graph["maximum_depth"])


if __name__ == "__main__":
    unittest.main()
