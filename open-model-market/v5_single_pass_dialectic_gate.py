"""Single-pass GPT → Claude → GPT synthesis gate for expert-team selection.

The state machine is intentionally acyclic:
1. GPT proposes once.
2. Claude red-teams exactly once.
3. If rejected, GPT synthesizes exactly once.
4. Deterministic constitutional validation is final.

Claude never reviews the synthesis, and no model may loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Callable, Mapping, Sequence

from v5_claude_red_team_policy import (
    CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK,
    GPT_PROPOSAL_CALLS,
    GPT_SYNTHESIS_CALLS_MAX,
)


class DialecticGateError(RuntimeError):
    """Fail-closed error raised by the single-pass governance gate."""


@dataclass(frozen=True)
class DialecticGateResult:
    status: str
    final_proposal: Mapping[str, Any]
    initial_proposal_sha256: str
    final_proposal_sha256: str
    claude_decision: str
    claude_codes: tuple[str, ...]
    claude_targets: tuple[str, ...]
    gpt_proposal_calls: int
    claude_red_team_calls: int
    gpt_synthesis_calls: int
    deterministic_violations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "v5-single-pass-dialectic-gate-1",
            "status": self.status,
            "final_proposal": dict(self.final_proposal),
            "initial_proposal_sha256": self.initial_proposal_sha256,
            "final_proposal_sha256": self.final_proposal_sha256,
            "claude_decision": self.claude_decision,
            "claude_codes": list(self.claude_codes),
            "claude_targets": list(self.claude_targets),
            "gpt_proposal_calls": self.gpt_proposal_calls,
            "claude_red_team_calls": self.claude_red_team_calls,
            "gpt_synthesis_calls": self.gpt_synthesis_calls,
            "second_claude_review_allowed": False,
            "model_loop_allowed": False,
            "deterministic_violations": list(self.deterministic_violations),
            "final_authority": "deterministic-constitutional-validator",
        }


def _canonical_digest(value: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(rendered.encode("utf-8")).hexdigest()


def _validated_verdict(
    verdict: Mapping[str, Any],
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    if not isinstance(verdict, Mapping):
        raise DialecticGateError("Claude verdict must be a mapping")
    required = {"decision", "codes", "targets"}
    if not required.issubset(verdict):
        raise DialecticGateError("Claude verdict is missing required fields")
    decision = str(verdict.get("decision") or "")
    if decision not in {"APPROVE", "REJECT"}:
        raise DialecticGateError("Claude verdict decision is invalid")
    raw_codes = verdict.get("codes")
    raw_targets = verdict.get("targets")
    if not isinstance(raw_codes, list) or not isinstance(raw_targets, list):
        raise DialecticGateError("Claude verdict codes and targets must be lists")
    codes = tuple(str(value) for value in raw_codes)
    targets = tuple(str(value) for value in raw_targets)
    if decision == "APPROVE" and (codes or targets):
        raise DialecticGateError("APPROVE verdict cannot carry objections")
    if decision == "REJECT" and not codes:
        raise DialecticGateError("REJECT verdict must carry an objection code")
    return decision, codes, targets


def apply_single_pass_dialectic(
    initial_proposal: Mapping[str, Any],
    claude_verdict: Mapping[str, Any],
    *,
    synthesize_once: Callable[
        [Mapping[str, Any], Mapping[str, Any]],
        Mapping[str, Any],
    ],
    deterministic_validate: Callable[[Mapping[str, Any]], Sequence[str]],
) -> DialecticGateResult:
    """Apply one non-recursive red-team cycle.

    `synthesize_once` is called only when Claude returns REJECT. It must perform
    one GPT synthesis from the initial proposal plus Claude's bounded verdict.
    There is no API or callback for a second Claude review by design.
    """
    if not isinstance(initial_proposal, Mapping) or not initial_proposal:
        raise DialecticGateError("initial GPT proposal must be a non-empty mapping")
    decision, codes, targets = _validated_verdict(claude_verdict)

    synthesis_calls = 0
    final_proposal: Mapping[str, Any] = dict(initial_proposal)
    if decision == "REJECT":
        synthesized = synthesize_once(
            dict(initial_proposal),
            {
                "decision": decision,
                "codes": list(codes),
                "targets": list(targets),
            },
        )
        synthesis_calls = 1
        if not isinstance(synthesized, Mapping) or not synthesized:
            raise DialecticGateError("GPT synthesis returned no usable proposal")
        final_proposal = dict(synthesized)

    if synthesis_calls > GPT_SYNTHESIS_CALLS_MAX:
        raise DialecticGateError("GPT synthesis exceeded the single-pass limit")
    violations = tuple(
        str(value)
        for value in deterministic_validate(final_proposal)
        if str(value).strip()
    )
    status = "PASS" if not violations else "FAIL"
    result = DialecticGateResult(
        status=status,
        final_proposal=final_proposal,
        initial_proposal_sha256=_canonical_digest(initial_proposal),
        final_proposal_sha256=_canonical_digest(final_proposal),
        claude_decision=decision,
        claude_codes=codes,
        claude_targets=targets,
        gpt_proposal_calls=GPT_PROPOSAL_CALLS,
        claude_red_team_calls=CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK,
        gpt_synthesis_calls=synthesis_calls,
        deterministic_violations=violations,
    )
    if status != "PASS":
        raise DialecticGateError(
            "final synthesized proposal failed deterministic constitutional validation: "
            + "; ".join(violations)
        )
    return result


def governance_call_budget(verdict_decision: str) -> dict[str, int]:
    """Return exact and maximum governance calls for audit and reservation."""
    decision = str(verdict_decision or "").upper()
    if decision not in {"APPROVE", "REJECT"}:
        raise DialecticGateError("verdict decision must be APPROVE or REJECT")
    synthesis = 1 if decision == "REJECT" else 0
    return {
        "gpt_proposal_calls": GPT_PROPOSAL_CALLS,
        "claude_red_team_calls": CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK,
        "gpt_synthesis_calls": synthesis,
        "actual_governance_calls": (
            GPT_PROPOSAL_CALLS
            + CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK
            + synthesis
        ),
        "maximum_governance_calls": (
            GPT_PROPOSAL_CALLS
            + CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK
            + GPT_SYNTHESIS_CALLS_MAX
        ),
    }
