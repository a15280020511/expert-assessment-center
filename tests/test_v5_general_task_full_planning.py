import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import resource_matrix  # noqa: E402
import v5_candidate_diversity  # noqa: E402
import v5_production_hardening  # noqa: E402
import v5_value_optimizer  # noqa: E402
from execution_graph import ExecutionGraph, GraphLimits  # noqa: E402
from tests.test_v5_planner_executor import TestV5PlannerExecutor  # noqa: E402
from v5_planning_diagnostics import build_infeasibility_report  # noqa: E402


class V5GeneralTaskFullPlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        v5_production_hardening.install()
        v5_candidate_diversity.install()
        cls.fixture = TestV5PlannerExecutor()

    @staticmethod
    def run(task: str):
        return SimpleNamespace(
            task=task,
            minimum_context_length=16_384,
            max_completion_tokens=3_000,
        )

    def candidate_bundle(self, task: str):
        run = self.run(task)
        profile = model_market.classify_task(task, run)
        resources = resource_matrix.compile_v5_task_resources(profile, run)
        compiled_market = v5_value_optimizer.compile_model_endpoint_market(
            self.fixture.models(),
            resources,
            endpoint_payloads=self.fixture.endpoints(),
            ranking_limit=50,
            allow_synthetic_fixture=False,
        )
        candidates = v5_value_optimizer.generate_candidate_graph(
            resources,
            compiled_market,
            maximum_per_group=10,
        )
        return profile, resources, candidates

    def plan(self, task: str, *, budget: float = 0.25):
        profile, resources, candidates = self.candidate_bundle(task)
        limits = GraphLimits(
            max_nodes=4,
            max_edges=64,
            max_stages=8,
            max_model_calls=4,
            max_retries=0,
            max_replacements=0,
            max_budget_usd=budget,
        )
        optimization = v5_value_optimizer.optimize_execution_graph(
            candidates,
            limits=limits,
            solver_timeout_seconds=10,
        )
        return profile, resources, candidates, optimization

    def test_wifi_task_has_a_feasible_low_cost_graph(self):
        task = (
            "比较手机流量和随身WiFi。A每月39元含60GB，超出5元每GB；B设备99元，"
            "每月20元含120GB。计算12个月和18个月总成本、盈亏平衡时间，并在80GB、"
            "100GB、140GB下做敏感性分析，给出30天试用建议。"
        )
        _, resources, _, optimization = self.plan(task)
        graph = ExecutionGraph.from_mapping(optimization["execution_graph"])
        self.assertEqual(
            resources["task_semantics"]["interpretations"][0]["strategy"],
            "cost_performance_compact_decision",
        )
        self.assertLessEqual(len(graph.nodes), 2)
        self.assertGreaterEqual(len(graph.nodes), 1)
        self.assertLessEqual(optimization["selected_effective_cost_usd"], 0.25 / 1.35)

    def test_job_choice_task_has_a_feasible_low_cost_graph(self):
        task = (
            "比较夜班保安与网约车。保安月收入4200元，网约车流水12000元、成本7800元。"
            "计算净收入、单位工时收入、三年收入，做悲观基准乐观情景、现金流风险分析，"
            "并给出转岗门槛和90天行动方案。"
        )
        profile, _, _, optimization = self.plan(task)
        graph = ExecutionGraph.from_mapping(optimization["execution_graph"])
        self.assertFalse(profile.high_stakes)
        self.assertLessEqual(len(graph.nodes), 2)
        self.assertFalse(optimization["fallback_used"])

    def test_diagnostic_reports_node_budget_shortage(self):
        task = "比较两个套餐，计算12个月成本、盈亏平衡时间和敏感性。"
        _, _, candidates = self.candidate_bundle(task)
        report = build_infeasibility_report(
            candidates,
            GraphLimits(max_nodes=0, max_model_calls=4, max_replacements=0),
            message="test",
        )
        self.assertEqual(report["code"], "BUDGET_INSUFFICIENT_NODES")
        self.assertEqual(report["minimum_required_nodes"], 1)
        self.assertEqual(report["model_calls_performed"], 0)

    def test_diagnostic_reports_cost_budget_shortage(self):
        task = "比较两个套餐，计算12个月成本、盈亏平衡时间和敏感性。"
        _, _, candidates = self.candidate_bundle(task)
        report = build_infeasibility_report(
            candidates,
            GraphLimits(
                max_nodes=4,
                max_model_calls=4,
                max_replacements=0,
                max_budget_usd=0.000001,
            ),
            message="test",
        )
        self.assertEqual(report["code"], "BUDGET_INSUFFICIENT_COST")
        self.assertGreater(report["minimum_effective_expected_cost_usd"], 0)
        self.assertIn("planning_raw_budget_usd", report["ticket_limits"])


def explicit_suite() -> unittest.TestSuite:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        V5GeneralTaskFullPlanningTests
    )
    if suite.countTestCases() != 4:
        raise RuntimeError(
            f"full-planning regression suite count mismatch: {suite.countTestCases()}"
        )
    return suite


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(explicit_suite())
    raise SystemExit(0 if result.wasSuccessful() else 1)
