import argparse
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import call_ledger  # noqa: E402
import execution_auditor  # noqa: E402
import hardened_runtime  # noqa: E402
import issue_ticket_hardened  # noqa: E402


def packet(*, private_output=None):
    data = {
        "task_id": "hardened-task-0001",
        "route": "expert-team",
        "task": {"question": "验证加固控制面", "requirements": ["输出实质结论"]},
        "execution_acceptance": ["核对调用次数", "核对Artifact"],
        "approved_budget": {"calls": 6},
    }
    if private_output is not None:
        data["private_output"] = private_output
    return data


class HardenedTicketTests(unittest.TestCase):
    def test_only_strict_actions_bot_state_headings_are_trusted(self):
        rows = [
            {"body": "## EXECUTION_COMPLETED", "user": {"login": "outsider"}},
            {
                "body": "# Expert report\nThe phrase EXECUTION_FAILED appears only as quoted analysis.",
                "user": {"login": "github-actions[bot]"},
            },
            {"body": "## EXECUTION_DEGRADED\ntrusted", "user": {"login": "github-actions[bot]"}},
        ]
        with mock.patch.object(issue_ticket_hardened.base, "_api_json", return_value=rows):
            bodies = list(issue_ticket_hardened._trusted_issue_comments("owner/repo", 1))
        self.assertEqual(bodies, ["## EXECUTION_DEGRADED\ntrusted"])

    def test_later_active_run_is_rejected_instead_of_silently_queued(self):
        payloads = [
            {"workflow_runs": [{"id": 100}, {"id": 200}]},
            {"workflow_runs": [{"id": 150}]},
        ]
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "x"}, clear=False), mock.patch.object(
            issue_ticket_hardened.base,
            "_api_json",
            side_effect=payloads,
        ):
            reason = issue_ticket_hardened._active_lower_run_reason("owner/repo", 200)
        self.assertIn("EXECUTION_BUSY", reason)
        self.assertIn("100", reason)

    def test_earliest_active_run_is_admitted(self):
        payloads = [
            {"workflow_runs": [{"id": 100}, {"id": 200}]},
            {"workflow_runs": []},
        ]
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "x"}, clear=False), mock.patch.object(
            issue_ticket_hardened.base,
            "_api_json",
            side_effect=payloads,
        ):
            reason = issue_ticket_hardened._active_lower_run_reason("owner/repo", 100)
        self.assertEqual(reason, "")

    def _prepare(self, payload):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        args = argparse.Namespace(
            event_path=None,
            issue_title="[execution] hardened",
            issue_body=json.dumps(payload),
            issue_number=20,
            actor="owner",
            author_association="OWNER",
            comment_body="",
            output_dir=temp.name,
        )
        output = Path(temp.name) / "github-output.txt"
        with mock.patch.dict(
            os.environ,
            {"GITHUB_REPOSITORY_OWNER": "owner", "GITHUB_OUTPUT": str(output)},
            clear=False,
        ), mock.patch.object(issue_ticket_hardened.base, "duplicate_reason", return_value=""):
            issue_ticket_hardened.prepare(args)
        return json.loads((Path(temp.name) / "ticket-status.json").read_text(encoding="utf-8"))

    def test_public_delivery_is_default_and_accepted(self):
        status = self._prepare(packet())
        self.assertTrue(status["accepted"])
        self.assertFalse(status["private_output"])
        self.assertIsNone(status["max_cost_usd"])
        self.assertEqual(status["cost_policy"], "no-hard-monetary-ceiling")

    def test_private_output_true_is_rejected(self):
        status = self._prepare(packet(private_output=True))
        self.assertFalse(status["accepted"])
        self.assertIn("no private delivery channel", status["reason"])

    def test_execution_acceptance_is_not_sent_to_experts(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        args = argparse.Namespace(
            event_path=None,
            issue_title="[execution] hardened",
            issue_body=json.dumps(packet()),
            issue_number=20,
            actor="owner",
            author_association="OWNER",
            comment_body="",
            output_dir=temp.name,
        )
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner"}, clear=False), \
             mock.patch.object(issue_ticket_hardened.base, "duplicate_reason", return_value=""):
            issue_ticket_hardened.prepare(args)
        task = (Path(temp.name) / "task.txt").read_text(encoding="utf-8")
        self.assertIn("输出实质结论", task)
        self.assertNotIn("核对调用次数", task)
        self.assertNotIn("核对Artifact", task)


class HardenedCostTests(unittest.TestCase):
    @staticmethod
    def result():
        return SimpleNamespace(
            seat_key="cross",
            attempts=[
                {
                    "model": "first/model",
                    "estimated_cost": 0.4,
                    "response_diagnostics": {"cost": 0.35},
                    "error": "truncated",
                },
                {"replacement": True},
                {
                    "model": "replacement/model",
                    "estimated_cost": 0.1,
                    "response_diagnostics": {"cost": 0.08},
                },
            ],
        )

    def test_no_limit_accounting_counts_failed_and_replacement_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            run = SimpleNamespace(output_dir=Path(temp), max_estimated_cost_usd=None)
            judge = {
                "choices": [{"finish_reason": "stop", "message": {"content": "done"}}],
                "usage": {"cost": 0.1},
            }
            total = hardened_runtime.enforce_post_judge_actual_budget(run, [self.result()], judge)
            self.assertAlmostEqual(total, 0.53)
            evidence = json.loads((Path(temp) / "cost-evidence.json").read_text())
            self.assertEqual(len(evidence["expert_attempts"]["entries"]), 2)
            self.assertIsNone(evidence["hard_cost_limit_usd"])

    def test_unknown_judge_cost_is_recorded_without_failing_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            run = SimpleNamespace(output_dir=Path(temp), max_estimated_cost_usd=None)
            judge = {"choices": [{"finish_reason": "stop", "message": {"content": "done"}}], "usage": {}}
            total = hardened_runtime.enforce_post_judge_actual_budget(run, [self.result()], judge)
            self.assertAlmostEqual(total, 0.43)
            evidence = json.loads((Path(temp) / "cost-evidence.json").read_text())
            self.assertEqual(evidence["status"], "unknown")

    def test_judge_contract_has_no_fixed_character_or_token_cap(self):
        payload = {"messages": [{"role": "system", "content": "judge"}, {"role": "user", "content": "task"}]}
        hardened_runtime.apply_judge_output_contract(payload)
        text = payload["messages"][0]["content"]
        self.assertNotIn("4200", text)
        self.assertIn("不得设置固定字符或Token上限", text)
        self.assertIn("不要求用满", text)
        self.assertIn("不要复述题目", text)


class LedgerAndAuditTests(unittest.TestCase):
    @staticmethod
    def payload(model):
        return {
            "model": model,
            "messages": [{"role": "user", "content": "test"}],
            "reasoning": {"exclude": True, "effort": "low"},
        }

    def _artifacts(self, root: Path):
        (root / "ticket-status.json").write_text(
            json.dumps({"accepted": True, "calls": 6, "private_output": False}),
            encoding="utf-8",
        )
        router_payload = self.payload("router/model")
        (root / "task-routing.json").write_text(
            json.dumps({
                "call_consumed": True,
                "model_id": "router/model",
                "request_payload": router_payload,
                "request_token_ceiling_sent": False,
                "estimated_cost_usd": 0.02,
                "response_diagnostics": {"cost": 0.01, "response_id": "route-1", "provider": "RouterProvider"},
            }),
            encoding="utf-8",
        )
        experts = []
        for seat, provider in (("core", "ProviderA"), ("cross", "ProviderB"), ("red", "ProviderC")):
            model = f"{seat}/model"
            attempts = [{
                "model": model,
                "payload": self.payload(model),
                "estimated_cost": 0.1,
                "response_diagnostics": {"cost": 0.08, "response_id": f"{seat}-1", "finish_reason": "stop", "provider": provider},
            }]
            experts.append({"seat_key": seat, "status": "success_complete", "attempts": attempts})
        experts[1]["attempts"] = [
            {
                "model": "cross/failed",
                "payload": self.payload("cross/failed"),
                "estimated_cost": 0.2,
                "response_diagnostics": {"cost": 0.18, "response_id": "cross-failed", "finish_reason": "length", "provider": "ProviderB"},
            },
            {"replacement": True},
            {
                "model": "cross/replacement",
                "payload": self.payload("cross/replacement"),
                "estimated_cost": 0.1,
                "response_diagnostics": {"cost": 0.08, "response_id": "cross-2", "finish_reason": "stop", "provider": "ProviderD"},
            },
        ]
        (root / "expert-responses.json").write_text(json.dumps(experts), encoding="utf-8")
        judge_payload = self.payload("judge/model")
        result = {
            "expert_results": experts,
            "judge_status": "success_complete",
            "judge": {"model_id": "judge/model"},
            "judge_request": judge_payload,
        }
        (root / "expert-team-result.json").write_text(json.dumps(result), encoding="utf-8")
        (root / "judge-response-diagnostics.json").write_text(
            json.dumps({"cost": 0.1, "estimated_cost": 0.12, "response_id": "judge-1", "finish_reason": "stop", "provider": "ProviderE"}),
            encoding="utf-8",
        )
        (root / "request-audit.json").write_text(
            json.dumps({
                "status": "PASS",
                "captured_request_count": 6,
                "expected_request_count": 6,
                "entries": [],
                "failures": [],
            }),
            encoding="utf-8",
        )
        report = "complete report\n"
        (root / "expert-team-report.md").write_text(report, encoding="utf-8")
        comments = root / "report-comments"
        comments.mkdir()
        (comments / "report-comment-001.md").write_text("part", encoding="utf-8")
        (comments / "report-comments-manifest.json").write_text(
            json.dumps({"report_sha256": hashlib.sha256(report.encode()).hexdigest(), "files": ["report-comment-001.md"]}),
            encoding="utf-8",
        )

    def test_ledger_reconstructs_all_six_calls_and_audit_degrades_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._artifacts(root)
            ledger = call_ledger.write_ledger(root)
            self.assertEqual(ledger["summary"]["call_count"], 6)
            self.assertEqual(ledger["summary"]["replacement_calls"], 1)
            self.assertEqual(ledger["summary"]["substantive_provider_count"], 5)
            audited = execution_auditor.audit(root, execute_outcome="success", publish_outcome="success")
            self.assertEqual(audited["status"], "DEGRADED")
            self.assertTrue(any("replacement" in item for item in audited["degradations"]))
            self.assertEqual(audited["checks"]["request_audit_status"], "PASS")

    def test_audit_falls_back_to_expert_responses_after_judge_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._artifacts(root)
            (root / "expert-team-result.json").unlink()
            (root / "expert-team-error.json").write_text(
                json.dumps({"error_code": "JUDGE_OUTPUT_TOO_SHORT", "stage": "judge", "message": "too short"}),
                encoding="utf-8",
            )
            call_ledger.write_ledger(root)
            audited = execution_auditor.audit(root, execute_outcome="failure", publish_outcome="skipped")
            self.assertEqual(audited["checks"]["experts_usable"], 3)
            self.assertEqual(audited["checks"]["experts_complete"], 3)
            self.assertEqual(audited["checks"]["expert_result_source"], "expert-responses.json")
            self.assertEqual(audited["primary_failure"]["code"], "JUDGE_OUTPUT_TOO_SHORT")


if __name__ == "__main__":
    unittest.main()
