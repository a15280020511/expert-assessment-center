from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import resource_matrix  # noqa: E402
import v5_general_task_planning  # noqa: E402


FAILED_PRODUCTION_TASK = (
    "真实复杂闭卷任务：福州市一所小学夜班期间发生第五类复合事件。已知条件仅限题面："
    "校内只有1名夜班保安；20:35开始雷雨并伴随频繁闪电；20:43教学楼顶层一处天窗"
    "出现裂纹且有雨水渗入，禁止靠近其正下方；20:49消防水箱区域传出持续流水声，"
    "来源未知，禁止保安进入受限设备区域；20:56一楼西侧走廊出现局部积水，但尚未"
    "接触可见电气设施；21:02校门外有2名自称校外活动承办方人员要求取回器材，无法"
    "核验当晚授权；21:08值班手机剩余27%电量；纸质登记显示1名维修人员签入时间清晰"
    "但签出栏空白；门卫室有纸质通讯录、手电筒1支、机械钥匙、灭火器、急救包、警戒带"
    "和纸笔；禁止保安独自进入设备区域，禁止站在裂纹天窗下方，禁止触碰未知积水附近"
    "电气设施，禁止与门外人员发生肢体冲突，禁止擅自宣布风险解除。请在不联网、不调用"
    "工具、不编造电话号码、外部制度、设备状态、人员位置或专业检测结论的前提下，完成"
    "从20:35到22:05的90分钟应急桌面推演。必须分别给出：1）已知事实、假设、推断和"
    "未知项；2）按分钟或事件触发的行动时间线；3）裂纹天窗、设备区流水声、走廊积水、"
    "身份不明访客、人员去向不明五条风险链；4）生命安全优先级和决策树；5）可执行的"
    "通信顺序与通话要点模板，不填写虚构号码；6）门禁、隔离、警戒、照明、手机电量和"
    "证据记录措施；7）一票否决、撤离、封控和升级条件；8）至少12种失败模式及预防措施；"
    "9）关键资源耗尽与替代方案；10）22:05前的结束或移交判定；11）事后24小时和7天"
    "整改清单；12）红队反证和仍未解决的不确定性。每一项必须有独立Markdown二级标题，"
    "标题文字须与上述12项完全一致并保持上述顺序，内容不得合并；三级及更低标题只能作为"
    "所属二级章节的内部结构；不得建议违法、冒险、靠近潜在坠落区域、擅入设备区或单人"
    "对抗行为。"
)


class V5TabletopProductionSemanticsTests(unittest.TestCase):
    @staticmethod
    def run_config() -> SimpleNamespace:
        return SimpleNamespace(
            task=FAILED_PRODUCTION_TASK,
            minimum_context_length=16_384,
            max_completion_tokens=10_000,
        )

    def test_explicit_classifier_preserves_safety_without_research_false_positive(self) -> None:
        run = self.run_config()
        original = model_market.classify_task
        profile = v5_general_task_planning.classify_task(run.task, run)
        self.assertIs(original, model_market.classify_task)
        self.assertTrue(profile.high_stakes)
        self.assertFalse(profile.long_context)
        self.assertEqual("security", profile.primary_domain)
        self.assertNotIn("research", profile.domains)
        self.assertNotIn("business", profile.domains)

    def test_original_failed_task_compiles_to_one_budget_compatible_work_unit(self) -> None:
        run = self.run_config()
        profile = v5_general_task_planning.classify_task(run.task, run)
        bundle = resource_matrix.compile_v5_task_resources(
            profile,
            run,
            semantic_compiler=v5_general_task_planning.compile_task_semantics,
        )
        self.assertTrue(
            bundle["semantic_input_policy"]["semantic_compiler_injected_explicitly"]
        )
        semantics = bundle["task_semantics"]
        signals = semantics["task_signals"]
        self.assertTrue(signals["closed_book_tabletop_compaction_applied"])
        self.assertFalse(signals["external_evidence_required"])
        self.assertEqual(["security"], signals["active_domains"])
        self.assertEqual(["analysis", "decision_comparison"], signals["active_operations"])
        self.assertEqual(1, len(semantics["interpretations"]))
        work = semantics["interpretations"][0]["atomic_work"][0]
        self.assertEqual(1, len(semantics["interpretations"][0]["atomic_work"]))
        self.assertEqual(
            12,
            work["output_contract"]["task_explicit_delivery_section_count"],
        )
        self.assertTrue(work["output_contract"]["task_explicit_long_form_required"])
        self.assertNotIn("evidence_validation", work["operation_requirements"])
        self.assertNotIn("forecasting", work["operation_requirements"])
        self.assertNotIn("adversarial_reasoning", work["operation_requirements"])
        matrix = bundle["resource_matrices"]["matrices"][0]
        self.assertEqual(1, matrix["shape"]["work_count"])
        self.assertEqual([], matrix["hard_requirements"])

    def test_deprecated_install_is_a_noop(self) -> None:
        before = model_market.classify_task
        self.assertIsNone(v5_general_task_planning.install())
        self.assertIs(before, model_market.classify_task)


if __name__ == "__main__":
    unittest.main()
