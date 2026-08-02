import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import SelectedNode  # noqa: E402
import v5_output_contract_delivery as delivery  # noqa: E402
import v5_task_delivery_contract as contract_policy  # noqa: E402
from v5_task_envelope import work_output_contract  # noqa: E402


EXPECTED_HEADINGS = [
    "已知条件、假设与未知项",
    "成本结构、公式与选择阈值",
    "实施风险、运营风险与反证",
    "综合结论、适用条件与下一步",
]
INTERNAL_HEADINGS = ["risk_findings", "counterexamples", "unknowns"]
PAID_ACCEPTANCE_TASK = """
比较方案A“自建内部工单系统”和方案B“采用托管SaaS工单系统”。
分别完成成本结构与选择阈值分析、实施与运营风险反证，并形成综合决策。
不得调用外部工具。严格使用以下4个Markdown二级标题，顺序不得改变，每项不得为空：
1）已知条件、假设与未知项
2）成本结构、公式与选择阈值
3）实施风险、运营风险与反证
4）综合结论、适用条件与下一步
""".strip()
FLATTENED_TASK = " ".join(PAID_ACCEPTANCE_TASK.split())


def contract(*, final_node: bool) -> dict:
    return work_output_contract(
        FLATTENED_TASK,
        INTERNAL_HEADINGS,
        final_node=final_node,
    )


def _node(contract_value: dict, functions=("adversarial_reasoning",)) -> SelectedNode:
    return SelectedNode(
        node_id="node-red-team",
        assigned_work=("work-red-team",),
        professional_capabilities={"adversarial_reasoning": 0.9},
        functions=tuple(functions),
        prompt_profile={"modules": ["adversarial_challenge"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "high"},
        parameter_profile={"supported_parameters": ["reasoning", "max_tokens"]},
        model="company/red-team-model",
        provider_endpoint="company/red-team-model@provider-a",
        output_contract=dict(contract_value),
        estimated_quality=0.82,
        quality_uncertainty=0.08,
        estimated_cost=0.01,
        failure_probability=0.05,
        request_config={
            "reasoning": {"effort": "high", "exclude": True},
            "provider": {
                "order": ["provider-a"],
                "only": ["provider-a"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
        },
    )


def _answer(headings: list[str]) -> str:
    return "\n\n".join(
        f"## {heading}\n\n{heading}的非空分析正文。"
        for heading in headings
    )


class TestV5FinalContractRuntimeIsolation(unittest.TestCase):
    def test_flattened_numbered_final_headings_survive_contract_extraction(self):
        value = contract(final_node=True)
        self.assertTrue(value["explicit_markdown_contract"])
        self.assertEqual(value["exact_markdown_headings"], EXPECTED_HEADINGS)
        self.assertEqual(value["required_fields"], EXPECTED_HEADINGS)
        self.assertEqual(value["task_explicit_delivery_section_count"], 4)

    def test_intermediate_contract_does_not_inherit_final_heading_names(self):
        value = contract(final_node=False)
        self.assertFalse(value.get("explicit_markdown_contract", False))
        self.assertEqual(value["task_explicit_delivery_section_count"], 4)
        self.assertEqual(value["required_fields"], INTERNAL_HEADINGS)

    def test_generic_node_contract_rejects_answer_using_only_final_headings(self):
        value = contract(final_node=False)
        violations = contract_policy.validate_markdown_contract(
            _answer(EXPECTED_HEADINGS),
            value,
        )
        self.assertTrue(violations)
        self.assertTrue(
            any(
                reason.startswith("missing-required-markdown-heading:")
                for reason in violations
            )
        )

    def test_generic_node_contract_accepts_all_internal_headings_in_order(self):
        value = contract(final_node=False)
        self.assertEqual(
            contract_policy.validate_markdown_contract(
                _answer(INTERNAL_HEADINGS),
                value,
            ),
            [],
        )

    def test_quality_gate_marks_final_headings_invalid_for_internal_node(self):
        value = contract(final_node=False)
        passed, _, reasons = delivery.contract_aware_quality_gate(
            _node(value),
            {"choices": [{"finish_reason": "stop"}]},
            _answer(EXPECTED_HEADINGS),
        )
        self.assertFalse(passed)
        self.assertTrue(
            any(
                reason.startswith("missing-required-markdown-heading:")
                for reason in reasons
            )
        )

    def test_prompt_scopes_final_format_away_from_intermediate_node(self):
        value = contract(final_node=False)
        prompt = delivery.contract_aware_system_prompt(_node(value))
        self.assertIn("本节点是内部工作节点", prompt)
        self.assertIn("不得复制或采用原始任务中的最终报告格式", prompt)
        self.assertIn("本节点输出格式只遵循", prompt)

    def test_explicit_synthesis_prompt_owns_final_contract(self):
        value = contract(final_node=True)
        prompt = delivery.contract_aware_system_prompt(
            _node(value, functions=("synthesis",))
        )
        self.assertIn("本节点承载用户明确指定的最终交付契约", prompt)
        for heading in EXPECTED_HEADINGS:
            self.assertIn(heading, prompt)
        self.assertNotIn("本节点是内部工作节点", prompt)


if __name__ == "__main__":
    unittest.main()
