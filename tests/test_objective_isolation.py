import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_issue_ticket as issue_ticket
from v5_ticket_identity import task_fingerprint  # noqa: E402


class ObjectiveIsolationTests(unittest.TestCase):
    @staticmethod
    def packet(objective="验证日志、Provider、Artifact和费用语义", requirement="计算成本"):
        return {
            "task_id": "objective-isolation-0001",
            "route": "expert-team",
            "objective": objective,
            "task": {
                "question": "比较三个仓库夜间值守方案并给出唯一推荐。",
                "requirements": [requirement, "使用中文"],
                "language": "zh-CN",
            },
            "evidence": {"note": "所有数据均为测试假设。"},
            "approved_budget": {"calls": 4},
            "private_output": False,
        }

    def test_objective_is_metadata_only_not_model_task_text(self):
        packet = self.packet()
        text = issue_ticket._substantive_task_text(packet)
        self.assertIn("比较三个仓库夜间值守方案", text)
        self.assertIn("计算成本", text)
        self.assertIn("所有数据均为测试假设", text)
        self.assertNotIn("验证日志", text)
        self.assertNotIn("Provider", text)
        self.assertNotIn("Artifact", text)
        self.assertNotIn("费用语义", text)

    def test_execution_objective_cannot_change_substantive_fingerprint(self):
        first = task_fingerprint(self.packet("验收日志系统"))
        second = task_fingerprint(self.packet("验收Artifact和Provider"))
        self.assertEqual(first, second)

    def test_substantive_requirements_do_change_fingerprint(self):
        first = task_fingerprint(self.packet(requirement="计算成本"))
        second = task_fingerprint(self.packet(requirement="分析安全风险"))
        self.assertNotEqual(first, second)

    def test_prepare_records_metadata_delivery_and_rewrites_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packet = self.packet()
            (root / "ticket-status.json").write_text(
                json.dumps({"accepted": True}), encoding="utf-8"
            )
            (root / "ticket.json").write_text(json.dumps(packet), encoding="utf-8")
            text = issue_ticket._substantive_task_text(packet)
            (root / "task.txt").write_text(text, encoding="utf-8")
            self.assertNotIn(packet["objective"], (root / "task.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
