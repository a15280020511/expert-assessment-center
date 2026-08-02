from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
from resource_matrix import compile_v5_task_resources  # noqa: E402
from v5_production_ticket import _canonical_user_task  # noqa: E402
from v5_proposal_materializer import compact_resources_for_gpt  # noqa: E402


QUESTION = (
    "真实决策任务：用户在福州市担任小学夜班保安，独自在岗亭值班；"
    "岗亭无法申请学校有线网络或室内Wi-Fi，但移动4G/5G流量可用；"
    "每月流量需求约100GB以上；希望长期月成本尽量控制在20元人民币左右，"
    "可以接受一次性购买随身Wi-Fi设备。请比较方案A：继续使用手机热点，"
    "与方案B：购买并使用随身Wi-Fi。仅依据题面已知条件分析，不调用外部工具、"
    "不联网、不编造具体套餐价格。必须给出：1）关键假设与未知信息；"
    "2）一次性成本和月成本的比较公式及盈亏平衡阈值；"
    "3）稳定性、信号、电池、发热、携带、维护和故障风险；"
    "4）什么条件下选A、什么条件下选B；5）明确推荐；"
    "6）一个低成本、可撤销的7天验证步骤。事实、假设和推断必须分开。"
)

DELEGATION_NOTICE = (
    "委托边界：外部网页 GPT 只负责提交用户问题和证据、报告运行状态；"
    "不得替代专家分析。"
)


def _run(task: str) -> SimpleNamespace:
    return SimpleNamespace(
        task=task,
        minimum_context_length=16_384,
        max_completion_tokens=3_000,
    )


class V5AdmissionTaskIsolationTests(unittest.TestCase):
    def test_canonical_task_excludes_runtime_delegation_notice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ticket.json").write_text(
                json.dumps(
                    {
                        "task": {
                            "question": QUESTION,
                            "requirements": ["禁止使用外部工具"],
                            "language": "zh-CN",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            canonical, source = _canonical_user_task(
                root,
                DELEGATION_NOTICE + "\n\n" + QUESTION,
            )
        self.assertEqual("ticket.task", source)
        self.assertTrue(canonical.startswith(QUESTION))
        self.assertNotIn("委托边界", canonical)
        self.assertNotIn("报告运行状态", canonical)
        self.assertIn("禁止使用外部工具", canonical)
        self.assertIn("输出语言：zh-CN", canonical)

    def test_consumer_trial_compiles_without_local_selection_algorithm(self) -> None:
        run = _run(QUESTION)
        profile = model_market.classify_task(QUESTION, run)
        self.assertEqual(["business"], profile.domains)
        self.assertEqual("business", profile.primary_domain)
        self.assertFalse(profile.high_stakes)
        self.assertFalse(profile.long_context)
        self.assertEqual(16_384, profile.requested_context)

        resources = compile_v5_task_resources(profile, run)
        signals = resources["task_semantics"]["task_signals"]
        self.assertEqual(["business"], signals["active_domains"])
        self.assertNotIn("research", signals["active_domains"])
        self.assertNotIn("evidence_validation", signals["active_operations"])
        self.assertTrue(
            resources["semantic_input_policy"]["trial_validation_disambiguated"]
        )

        hard = {
            item["capability"]
            for matrix in resources["resource_matrices"]["matrices"]
            for item in matrix["hard_requirements"]
        }
        self.assertNotIn("domain:business", hard)
        self.assertNotIn("evidence_validation", hard)
        self.assertIn("adversarial_reasoning", hard)
        self.assertIn("synthesis", hard)

        compact = compact_resources_for_gpt(resources)
        self.assertTrue(compact)
        rendered = json.dumps(compact, ensure_ascii=False, sort_keys=True)
        for obsolete in (
            "solver_status",
            "objective_value",
            "candidate_score",
            "pareto_frontier",
            "cp_sat",
        ):
            self.assertNotIn(obsolete, rendered)


if __name__ == "__main__":
    unittest.main()
