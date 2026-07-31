import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import resource_matrix  # noqa: E402
import v5_general_task_planning  # noqa: E402


class V5GeneralTaskPlanningTests(unittest.TestCase):
    @staticmethod
    def run_config(task: str):
        return SimpleNamespace(
            task=task,
            minimum_context_length=16_384,
            max_completion_tokens=3_000,
        )

    def compile(self, task: str):
        run = self.run_config(task)
        profile = v5_general_task_planning.classify_task(task, run)
        bundle = resource_matrix.compile_v5_task_resources(
            profile,
            run,
            semantic_compiler=v5_general_task_planning.compile_task_semantics,
        )
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
        self.assertTrue(
            bundle["semantic_input_policy"]["semantic_compiler_injected_explicitly"]
        )

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
        run = self.run_config(task)
        profile = v5_general_task_planning.classify_task(task, run)
        self.assertFalse(profile.long_context)
        self.assertLess(profile.requested_context, 65_536)

    def test_explicit_full_repository_audit_remains_long_context(self):
        task = "请对整个代码库逐行审计，检查安全漏洞和合规问题。"
        run = self.run_config(task)
        profile = v5_general_task_planning.classify_task(task, run)
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

    def test_closed_book_emergency_tabletop_is_four_work_fail_closed_plan(self):
        task = (
            "真实复杂闭卷任务：一所小学夜班发生复合事件。已知条件仅限题面，"
            "禁止保安进入受限设备区域，禁止靠近裂纹天窗下方，禁止触碰未知积水附近"
            "电气设施。请在不联网、不调用工具、不编造电话号码、外部制度、设备状态、"
            "人员位置或专业检测结论的前提下，完成90分钟应急桌面推演，给出行动时间线、"
            "风险链、生命安全决策树、撤离封控升级条件、至少12种失败模式、移交判定、"
            "红队反证和仍未解决的不确定性。"
        )
        profile, bundle = self.compile(task)
        self.assertTrue(profile.high_stakes)
        self.assertFalse(profile.long_context)
        self.assertEqual(profile.complexity, "complex")
        self.assertEqual("security", profile.primary_domain)
        self.assertNotIn("research", profile.domains)
        semantics = bundle["task_semantics"]
        signals = semantics["task_signals"]
        self.assertFalse(signals["closed_book_tabletop_compaction_applied"])
        self.assertTrue(signals["closed_book_tabletop_decomposition_applied"])
        self.assertEqual(signals["minimum_planned_work_units"], 4)
        works = semantics["interpretations"][0]["atomic_work"]
        self.assertEqual(4, len(works))
        self.assertEqual(
            sum("synthesis" in work["operation_requirements"] for work in works),
            1,
        )
        self.assertTrue(
            all(work["output_contract"]["fail_closed_on_quality_gate"] for work in works)
        )
        self.assertTrue(
            any("adversarial_reasoning" in work["operation_requirements"] for work in works)
        )
        matrix = bundle["resource_matrices"]["matrices"][0]
        self.assertGreaterEqual(matrix["shape"]["work_count"], 4)


def explicit_suite() -> unittest.TestSuite:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        V5GeneralTaskPlanningTests
    )
    if suite.countTestCases() != 6:
        raise RuntimeError(
            f"classification regression suite count mismatch: {suite.countTestCases()}"
        )
    return suite


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(explicit_suite())
    raise SystemExit(0 if result.wasSuccessful() else 1)
