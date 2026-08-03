from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConstitutionHardRuleTests(unittest.TestCase):
    def test_constitution_is_canonical_and_free_first(self) -> None:
        text = (ROOT / "CONSTITUTION.md").read_text(encoding="utf-8")
        self.assertIn("最高宪法", text)
        self.assertIn("免费模型优先，付费模型仅用于正式生产", text)
        self.assertIn("零模型调用的确定性验证", text)
        self.assertIn("免费模型影子验证", text)
        self.assertIn("不得自动升级为付费调用", text)
        self.assertIn("付费模型只允许用于", text)
        self.assertIn("同一目标 SHA", text)
        self.assertIn("不能冒充或替代 GPT latest / Claude Opus latest", text)

    def test_constitution_requires_global_company_uniqueness(self) -> None:
        text = (ROOT / "CONSTITUTION.md").read_text(encoding="utf-8")
        self.assertIn("专家团模型公司全局唯一", text)
        self.assertIn("初始专家节点之间，模型公司全局唯一", text)
        self.assertIn("恢复专家与初始专家之间，模型公司全局唯一", text)
        self.assertIn("不同模型名称、不同版本、不同 Provider", text)
        self.assertIn("已失败的专家调用也计入公司占用", text)
        self.assertIn("不得降低约束、复用公司", text)

    def test_readme_explicitly_binds_to_constitution(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("[`CONSTITUTION.md`](CONSTITUTION.md)", text)
        self.assertIn("免费路径失败时必须失败关闭", text)
        self.assertIn("同一任务内所有初始专家和恢复专家必须来自不同模型公司", text)

    def test_company_uniqueness_remains_a_deterministic_hard_gate(self) -> None:
        text = (
            ROOT / "open-model-market" / "execution_graph_validator.py"
        ).read_text(encoding="utf-8")
        self.assertIn("candidate_company(node)", text)
        self.assertIn('"model_company_reuse"', text)
        self.assertIn("len(company_node_ids) > 1", text)

    def test_free_first_policy_fails_closed_before_paid_authorization(self) -> None:
        text = (
            ROOT / "open-model-market" / "v5_free_first_preflight.py"
        ).read_text(encoding="utf-8")
        self.assertIn("simulation-used-model-calls", text)
        self.assertIn("free-canary-used-paid-call", text)
        self.assertIn("free-canary-positive-cost", text)
        self.assertIn("shadow-used-paid-call", text)
        self.assertIn("paid_acceptance_allowed", text)
        self.assertIn('"production_promotion_allowed": False', text)

    def test_paid_workflow_orders_free_gates_before_exact_paid_execution(self) -> None:
        text = (
            ROOT
            / ".github"
            / "workflows"
            / "v5-final-paid-claude-acceptance-20260803.yml"
        ).read_text(encoding="utf-8")
        zero_call = text.index(
            "Complete all zero-call release gates before paid execution"
        )
        free_canary = text.index(
            "Run zero-cost free Canary and API-key limit preflight"
        )
        paid = text.index("Execute one bounded exact production task")
        self.assertLess(zero_call, free_canary)
        self.assertLess(free_canary, paid)
        self.assertIn('"paid_model_calls": 0', text)
        self.assertIn("free Canary returned positive cost", text)


if __name__ == "__main__":
    unittest.main()
