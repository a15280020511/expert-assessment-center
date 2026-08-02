from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_claude_red_team_policy import (  # noqa: E402
    CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK,
    CLAUDE_RED_TEAM_MODEL,
    GPT_PROPOSAL_CALLS,
    GPT_SYNTHESIS_CALLS_MAX,
    RedTeamScope,
    build_claude_red_team_request,
    fixed_prompt,
    fixed_prompt_sha256,
)
from v5_single_pass_dialectic_gate import (  # noqa: E402
    DialecticGateError,
    apply_single_pass_dialectic,
    governance_call_budget,
)


def review_payload() -> dict:
    return {
        "task_digest": "a" * 64,
        "proposal_digest": "b" * 64,
        "approved_total_calls": 7,
        "governance_calls_reserved": 3,
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
                "recovery_candidate_ids": [],
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


class V5SinglePassDialecticGateTests(unittest.TestCase):
    def test_latest_alias_and_fixed_prompt_are_mandatory(self) -> None:
        self.assertEqual("anthropic/claude-opus-latest", CLAUDE_RED_TEAM_MODEL)
        request = build_claude_red_team_request(
            RedTeamScope.INTERNAL_SELECTION,
            review_payload(),
        )
        self.assertEqual(CLAUDE_RED_TEAM_MODEL, request["model"])
        self.assertEqual(
            fixed_prompt(RedTeamScope.INTERNAL_SELECTION),
            request["messages"][0]["content"],
        )
        self.assertEqual(
            fixed_prompt_sha256(RedTeamScope.INTERNAL_SELECTION),
            request["red_team_policy"]["prompt_sha256"],
        )
        self.assertEqual(1, request["red_team_policy"]["maximum_calls_per_task"])
        self.assertFalse(request["red_team_policy"]["second_review_allowed"])

    def test_approve_skips_synthesis_and_runs_claude_once(self) -> None:
        synth_calls = 0

        def synthesize(_proposal, _verdict):
            nonlocal synth_calls
            synth_calls += 1
            return {"experts": ["unexpected"]}

        result = apply_single_pass_dialectic(
            {"experts": ["mistral", "deepseek"]},
            {"decision": "APPROVE", "codes": [], "targets": []},
            synthesize_once=synthesize,
            deterministic_validate=lambda proposal: [],
        )
        self.assertEqual("PASS", result.status)
        self.assertEqual(0, synth_calls)
        self.assertEqual(1, result.claude_red_team_calls)
        self.assertEqual(0, result.gpt_synthesis_calls)

    def test_reject_triggers_exactly_one_gpt_synthesis_without_claude_recheck(self) -> None:
        synth_calls = 0

        def synthesize(proposal, verdict):
            nonlocal synth_calls
            synth_calls += 1
            self.assertEqual(["DUPLICATE_COMPANY"], verdict["codes"])
            return {
                "experts": ["mistral", "deepseek"],
                "revision": proposal["revision"] + 1,
            }

        result = apply_single_pass_dialectic(
            {"experts": ["mistral", "mistral"], "revision": 0},
            {
                "decision": "REJECT",
                "codes": ["DUPLICATE_COMPANY"],
                "targets": ["node-2"],
            },
            synthesize_once=synthesize,
            deterministic_validate=lambda proposal: (
                []
                if len(set(proposal["experts"])) == len(proposal["experts"])
                else ["duplicate-company"]
            ),
        )
        self.assertEqual(1, synth_calls)
        self.assertEqual(1, result.claude_red_team_calls)
        self.assertEqual(1, result.gpt_synthesis_calls)
        self.assertEqual(1, result.final_proposal["revision"])
        evidence = result.to_dict()
        self.assertFalse(evidence["second_claude_review_allowed"])
        self.assertFalse(evidence["model_loop_allowed"])

    def test_final_deterministic_validator_is_authoritative(self) -> None:
        with self.assertRaises(DialecticGateError):
            apply_single_pass_dialectic(
                {"experts": ["mistral", "mistral"]},
                {
                    "decision": "REJECT",
                    "codes": ["DUPLICATE_COMPANY"],
                    "targets": ["node-2"],
                },
                synthesize_once=lambda proposal, verdict: proposal,
                deterministic_validate=lambda proposal: ["duplicate-company"],
            )

    def test_governance_budget_is_two_or_three_never_more(self) -> None:
        approved = governance_call_budget("APPROVE")
        rejected = governance_call_budget("REJECT")
        self.assertEqual(GPT_PROPOSAL_CALLS, 1)
        self.assertEqual(CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK, 1)
        self.assertEqual(GPT_SYNTHESIS_CALLS_MAX, 1)
        self.assertEqual(2, approved["actual_governance_calls"])
        self.assertEqual(3, rejected["actual_governance_calls"])
        self.assertEqual(3, approved["maximum_governance_calls"])
        self.assertEqual(3, rejected["maximum_governance_calls"])


if __name__ == "__main__":
    unittest.main()
