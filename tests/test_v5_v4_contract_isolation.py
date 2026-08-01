from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import SelectedNode  # noqa: E402
import task_semantic_compiler as compiler  # noqa: E402
from v5_constitutional_runtime import (  # noqa: E402
    ConstitutionalExecutionEngine,
    ConstitutionalPromptPolicy,
    validate_scope_boundaries,
)
import v5_task_delivery_contract as contracts  # noqa: E402
from v5_task_constraints import closed_world_numeric_prompt  # noqa: E402

HEADINGS = [
    "题面事实",
    "计算与校验",
    "推断与未知",
    "结论与反转条件",
]
TASK = (
    "仅依据题面。方案A一次性投入1200元、每月300元；"
    "方案B一次性投入300元、每月450元；评估期24个月。\n"
    "执行要求：\n"
    "- 严格依次使用四个Markdown二级标题：题面事实、计算与校验、"
    "推断与未知、结论与反转条件；每节不得为空\n"
    "- 不得调用外部工具，不得引入题面外精确数量。"
)
PAID_TASK = (
    "仅依据题面完成封闭世界决策。方案A一次性投入1200元、每月300元；"
    "方案B一次性投入300元、每月450元；评估期24个月。"
    "题面给定并要求校验：盈亏平衡点为第6个月，方案A的24个月总成本为"
    "8400元，方案B为11100元，差额为2700元。不得调用外部工具、不得联网、"
    "不得引入题面外事实或其他精确数量。"
)


