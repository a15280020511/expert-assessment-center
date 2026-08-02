from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_single_pass_advisory import (  # noqa: E402
    AdvisoryProtocolError,
    apply_single_pass_advisory,
    governance_call_budget,
)


class SinglePassAdvisoryTests(unittest.TestCase):
    def test_empty_advice_still_triggers_one_synthesis(self) -> None:
        calls: list[tuple[dict, dict]] = []

        def synthesize(initial: dict, advice: dict) -> dict:
            calls.append((initial, advice))
            return {**initial, "final": True}

        result = apply_single_pass_advisory(
            {"proposal": 1},
            {"suggestions": []},
            synthesize_once=synthesize,
            deterministic_validate=lambda _: [],
        )
        self.assertEqual(1, len(calls))
        self.assertEqual([], calls[0][1]["suggestions"])
        self.assertEqual(1, result.gpt_synthesis_calls)
        self.assertTrue(result.final_proposal["final"])

    def test_modification_advice_is_passed_to_gpt(self) -> None:
        captured: list[dict] = []
        advice = {
            "suggestions": [
                {
                    "code": "DUPLICATE_COMPANY",
                    "target": "node-2",
                    "change": "改用不同公司的候选模型。",
                }
            ]
        }

        def synthesize(initial: dict, received: dict) -> dict:
            captured.append(received)
            return {"proposal": 2}

        apply_single_pass_advisory(
            {"proposal": 1},
            advice,
            synthesize_once=synthesize,
            deterministic_validate=lambda _: [],
        )
        self.assertEqual(advice, captured[0])

    def test_claude_has_no_gatekeeping_field(self) -> None:
        with self.assertRaises(AdvisoryProtocolError):
            apply_single_pass_advisory(
                {"proposal": 1},
                {"decision": "REJECT", "suggestions": []},
                synthesize_once=lambda initial, advice: initial,
                deterministic_validate=lambda _: [],
            )

    def test_deterministic_validator_is_only_gate(self) -> None:
        with self.assertRaises(AdvisoryProtocolError):
            apply_single_pass_advisory(
                {"proposal": 1},
                {"suggestions": []},
                synthesize_once=lambda initial, advice: initial,
                deterministic_validate=lambda _: ["duplicate-company"],
            )

    def test_governance_budget_is_always_three(self) -> None:
        self.assertEqual(
            {
                "gpt_proposal_calls": 1,
                "claude_red_team_calls": 1,
                "gpt_synthesis_calls": 1,
                "actual_governance_calls": 3,
                "maximum_governance_calls": 3,
            },
            governance_call_budget(),
        )


if __name__ == "__main__":
    unittest.main()
