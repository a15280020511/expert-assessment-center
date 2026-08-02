from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_claude_red_team_policy import (  # noqa: E402
    CLAUDE_RED_TEAM_MAX_INPUT_CHARS,
    CLAUDE_RED_TEAM_MAX_OUTPUT_CHARS,
    CLAUDE_RED_TEAM_MAX_OUTPUT_TOKENS,
    CLAUDE_RED_TEAM_MODEL,
    RedTeamScope,
    build_claude_red_team_request,
    canonical_review_input,
    forbidden_claude_capabilities,
    parse_claude_red_team_verdict,
)


TASK_DIGEST = "a" * 64
PROPOSAL_DIGEST = "b" * 64
INFORMATION_DIGEST = "c" * 64


def internal_payload() -> dict:
    return {
        "task_digest": TASK_DIGEST,
        "proposal_digest": PROPOSAL_DIGEST,
        "approved_total_calls": 6,
        "approved_recovery_calls": 1,
        "cost_anomaly_usd": 0.35,
        "required_work": ["work-1", "work-2"],
        "nodes": [
            {
                "node_id": "node-1",
                "candidate_id": "candidate-1",
                "work_ids": ["work-1"],
                "model": "mistralai/mistral-large",
                "company": "mistral",
                "provider": "mistral",
                "estimated_cost_usd": 0.01,
                "contract_kind": "analysis",
                "recovery_candidate_ids": ["candidate-3"],
            },
            {
                "node_id": "node-2",
                "candidate_id": "candidate-2",
                "work_ids": ["work-2"],
                "model": "deepseek/deepseek-v3",
                "company": "deepseek",
                "provider": "deepinfra",
                "estimated_cost_usd": 0.01,
                "contract_kind": "synthesis",
                "recovery_candidate_ids": [],
            },
        ],
        "edges": [{"source": "node-1", "target": "node-2"}],
    }


def external_payload() -> dict:
    return {
        "task_digest": TASK_DIGEST,
        "information_digest": INFORMATION_DIGEST,
        "contract_kind": "closed-world-fact-review",
        "claims": [
            {
                "claim_id": "claim-1",
                "label": "fact",
                "source": "user-task",
                "text": "南门外闻到来源不明的焦糊味。",
                "quantity_tokens": [],
                "location_tokens": ["南门外"],
            }
        ],
    }


class V5ClaudeRedTeamPolicyTests(unittest.TestCase):
    def test_internal_request_is_latest_fixed_claude_and_bounded(self) -> None:
        request = build_claude_red_team_request(
            RedTeamScope.INTERNAL_SELECTION,
            internal_payload(),
        )
        self.assertEqual(CLAUDE_RED_TEAM_MODEL, request["model"])
        self.assertEqual(CLAUDE_RED_TEAM_MAX_OUTPUT_TOKENS, request["max_tokens"])
        self.assertEqual(
            {"effort": "low", "exclude": True},
            request["reasoning"],
        )
        self.assertEqual("low", request["verbosity"])
        self.assertEqual(0, request["temperature"])
        self.assertEqual(["anthropic"], request["provider"]["only"])
        self.assertFalse(request["provider"]["allow_fallbacks"])
        self.assertNotIn("tools", request)
        self.assertNotIn("tool_choice", request)
        self.assertLessEqual(
            len(request["messages"][1]["content"]),
            CLAUDE_RED_TEAM_MAX_INPUT_CHARS,
        )

    def test_internal_prompt_forbids_selection_and_report_work(self) -> None:
        request = build_claude_red_team_request(
            RedTeamScope.INTERNAL_SELECTION,
            internal_payload(),
        )
        system = request["messages"][0]["content"]
        for phrase in ("不得选择", "不得修改执行图", "不得输出解释", "不得输出", "报告"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, system)

    def test_external_scope_only_reviews_information(self) -> None:
        request = build_claude_red_team_request(
            RedTeamScope.EXTERNAL_INFORMATION,
            external_payload(),
        )
        system = request["messages"][0]["content"]
        self.assertIn("只检查输入信息", system)
        self.assertIn("不得补充事实", system)
        schema = request["response_format"]["json_schema"]["schema"]
        allowed = schema["properties"]["codes"]["items"]["enum"]
        self.assertIn("UNSUPPORTED_FACT", allowed)
        self.assertNotIn("DUPLICATE_COMPANY", allowed)

    def test_approve_is_minimal_json_only(self) -> None:
        verdict = parse_claude_red_team_verdict(
            RedTeamScope.INTERNAL_SELECTION,
            '{"decision":"APPROVE","codes":[],"targets":[]}',
        )
        self.assertEqual("APPROVE", verdict["decision"])
        self.assertEqual([], verdict["codes"])
        self.assertEqual("bounded-red-team-verdict-only", verdict["reviewer_role"])

    def test_reject_requires_enumerated_code(self) -> None:
        verdict = parse_claude_red_team_verdict(
            RedTeamScope.INTERNAL_SELECTION,
            json.dumps(
                {
                    "decision": "REJECT",
                    "codes": ["DUPLICATE_COMPANY"],
                    "targets": ["node-2"],
                }
            ),
        )
        self.assertEqual(["DUPLICATE_COMPANY"], verdict["codes"])
        with self.assertRaises(ValueError):
            parse_claude_red_team_verdict(
                RedTeamScope.INTERNAL_SELECTION,
                '{"decision":"REJECT","codes":[],"targets":[]}',
            )

    def test_free_text_or_extra_fields_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_claude_red_team_verdict(
                RedTeamScope.INTERNAL_SELECTION,
                '{"decision":"APPROVE","codes":[],"targets":[],"reason":"ok"}',
            )
        with self.assertRaises(ValueError):
            parse_claude_red_team_verdict(
                RedTeamScope.INTERNAL_SELECTION,
                "The expert team is approved.",
            )

    def test_output_character_limit_is_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            parse_claude_red_team_verdict(
                RedTeamScope.EXTERNAL_INFORMATION,
                "x" * (CLAUDE_RED_TEAM_MAX_OUTPUT_CHARS + 1),
            )

    def test_input_extra_fields_and_oversized_claim_are_rejected(self) -> None:
        payload = internal_payload()
        payload["instruction"] = "select a replacement"
        with self.assertRaises(ValueError):
            canonical_review_input(RedTeamScope.INTERNAL_SELECTION, payload)

        external = external_payload()
        external["claims"][0]["text"] = "字" * 241
        with self.assertRaises(ValueError):
            canonical_review_input(RedTeamScope.EXTERNAL_INFORMATION, external)

    def test_forbidden_capabilities_are_machine_readable(self) -> None:
        forbidden = set(forbidden_claude_capabilities())
        self.assertTrue(
            {
                "select_experts",
                "replace_experts",
                "modify_execution_graph",
                "execute_task",
                "call_tools",
                "browse_network",
                "write_report",
                "emit_free_text_reasoning",
            }.issubset(forbidden)
        )


if __name__ == "__main__":
    unittest.main()
