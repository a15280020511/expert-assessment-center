import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_dynamic_prompt_delivery as dynamic_prompt  # noqa: E402
import v5_output_contract_delivery as delivery  # noqa: E402
from execution_graph import SelectedNode  # noqa: E402
from v5_runtime import ProductionRuntime, RuntimeConfig  # noqa: E402


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
        request_config={
            "provider": {
                "only": ["provider-a/default"],
                "order": ["provider-a/default"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


class TestV5OutputContractDelivery(unittest.TestCase):
    def runtime(self):
        return ProductionRuntime(RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            quality_tier="value",
        ))

    def test_machine_readable_prompt_demands_actual_json_fields(self):
        with patch.dict(os.environ, {delivery.COMPACT_MODE_ENV: ""}, clear=False):
            prompt = delivery.contract_aware_system_prompt(_node())
        self.assertIn("最终响应必须只包含一个合法JSON对象", prompt)
        self.assertIn('"assumptions"', prompt)
        self.assertIn('"evidence_gaps"', prompt)
        self.assertIn("禁止复述输出契约", prompt)
        self.assertIn("优先保证所有必填键存在且JSON语法完整闭合", prompt)
        self.assertNotIn("输出契约：{", prompt)
        self.assertNotIn('"machine_readable_required": true', prompt)
        self.assertNotIn("微型Canary精简模式", prompt)
        self.assertNotIn("450个中文字符", prompt)

    def test_compact_mode_is_explicit_and_canary_scoped(self):
        with patch.dict(os.environ, {delivery.COMPACT_MODE_ENV: "1"}, clear=False):
            prompt = delivery.contract_aware_system_prompt(_node())
        self.assertIn("微型Canary精简模式", prompt)
        self.assertIn("每个普通字段最多2条", prompt)
        self.assertIn("每条不超过48个中文字符", prompt)
        self.assertIn("acceptance_tests字段最多3条", prompt)
        self.assertIn("450个中文字符以内", prompt)
        self.assertIn("必须在输出上限前闭合所有括号和引号", prompt)

    def test_compact_mode_tightens_many_field_contracts(self):
        node = _node()
        node = SelectedNode(**{
            **node.to_dict(),
            "assigned_work": tuple(node.assigned_work),
            "functions": tuple(node.functions),
            "output_contract": {
                **dict(node.output_contract),
                "required_fields": [
                    "assumptions",
                    "evidence_gaps",
                    "conclusions",
                    "recommendations",
                    "risks",
                    "acceptance_tests",
                ],
            },
        })
        with patch.dict(os.environ, {delivery.COMPACT_MODE_ENV: "true"}, clear=False):
            prompt = delivery.contract_aware_system_prompt(node)
        self.assertIn("每个普通字段最多1条", prompt)
        self.assertIn("每条不超过36个中文字符", prompt)

    def test_non_machine_readable_prompt_still_forbids_schema_echo(self):
        with patch.dict(os.environ, {delivery.COMPACT_MODE_ENV: ""}, clear=False):
            prompt = delivery.contract_aware_system_prompt(_node(machine_readable=False))
        self.assertIn("最终响应必须直接交付以下内容", prompt)
        self.assertIn("assumptions", prompt)
        self.assertIn("禁止复述输出契约", prompt)
        self.assertIn("完全一致的Markdown二级标题", prompt)
        for field in ["assumptions", "evidence_gaps", "conclusions", "recommendations"]:
            self.assertIn(f"## {field}", prompt)
        self.assertIn("不得把多个必填字段合并到同一标题", prompt)
        self.assertNotIn("最终响应必须只包含一个合法JSON对象", prompt)
        self.assertNotIn("微型Canary精简模式", prompt)

    def test_valid_direct_json_delivery_passes_quality_gate(self):
        node = _node()
        answer = json.dumps({
            "assumptions": ["输入代码与配置是完整审计范围，未提供的部署事实不作确定判断。"],
            "evidence_gaps": ["缺少生产环境权限边界、密钥轮换记录和容器隔离策略证据。"],
            "conclusions": ["默认凭据、命令拼接和跨租户结果读取构成可验证的高风险失败路径。"],
            "recommendations": ["删除默认凭据，使用参数化执行，实施租户级目录隔离，并增加拒绝测试和回滚门。"],
        }, ensure_ascii=False)
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
        answer = json.dumps({
            "machine_readable_required": True,
            "must_separate_fact_assumption_inference": True,
            "required_fields": [
                "assumptions", "evidence_gaps", "conclusions", "recommendations"
            ],
            "description": "这是输出契约定义而不是任务分析结果。" * 8,
        }, ensure_ascii=False)
        passed, score, reasons = delivery.contract_aware_quality_gate(
            node,
            {"choices": [{"finish_reason": "stop"}]},
            answer,
        )
        self.assertFalse(passed)
        self.assertLess(score, 0.6)
        self.assertIn("contract-metadata-echo", reasons)
        self.assertTrue(any(reason.startswith("missing-required-json-keys:") for reason in reasons))

    def test_formal_runtime_layers_dynamic_role_over_contract_prompt(self):
        runtime = self.runtime()
        node = _node()
        with patch.dict(os.environ, {delivery.COMPACT_MODE_ENV: ""}, clear=False):
            payload = runtime.build_node_payload(node, "审计任务", [])
        prompt = payload["messages"][0]["content"]
        self.assertEqual(prompt, dynamic_prompt.dynamic_system_prompt(node))
        self.assertIn("JSON语法完整闭合", prompt)
        self.assertNotIn("微型Canary精简模式", prompt)
        passed, _, _ = runtime.quality_policy.evaluate(
            node,
            {"choices": [{"finish_reason": "stop"}]},
            json.dumps({
                "assumptions": ["A" * 80],
                "evidence_gaps": ["B" * 80],
                "conclusions": ["C" * 80],
                "recommendations": ["D" * 80],
            }, ensure_ascii=False),
        )
        self.assertTrue(passed)

    def test_formal_workflow_does_not_force_compact_mode_or_installers(self):
        workflow = (ROOT / ".github" / "workflows" / "execution-ticket.yml").read_text(encoding="utf-8")
        production = (ROOT / "open-model-market" / "v5_production_ticket.py").read_text(encoding="utf-8")
        runtime = (ROOT / "open-model-market" / "v5_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("V5_COMPACT_OUTPUT_CONTRACT", workflow)
        self.assertNotIn(".install()", production)
        self.assertIn("PromptPolicy", runtime)
        self.assertIn("QualityGatePolicy", runtime)


if __name__ == "__main__":
    unittest.main()
