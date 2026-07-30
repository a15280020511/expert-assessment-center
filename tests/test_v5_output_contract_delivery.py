import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_candidate_diversity  # noqa: E402
import v5_executor  # noqa: E402
import v5_output_contract_delivery as delivery  # noqa: E402
from execution_graph import SelectedNode  # noqa: E402


def _node(*, machine_readable: bool = True) -> SelectedNode:
    return SelectedNode(
        node_id="node-contract-test",
        assigned_work=("work-security-audit",),
        professional_capabilities={"security": 0.8},
        functions=("analysis",),
        prompt_profile={"modules": ["structured_delivery", "evidence_discipline"]},
        reasoning_profile={"enabled": False},
        parameter_profile={},
        model="vendor/model-a",
        provider_endpoint="vendor/model-a@provider-a/default",
        output_contract={
            "required_fields": [
                "assumptions",
                "evidence_gaps",
                "conclusions",
                "recommendations",
            ],
            "machine_readable_required": machine_readable,
            "must_separate_fact_assumption_inference": True,
        },
        estimated_quality=0.70,
        quality_uncertainty=0.10,
        estimated_cost=0.001,
        failure_probability=0.05,
        request_config={},
    )


class TestV5OutputContractDelivery(unittest.TestCase):
    def test_machine_readable_prompt_demands_actual_json_fields(self):
        prompt = delivery.contract_aware_system_prompt(_node())
        self.assertIn("最终响应必须只包含一个合法JSON对象", prompt)
        self.assertIn('"assumptions"', prompt)
        self.assertIn('"evidence_gaps"', prompt)
        self.assertIn("禁止复述输出契约", prompt)
        self.assertIn("优先保证所有必填键存在且JSON语法完整闭合", prompt)
        self.assertNotIn("输出契约：{", prompt)
        self.assertNotIn('"machine_readable_required": true', prompt)

    def test_non_machine_readable_prompt_still_forbids_schema_echo(self):
        prompt = delivery.contract_aware_system_prompt(
            _node(machine_readable=False)
        )
        self.assertIn("最终响应必须直接交付以下内容", prompt)
        self.assertIn("assumptions", prompt)
        self.assertIn("禁止复述输出契约", prompt)
        self.assertNotIn("最终响应必须只包含一个合法JSON对象", prompt)

    def test_valid_direct_json_delivery_passes_quality_gate(self):
        node = _node()
        answer = json.dumps(
            {
                "assumptions": [
                    "输入代码与配置是完整审计范围，未提供的部署事实不作确定判断。"
                ],
                "evidence_gaps": [
                    "缺少生产环境权限边界、密钥轮换记录和容器隔离策略证据。"
                ],
                "conclusions": [
                    "默认凭据、命令拼接和跨租户结果读取构成可验证的高风险失败路径。"
                ],
                "recommendations": [
                    "删除默认凭据，使用参数化执行，实施租户级目录隔离，并增加拒绝测试和回滚门。"
                ],
            },
            ensure_ascii=False,
        )
        passed, score, reasons = delivery.contract_aware_quality_gate(
            node,
            {"choices": [{"finish_reason": "stop"}]},
            answer,
        )
        self.assertTrue(passed)
        self.assertGreater(score, 0.6)
        self.assertFalse(reasons)

    def test_contract_metadata_echo_fails_quality_gate(self):
        node = _node()
        answer = json.dumps(
            {
                "machine_readable_required": True,
                "must_separate_fact_assumption_inference": True,
                "required_fields": [
                    "assumptions",
                    "evidence_gaps",
                    "conclusions",
                    "recommendations",
                ],
                "description": "这是输出契约定义而不是任务分析结果。" * 8,
            },
            ensure_ascii=False,
        )
        passed, score, reasons = delivery.contract_aware_quality_gate(
            node,
            {"choices": [{"finish_reason": "stop"}]},
            answer,
        )
        self.assertFalse(passed)
        self.assertLess(score, 0.6)
        self.assertIn("contract-metadata-echo", reasons)
        self.assertTrue(
            any(reason.startswith("missing-required-json-keys:") for reason in reasons)
        )

    def test_formal_v5_safety_installer_patches_prompt_and_gate(self):
        v5_candidate_diversity.install()
        self.assertIs(
            v5_executor._system_prompt,
            delivery.contract_aware_system_prompt,
        )
        self.assertIs(
            v5_executor.quality_gate,
            delivery.contract_aware_quality_gate,
        )
        prompt = v5_executor._system_prompt(_node())
        self.assertIn("JSON语法完整闭合", prompt)


if __name__ == "__main__":
    unittest.main()
