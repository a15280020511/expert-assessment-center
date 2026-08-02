from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_production_ticket import _canonical_user_task  # noqa: E402
from v5_task_envelope import build_task_envelope  # noqa: E402


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

    def test_consumer_trial_uses_only_minimal_task_envelope(self) -> None:
        envelope = build_task_envelope(
            QUESTION,
            minimum_context_length=16_384,
            maximum_completion_tokens=3_000,
        )
        self.assertEqual("~openai/gpt-latest", envelope["decomposition_authority"])
        self.assertFalse(envelope["local_task_classification_used"])
        self.assertFalse(envelope["local_complexity_scoring_used"])
        self.assertFalse(envelope["local_atomic_work_generation_used"])
        self.assertFalse(envelope["local_resource_matrix_used"])
        self.assertFalse(envelope["task_constraints"]["external_tools_allowed"])
        rendered = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
        for obsolete in (
            "active_domains",
            "active_operations",
            "capability_weights",
            "resource_matrices",
            "solver_status",
            "objective_value",
            "candidate_score",
            "pareto_frontier",
            "cp_sat",
        ):
            self.assertNotIn(obsolete, rendered)


if __name__ == "__main__":
    unittest.main()
