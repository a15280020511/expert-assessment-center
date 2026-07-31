import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import task_semantic_compiler as compiler  # noqa: E402
import v5_execution_auditor_integrity as auditor  # noqa: E402
import v5_output_contract_delivery as delivery  # noqa: E402
import v5_task_delivery_contract as contract_policy  # noqa: E402
import v5_truncation_budget_policy as truncation  # noqa: E402
from execution_graph import SelectedNode  # noqa: E402
from v5_runtime import ProductionRuntime, RuntimeConfig  # noqa: E402


TASK = (
    "请完成90分钟推演。必须分别给出："
    "1）已知事实、假设、推断和未知项；"
    "2）按分钟或事件触发的行动时间线；"
    "3）停电、暴雨、不明人员、施工人员失联四条风险链；"
    "4）优先级和决策树；"
    "5）可执行的通信顺序与通话要点模板；"
    "6）门禁、巡查、照明、手机电量和证据记录措施；"
    "7）一票否决和升级条件；"
    "8）至少12种失败模式及预防措施；"
    "9）资源耗尽与替代方案；"
    "10）23:30前的结束判定；"
    "11）事后24小时和7天整改清单；"
    "12）红队反证和仍未解决的不确定性。"
    "每一项必须有独立Markdown二级标题，内容不得合并。"
)


