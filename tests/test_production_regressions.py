import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import expert_team_hardened  # noqa: E402
import issue_ticket_hardened  # noqa: E402


class ProductionRegressionTests(unittest.TestCase):
    def test_degraded_state_is_retryable_without_recursive_parser(self):
        state = issue_ticket_hardened._execution_state([
            "## EXECUTION_ACCEPTED\naccepted",
            "## EXECUTION_DEGRADED\npartial",
        ])
        self.assertTrue(state["degraded"])
        self.assertTrue(state["failed"])
        self.assertFalse(state["completed"])

    def test_router_payload_token_ceilings_are_removed(self):
        payload = {
            "max_tokens": 1200,
            "max_completion_tokens": 1200,
            "reasoning": {"max_tokens": 400},
        }
        result = expert_team_hardened._remove_token_ceilings(payload)
        self.assertNotIn("max_tokens", result)
        self.assertNotIn("max_completion_tokens", result)
        self.assertEqual(result["reasoning"], {"effort": "low", "exclude": True})
        self.assertEqual(expert_team_hardened._token_ceiling_paths(result), [])

    def test_request_row_rejects_missing_or_forbidden_controls(self):
        missing = expert_team_hardened._request_row("judge", None)
        self.assertEqual(missing["status"], "FAIL")
        bad = expert_team_hardened._request_row(
            "expert",
            {"model": "sample/model", "max_tokens": 1000, "tools": []},
        )
        self.assertEqual(bad["status"], "FAIL")
        self.assertEqual(bad["token_ceiling_paths"], ["max_tokens"])
        self.assertEqual(bad["forbidden_request_fields"], ["tools"])

    def test_post_run_annotation_captures_requests_and_labels_estimate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = {
                "model": "sample/model",
                "messages": [{"role": "user", "content": "test"}],
                "reasoning": {"exclude": True, "effort": "low"},
            }
            (root / "task-routing.json").write_text(
                json.dumps({"call_consumed": True, "model_id": "router/model"}), encoding="utf-8"
            )
            experts = []
            for seat in ("core", "cross", "red"):
                experts.append({
                    "seat_key": seat,
                    "attempts": [{"model": f"{seat}/model", "payload": {**payload, "model": f"{seat}/model"}}],
                })
            (root / "expert-responses.json").write_text(json.dumps(experts), encoding="utf-8")
            (root / "judge-attempts.json").write_text(
                json.dumps([{"attempt_index": 1, "model": "judge/model"}]), encoding="utf-8"
            )
            (root / "model-selection.json").write_text(
                json.dumps({"estimated_cost_usd": 1.5}), encoding="utf-8"
            )
            (root / "expert-team-report.md").write_text(
                "- Estimated cost: `$1.500000`\n", encoding="utf-8"
            )
            previous_router = expert_team_hardened._LAST_ROUTER_REQUEST
            previous_judges = list(expert_team_hardened._JUDGE_REQUESTS)
            try:
                expert_team_hardened._LAST_ROUTER_REQUEST = {**payload, "model": "router/model"}
                expert_team_hardened._JUDGE_REQUESTS[:] = [{**payload, "model": "judge/model"}]
                expert_team_hardened._annotate_post_run_artifacts(root)
            finally:
                expert_team_hardened._LAST_ROUTER_REQUEST = previous_router
                expert_team_hardened._JUDGE_REQUESTS[:] = previous_judges

            request_audit = json.loads((root / "request-audit.json").read_text(encoding="utf-8"))
            self.assertEqual(request_audit["status"], "PASS")
            self.assertEqual(request_audit["captured_request_count"], 5)
            routing = json.loads((root / "task-routing.json").read_text(encoding="utf-8"))
            self.assertFalse(routing["request_token_ceiling_sent"])
            selection = json.loads((root / "model-selection.json").read_text(encoding="utf-8"))
            self.assertEqual(selection["estimated_cost_policy"], "provider-max-theoretical-not-a-limit")
            self.assertEqual(selection["provider_max_theoretical_estimated_cost_usd"], 1.5)
            report = (root / "expert-team-report.md").read_text(encoding="utf-8")
            self.assertIn("Provider-max theoretical estimate (not a limit)", report)

    def test_completed_judge_call_is_recorded_once(self):
        with tempfile.TemporaryDirectory() as temp:
            history_path = Path(temp) / "history.json"
            run = SimpleNamespace(history_path=history_path)
            response = {
                "id": "response-1",
                "choices": [{"finish_reason": "stop", "message": {"content": "done"}}],
                "usage": {"cost": 0.1, "completion_tokens": 100},
            }
            expert_team_hardened._record_failed_or_partial_attempt(
                run, "sample/decision", 0.1, response, 1.0, None
            )
            self.assertFalse(history_path.exists())
            expert_team_hardened._record_complete_final_once(
                run, "sample/decision", 0.1, response, 1.0, None
            )
            history = json.loads(history_path.read_text(encoding="utf-8"))
            self.assertEqual(history["models"]["sample/decision"]["calls"], 1)


if __name__ == "__main__":
    unittest.main()
