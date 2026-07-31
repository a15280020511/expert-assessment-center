import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import resource_matrix  # noqa: E402
import v5_general_task_planning  # noqa: E402


class V5GeneralTaskPlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        v5_general_task_planning.install()

    @staticmethod
    def run(task: str):
        return SimpleNamespace(
            task=task,
            minimum_context_length=16_384,
            max_completion_tokens=3_000,
        )

    def compile(self, task: str):
        run = self.run(task)
        profile = model_market.classify_task(task, run)
        bundle = resource_matrix.compile_v5_task_resources(profile, run)
        return profile, bundle

    def test_wifi_cost_comparison_is_compact_and_not_high_stakes(self):
        task = (
            "一名夜班工作人员需要比较手机流量和随身WiFi。方案A每月39元含60GB，"
            "超出后5元每GB；方案B设备99元，每月20元含120GB。计算12个月、18个月"
            "总成本、盈亏平衡时间，并在80GB、100GB、140GB下做敏感性分析，最后输出"
            "风险和30天试用报告。"
        )
        profile, bundle = self.compile(task)
        self.assertFalse(profile.high_stakes)
        self.assertFalse(profile.long_context)
        self.assertEqual(profile.complexity, "simple")
        self.assertNotIn("research", profile.domains)

        semantics = bundle["task_semantics"]
        self.assertTrue(semantics["task_signals"]["cost_performance_compaction_applied"])
        self.assertEqual(len(semantics["interpretations"]), 1)
        works = semantics["interpretations"][0]["atomic_work"]
        self.assertEqual(len(works), 1)
        self.assertEqual(
            works[0]["independence_requirements"]["minimum_independent_copies"],
            1,
        )
        self.assertFalse(
            works[0]["independence_requirements"]["different_model_required"]
        )
        self.assertNotIn("evidence_validation", works[0]["operation_requirements"])
        self.assertNotIn("adversarial_reasoning", works[0]["operation_requirements"])

    def test_job_choice_with_cashflow_risk_is_not_regulated_high_stakes(self):
        task = (
            "比较继续做夜班保安与转做网约车。保安每月4200元，网约车流水12000元，"
            "车辆和能源等成本7800元。计算月净收入、单位工时收入、三年收入，做悲观、"
            "基准、乐观情景和现金流风险分析，给出转岗门槛与90天行动方案。"
        )
        profile, bundle = self.compile(task)
        self.assertFalse(profile.high_stakes)
        self.assertFalse(profile.long_context)
        works = bundle["task_semantics"]["interpretations"][0]["atomic_work"]
        self.assertEqual(len(works), 1)
        operations = works[0]["operation_requirements"]
        self.assertIn("quantitative_modeling", operations)
        self.assertIn("decision_comparison", operations)
        self.assertIn("forecasting", operations)

    def test_generic_report_word_does_not_force_long_context(self):
        task = "比较两个月度套餐的成本并输出简洁报告。"
        run = self.run(task)
        profile = model_market.classify_task(task, run)
        self.assertFalse(profile.long_context)
        self.assertLess(profile.requested_context, 65_536)

    def test_explicit_full_repository_audit_remains_long_context(self):
        task = "请对整个代码库逐行审计，检查安全漏洞和合规问题。"
        run = self.run(task)
        profile = model_market.classify_task(task, run)
        self.assertTrue(profile.long_context)
        self.assertTrue(profile.high_stakes)
        self.assertGreaterEqual(profile.requested_context, 65_536)

    def test_medical_decision_remains_high_stakes_and_uncompacted(self):
        task = "比较两种临床治疗方案，核验医学证据并评估用药风险。"
        profile, bundle = self.compile(task)
        self.assertTrue(profile.high_stakes)
        self.assertFalse(
            bundle["task_semantics"]["task_signals"].get(
                "cost_performance_compaction_applied", False
            )
        )
        self.assertGreaterEqual(len(bundle["task_semantics"]["interpretations"]), 1)


if __name__ == "__main__":
    unittest.main()