def node(contract):
    return SelectedNode(
        node_id="node-markdown-contract",
        assigned_work=("work-synthesis",),
        professional_capabilities={"synthesis": 1.0},
        functions=("synthesis",),
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"enabled": True},
        parameter_profile={},
        model="vendor/model",
        provider_endpoint="vendor/model@provider/default",
        output_contract=contract,
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.05,
        request_config={
            "provider": {
                "only": ["provider/default"],
                "order": ["provider/default"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


def complete_answer(headings):
    return "\n\n".join(
        f"## {heading}\n\n### 内部细分\n\n{index}号章节的非空正文。"
        for index, heading in enumerate(headings, 1)
    )


class TestV5LongMarkdownContractIntegrity(unittest.TestCase):
    def test_extracts_numbered_h2_contract_after_chinese_colon(self):
        contract = contract_policy.extract_explicit_markdown_contract(TASK)
        self.assertTrue(contract["explicit_markdown_contract"])
        self.assertEqual(contract["task_explicit_delivery_section_count"], 12)
        self.assertEqual(contract["exact_markdown_headings"][0], "已知事实、假设、推断和未知项")
        self.assertEqual(contract["exact_markdown_headings"][-1], "红队反证和仍未解决的不确定性")
        self.assertNotIn("每一项必须", contract["exact_markdown_headings"][-1])

    def test_final_synthesis_inherits_exact_sections_internal_nodes_only_keep_breadth(self):
        synthesis = compiler._output_contract(TASK, {"synthesis": 1.0}, False)
        internal = compiler._output_contract(TASK, {"adversarial_reasoning": 1.0}, False)
        self.assertTrue(synthesis["explicit_markdown_contract"])
        self.assertEqual(synthesis["required_fields"], synthesis["exact_markdown_headings"])
        self.assertEqual(internal["task_explicit_delivery_section_count"], 12)
        self.assertTrue(internal["task_explicit_long_form_required"])
        self.assertNotIn("explicit_markdown_contract", internal)

    def test_nested_h3_does_not_terminate_h2_contract_section(self):
        contract = {
            "required_fields": ["forecast_horizon", "options"],
            "machine_readable_required": False,
        }
        answer = (
            "## forecast_horizon\n\n### 22:00—22:08\n\n行动正文\n\n"
            "### 22:08—22:12\n\n更多正文\n\n"
            "## options\n\n### 方案A\n\n方案正文"
        )
        runtime = ProductionRuntime(RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            quality_tier="value",
        ))
        evidence = runtime.execution_engine._contract(node(contract), answer)
        self.assertTrue(evidence["required_fields_complete"])
        self.assertIn("22:08—22:12", evidence["raw_fields"]["forecast_horizon"])
        self.assertEqual(evidence["contract_violations"], [])

    def test_generic_synthesis_shape_cannot_pass_explicit_user_markdown_contract(self):
        contract = compiler._output_contract(TASK, {"synthesis": 1.0}, False)
        wrong = (
            "## agreements\n\n共识\n\n"
            "## conclusions\n\n结论\n\n"
            "## final_recommendation\n\n建议"
        )
        passed, score, reasons = delivery.contract_aware_quality_gate(
            node(contract),
            {"choices": [{"finish_reason": "stop"}]},
            wrong,
        )
        self.assertFalse(passed)
        self.assertLessEqual(score, 0.35)
        self.assertTrue(any(reason.startswith("missing-exact-markdown-heading:") for reason in reasons))

    def test_complete_exact_markdown_contract_passes_and_preserves_order(self):
        contract = compiler._output_contract(TASK, {"synthesis": 1.0}, False)
        answer = complete_answer(contract["exact_markdown_headings"])
        self.assertEqual(contract_policy.validate_markdown_contract(answer, contract), [])
        passed, _, reasons = delivery.contract_aware_quality_gate(
            node(contract),
            {"choices": [{"finish_reason": "stop"}]},
            answer,
        )
        self.assertTrue(passed, reasons)

    def test_long_form_allowance_is_large_but_usage_remains_separate(self):
        contract = compiler._output_contract(TASK, {"synthesis": 1.0}, False)
        work = {
            "output_contract": contract,
            "context_requirements": {
                "expected_output_tokens": 2950,
                "expected_reasoning_tokens": 1700,
            },
            "reasoning_requirements": {
                "depth": 1.0,
                "verification": 0.9,
            },
        }
        allowance = truncation.completion_envelope(work, 32768)
        usage = truncation.estimated_completion_usage(work, 32768)
        self.assertGreaterEqual(allowance, 13_000)
        self.assertLess(usage, allowance)
        self.assertLessEqual(allowance, 32_768)

    def test_degraded_audit_gets_truthful_primary_diagnosis(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "v5-execution-summary.json").write_text(
                json.dumps({
                    "completion_mode": "degraded",
                    "quality_status": "degraded_success",
                    "quality_integrity": {"status": "DEGRADED"},
                }),
                encoding="utf-8",
            )
            base_result = {
                "status": "DEGRADED",
                "primary_failure": {
                    "code": "V5_PRODUCTION_AUDIT_FAILED",
                    "stage": "v5-production-audit",
                    "message": auditor.LEGACY_RUNTIME_FAILURE,
                    "retryable": False,
                },
                "checks": {},
                "failures": [],
                "degradations": ["V5 delivered through bounded degradation"],
            }
            evidence = {
                "strict_node_ids": ["strict"],
                "degraded_nodes": [{
                    "node_id": "degraded",
                    "status": "success_degraded",
                    "quality_score": 0.7,
                    "gate_failures": [],
                    "contract_incomplete": True,
                }],
                "failed_node_ids": [],
                "contract_incomplete_node_ids": ["degraded"],
                "all_nodes_strict": False,
            }
            with patch.object(auditor.base, "audit", return_value=base_result), patch.object(
                auditor,
                "_apply_native_contract",
                side_effect=lambda _root, value, _planning: value,
            ), patch.object(auditor, "_node_quality", return_value=evidence):
                result = auditor.audit(root, execute_outcome="success", publish_outcome="success")
            self.assertEqual(result["status"], "DEGRADED")
            self.assertEqual(result["primary_failure"]["code"], "DEGRADED_SUCCESS")
            self.assertEqual(result["primary_failure"]["stage"], "quality-integrity")
            self.assertNotEqual(result["primary_failure"]["message"], auditor.LEGACY_RUNTIME_FAILURE)


if __name__ == "__main__":
    unittest.main()
