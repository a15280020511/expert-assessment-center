from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "open-model-market" / "v5_ticket_gate.py"
SPEC = importlib.util.spec_from_file_location("v5_ticket_gate", MODULE_PATH)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class V5TicketGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "ticket-artifacts"
        self.root.mkdir()
        self.ticket = {
            "task_id": "v5-real-decision-test-0001",
            "route": "expert-team",
            "task": {
                "question": "比较手机热点与随身 Wi-Fi，并给出可撤销验证方案。",
                "requirements": ["区分事实与假设", "不得调用外部工具"],
            },
            "approved_budget": {
                "calls": 5,
                "maximum_recovery_calls": 1,
                "cost_policy": "prompt_led_soft_governance",
                "cost_anomaly_usd": 0.25,
            },
            "cost_threshold_can_stop_execution": False,
            "private_output": False,
        }
        self.status = {
            "accepted": True,
            "task_id": self.ticket["task_id"],
            "task_fingerprint": gate.task_fingerprint(self.ticket),
            "calls": 5,
            "maximum_recovery_calls": 1,
            "maximum_initial_calls": 1,
            "maximum_replacements": 1,
                        "cost_anomaly_usd": 0.25,
            "cost_policy": "prompt_led_soft_governance",
            "cost_threshold_can_stop_execution": False,
            "private_output": False,
            "is_retry": False,
            "retry_id": "",
            "trigger_mode": "run",
            "execution_id": self.ticket["task_id"],
            "analysis_owner": "github-v5-gpt-claude-expert-graph",
            "authoritative_trigger": "issue_comment.created",
            "runtime_version": "v5-native-runtime-1",
            "fallback_policy": "disabled-fail-closed",
            "legacy_runtime_present": False,
            "cross_task_history_used": False,
        }
        self._write_fixture()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_fixture(self) -> None:
        (self.root / "ticket.json").write_text(
            json.dumps(self.ticket, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.root / "ticket-status.json").write_text(
            json.dumps(self.status, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.root / "task.txt").write_text(
            "委托边界：专家禁止使用外部工具。\n\n"
            + self.ticket["task"]["question"],
            encoding="utf-8",
        )

    @staticmethod
    def _sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _run(self) -> dict:
        return gate.run_gate(
            self.root,
            expected_calls=5,
            expected_recovery_calls=1,
            expected_cost_anomaly_usd=0.25,
        )

    def test_valid_gate_passes_without_mutating_admission_files(self) -> None:
        immutable = [
            self.root / "ticket.json",
            self.root / "ticket-status.json",
            self.root / "task.txt",
        ]
        before = {path.name: self._sha(path) for path in immutable}
        result = self._run()
        after = {path.name: self._sha(path) for path in immutable}
        self.assertEqual("PASS", result["status"])
        self.assertEqual(before, after)
        self.assertEqual(0, result["model_calls_performed"])
        self.assertFalse(result["mutation_performed"])
        self.assertEqual(
            result["task_fingerprint"],
            self.status["task_fingerprint"],
        )

    def test_workflow_call_mismatch_fails_closed(self) -> None:
        with self.assertRaises(gate.TicketGateError):
            gate.run_gate(
                self.root,
                expected_calls=6,
                expected_recovery_calls=1,
                    expected_cost_anomaly_usd=0.25,
            )
        failure = json.loads(
            (self.root / "ticket-gate.json").read_text(encoding="utf-8")
        )
        self.assertEqual("FAIL", failure["status"])
        self.assertEqual(0, failure["model_calls_performed"])

    def test_anomaly_mismatch_fails_closed(self) -> None:
        self.status["cost_anomaly_usd"] = 0.24
        self._write_fixture()
        with self.assertRaises(gate.TicketGateError):
            self._run()
        failure = json.loads(
            (self.root / "ticket-gate.json").read_text(encoding="utf-8")
        )
        self.assertTrue(
            any("cost advisory" in item for item in failure["errors"])
        )

    def test_task_text_tampering_fails_closed(self) -> None:
        (self.root / "task.txt").write_text(
            "different task",
            encoding="utf-8",
        )
        with self.assertRaises(gate.TicketGateError):
            self._run()

    def test_controlled_retry_contract_passes(self) -> None:
        self.status.update(
            {
                "is_retry": True,
                "retry_id": "retry-gate-fix-0001",
                "trigger_mode": "retry",
                "execution_id": "",
            }
        )
        self._write_fixture()
        result = self._run()
        self.assertEqual("PASS", result["status"])
        self.assertEqual("retry", result["trigger_mode"])

    def test_run_execution_id_mismatch_fails_closed(self) -> None:
        self.status["execution_id"] = "different-execution-id"
        self._write_fixture()
        with self.assertRaises(gate.TicketGateError):
            self._run()


if __name__ == "__main__":
    unittest.main()
