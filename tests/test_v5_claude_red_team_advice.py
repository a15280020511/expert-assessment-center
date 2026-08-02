from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_claude_red_team_policy import (  # noqa: E402
    CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
    CLAUDE_RED_TEAM_MODEL,
    CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK,
    GPT_SYNTHESIS_CALLS,
    RedTeamScope,
    build_claude_red_team_request,
    forbidden_claude_capabilities,
    parse_claude_red_team_advice,
)


def internal_payload() -> dict[str, object]:
    return {
        "task_digest": "a" * 64,
        "proposal_digest": "b" * 64,
        "approved_total_calls": 8,
        "governance_calls_reserved": 3,
        "approved_recovery_calls": 1,
        "cost_anomaly_usd": 0.5,
        "work_items": [
            {
                "work_id": "work-1",
                "objective": "Analyze the bounded decision problem",
                "dependencies": [],
                "required_outputs": ["analysis"],
            },
            {
                "work_id": "work-2",
                "objective": "Synthesize the final decision",
                "dependencies": ["work-1"],
                "required_outputs": ["recommendation"],
            },
        ],
        "nodes": [
            {
                "node_id": "node-1",
                "candidate_id": "model-a@provider-a",
                "work_ids": ["work-1", "work-2"],
                "role": "Decision analyst and synthesizer",
                "functions": ["analysis", "synthesis"],
                "model": "company-a/model-a",
                "company": "company-a",
                "provider": "provider-a",
                "estimated_cost_usd": 0.01,
                "contract_kind": "gpt-authored-expert-node",
                "recovery_candidate_ids": [],
            }
        ],
        "edges": [],
    }


class ClaudeRedTeamAdviceTests(unittest.TestCase):
    def test_request_uses_latest_and_advice_schema(self) -> None:
        request = build_claude_red_team_request(
            RedTeamScope.INTERNAL_SELECTION,
            internal_payload(),
        )
        self.assertEqual(
            "~anthropic/claude-opus-latest",
            CLAUDE_RED_TEAM_MODEL,
        )
        self.assertEqual(CLAUDE_RED_TEAM_MODEL, request["model"])
        schema = request["response_format"]["json_schema"]["schema"]
        self.assertEqual(["suggestions"], schema["required"])
        self.assertNotIn("decision", schema["properties"])
        self.assertNotIn("APPROVE", json.dumps(schema))
        self.assertNotIn("REJECT", json.dumps(schema))
        self.assertFalse(request["provider"]["allow_fallbacks"])

    def test_empty_advice_is_valid_and_not_approval(self) -> None:
        result = parse_claude_red_team_advice(
            RedTeamScope.INTERNAL_SELECTION,
            '{"suggestions":[]}',
        )
        self.assertEqual([], result["suggestions"])
        self.assertFalse(result["hard_gate"])
        self.assertFalse(result["approval_authority"])
        self.assertEqual("advisory-red-team-only", result["reviewer_role"])

    def test_concrete_modification_advice_is_preserved(self) -> None:
        result = parse_claude_red_team_advice(
            RedTeamScope.INTERNAL_SELECTION,
            json.dumps(
                {
                    "suggestions": [
                        {
                            "code": "WORK_UNCOVERED",
                            "target": "work-2",
                            "change": "为work-2增加明确负责节点并补齐依赖边。",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )
        self.assertEqual("WORK_UNCOVERED", result["suggestions"][0]["code"])
        self.assertIn("补齐依赖边", result["suggestions"][0]["change"])

    def test_approval_or_rejection_fields_are_rejected(self) -> None:
        for payload in (
            '{"decision":"APPROVE","suggestions":[]}',
            '{"decision":"REJECT","suggestions":[]}',
        ):
            with self.assertRaises(ValueError):
                parse_claude_red_team_advice(
                    RedTeamScope.INTERNAL_SELECTION,
                    payload,
                )

    def test_call_budget_is_fixed_three(self) -> None:
        self.assertEqual(1, CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK)
        self.assertEqual(1, GPT_SYNTHESIS_CALLS)
        self.assertEqual(3, CLAUDE_RED_TEAM_GOVERNANCE_CALLS)

    def test_forbidden_capabilities_include_gatekeeping(self) -> None:
        forbidden = set(forbidden_claude_capabilities())
        self.assertIn("approve_proposal", forbidden)
        self.assertIn("reject_proposal", forbidden)
        self.assertIn("block_execution", forbidden)


if __name__ == "__main__":
    unittest.main()