def node(contract, functions=("analysis",)):
    return SelectedNode(
        node_id="node-test",
        assigned_work=("work-test",),
        professional_capabilities={"analysis": 0.8, "synthesis": 0.8},
        functions=tuple(functions),
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "high"},
        parameter_profile={"supported_parameters": ["reasoning"]},
        model="openai/test-model",
        provider_endpoint="openai/test-model@provider-a",
        output_contract=dict(contract),
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.001,
        failure_probability=0.02,
        request_config={
            "provider": {
                "order": ["provider-a"],
                "only": ["provider-a"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


class V5V4ContractIsolationTests(unittest.TestCase):
    def test_inline_chinese_count_markdown_contract_is_extracted(self):
        contract = contracts.extract_explicit_markdown_contract(TASK)
        self.assertTrue(contract["explicit_markdown_contract"])
        self.assertEqual(HEADINGS, contract["exact_markdown_headings"])

    def test_synthesis_owns_inline_final_contract_internal_node_does_not(self):
        final = compiler._output_contract(TASK, {"synthesis": 1.0}, False)
        internal = compiler._output_contract(
            TASK,
            {"analysis": 1.0, "decision_comparison": 1.0},
            False,
        )
        self.assertEqual(HEADINGS, final["required_fields"])
        self.assertTrue(final["explicit_markdown_contract"])
        self.assertFalse(internal.get("explicit_markdown_contract", False))
        self.assertNotEqual(HEADINGS, internal["required_fields"])

    def test_internal_task_projection_removes_final_headings_but_keeps_facts(self):
        internal = compiler._output_contract(TASK, {"analysis": 1.0}, False)
        projected = contracts.project_task_for_node(TASK, internal)
        self.assertIn("1200元", projected)
        self.assertIn("不得调用外部工具", projected)
        for heading in HEADINGS:
            self.assertNotIn(heading, projected)
        self.assertIn("最终报告格式仅由最终综合节点执行", projected)

    def test_prompt_policy_projects_internal_task_and_preserves_final_task(self):
        internal_contract = compiler._output_contract(
            TASK, {"analysis": 1.0}, False
        )
        final_contract = compiler._output_contract(
            TASK, {"synthesis": 1.0}, False
        )
        policy = ConstitutionalPromptPolicy()
        internal_payload = policy.build_payload(
            node(internal_contract), TASK, []
        )
        final_payload = policy.build_payload(
            node(final_contract, functions=("synthesis",)), TASK, []
        )
        internal_user = internal_payload["messages"][1]["content"]
        final_user = final_payload["messages"][1]["content"]
        for heading in HEADINGS:
            self.assertNotIn(heading, internal_user)
            self.assertIn(heading, final_user)

        numeric = closed_world_numeric_prompt(TASK)
        self.assertIn("1200:yuan", numeric)
        self.assertIn("300:yuan", numeric)
        self.assertIn("450:yuan", numeric)
        self.assertIn("24:month", numeric)
        self.assertIn("算术中间结果", numeric)
        self.assertIn("直接等式", numeric)
        self.assertIn("定性表述", numeric)
        self.assertNotIn("2550:yuan", numeric)
        for payload in (internal_payload, final_payload):
            system = payload["messages"][0]["content"]
            self.assertIn(numeric, system)

    def test_closed_world_rejects_v4_novel_month_and_currency_values(self):
        rejected = (
            "若评估期为5个月，阈值为2550元；另以12个月、150元、3000元、"
            "7200元和10800元作为中间结果或派生情景。"
        )
        rendered = ";".join(validate_scope_boundaries(PAID_TASK, rejected))
        for token in (
            "5:month",
            "12:month",
            "150:yuan",
            "2550:yuan",
            "3000:yuan",
            "7200:yuan",
            "10800:yuan",
        ):
            self.assertIn(token, rendered)

        compliant = (
            "## 题面事实\n"
            "方案A一次性投入1200元、每月300元；方案B一次性投入300元、"
            "每月450元；评估期24个月。\n"
            "## 计算与校验\n"
            "1200元 + 300元 × 24个月 = 8400元。\n"
            "300元 + 450元 × 24个月 = 11100元。\n"
            "11100元 - 8400元 = 2700元。\n"
            "1200元 + 300元 × 6个月 = 300元 + 450元 × 6个月。\n"
            "## 推断与未知\n"
            "题面未给其他情景参数，不作数值外推。\n"
            "## 结论与反转条件\n"
            "按题面总成本选择方案A；反转条件仅作定性表述。"
        )
        self.assertEqual([], validate_scope_boundaries(PAID_TASK, compliant))

    def test_company_audit_separates_degraded_from_strict_success(self):
        audit = ConstitutionalExecutionEngine._actual_company_audit(
            {
                "node_results": [
                    {
                        "node_id": "n1",
                        "resolved_model": "deepseek/model-a",
                        "status": "success",
                        "attempts": [
                            {"model": "deepseek/model-a", "status": "passed"}
                        ],
                    },
                    {
                        "node_id": "n2",
                        "resolved_model": "xiaomi/model-b",
                        "status": "success_degraded",
                        "attempts": [
                            {
                                "model": "xiaomi/model-b",
                                "status": "quality_gate_failed",
                            }
                        ],
                    },
                ]
            }
        )
        self.assertEqual(["deepseek"], [
            row["company"] for row in audit["successful_node_models"]
        ])
        self.assertEqual(["xiaomi"], [
            row["company"] for row in audit["degraded_node_models"]
        ])
        self.assertTrue(audit["degraded_nodes_are_not_labeled_strict_success"])

    def test_v4_dry_run_binds_exact_contract_only_to_final_nodes(self):
        with tempfile.TemporaryDirectory(prefix="v5-v4-contract-") as directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "open-model-market/v5_constitutional_pipeline.py"),
                    "--task", TASK,
                    "--catalog-file", str(ROOT / "tests/fixtures/models.json"),
                    "--endpoint-file", str(ROOT / "tests/fixtures/endpoints.json"),
                    "--dry-run",
                    "--maximum-total-calls", "4",
                    "--maximum-recovery-calls", "1",
                    "--cost-anomaly-usd", "0.25",
                    "--quality-tier", "value",
                    "--output-dir", directory,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            graph = json.loads(
                (Path(directory) / "v5-execution-graph.json").read_text()
            )
        finals = set(graph["final_nodes"])
        self.assertTrue(finals)
        for row in graph["nodes"]:
            is_final = row["node_id"] in finals
            profile = row["parameter_profile"]
            if is_final:
                self.assertEqual("exact-markdown", profile["output_contract_kind"])
                self.assertEqual(HEADINGS, row["output_contract"]["required_fields"])
            else:
                self.assertEqual("generic", profile["output_contract_kind"])
                self.assertNotEqual(HEADINGS, row["output_contract"]["required_fields"])


if __name__ == "__main__":
    unittest.main()
