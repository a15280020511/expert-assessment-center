import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import SelectedNode  # noqa: E402
import v5_dynamic_prompt_delivery as delivery  # noqa: E402
import v5_output_contract_delivery as contracts  # noqa: E402


def node(role="商业与财务·决策优化复合节点"):
    return SelectedNode(
        node_id="n1",
        assigned_work=("w1",),
        professional_capabilities={"domain:business": 0.9},
        functions=("decision_comparison",),
        prompt_profile={
            "modules": ["decision_comparison"],
            "professional_role": role,
            "dominant_domains": ["business", "legal"],
            "cognitive_operations": ["decision_comparison"],
        },
        reasoning_profile={"reasoning_enabled": True, "effort": "high"},
        parameter_profile={},
        model="vendor/model",
        provider_endpoint="vendor/model@provider",
        output_contract={
            "required_fields": ["conclusions"],
            "machine_readable_required": False,
        },
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.02,
        failure_probability=0.05,
        request_config={
            "provider": {
                "order": ["provider"],
                "only": ["provider"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


class TestV5DynamicPromptDelivery(unittest.TestCase):
    def test_dynamic_role_and_delivery_contract_are_both_present(self):
        prompt = delivery.dynamic_system_prompt(node())
        self.assertIn("动态专业角色：商业与财务·决策优化复合节点", prompt)
        self.assertIn("角色依据领域：business, legal", prompt)
        self.assertIn("不授予任何外部工具", prompt)
        self.assertIn("禁止调用", prompt)
        self.assertIn("最终响应必须直接交付以下内容", prompt)
        self.assertIn("禁止复述输出契约", prompt)

    def test_missing_role_preserves_contract_aware_prompt(self):
        plain = node(role="")
        self.assertEqual(
            delivery.dynamic_system_prompt(plain),
            contracts.contract_aware_system_prompt(plain),
        )


if __name__ == "__main__":
    unittest.main()
