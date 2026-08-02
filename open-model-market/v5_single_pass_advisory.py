"""Single-pass GPT -> Claude advice -> GPT synthesis protocol.

Claude is advisory only. The deterministic constitutional validator is the
only hard gate after GPT has synthesized the advice exactly once.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Mapping, Sequence

from v5_claude_red_team_policy import (
    CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK,
    GPT_PROPOSAL_CALLS,
    GPT_SYNTHESIS_CALLS,
)


class AdvisoryProtocolError(RuntimeError):
    """Fail-closed error raised by the single-pass advisory protocol."""


@dataclass(frozen=True)
class AdvisoryResult:
    status: str
    final_proposal: Mapping[str, Any]
    initial_proposal_sha256: str
    final_proposal_sha256: str
    claude_suggestions: tuple[Mapping[str, str], ...]
    gpt_proposal_calls: int
    claude_red_team_calls: int
    gpt_synthesis_calls: int
    deterministic_violations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "v5-single-pass-advisory-1",
            "status": self.status,
            "final_proposal": dict(self.final_proposal),
            "initial_proposal_sha256": self.initial_proposal_sha256,
            "final_proposal_sha256": self.final_proposal_sha256,
            "claude_suggestions": [
                dict(value) for value in self.claude_suggestions
            ],
            "claude_is_advisory_only": True,
            "claude_gatekeeping_allowed": False,
            "gpt_proposal_calls": self.gpt_proposal_calls,
            "claude_red_team_calls": self.claude_red_team_calls,
            "gpt_synthesis_calls": self.gpt_synthesis_calls,
            "second_claude_review_allowed": False,
            "model_loop_allowed": False,
            "deterministic_violations": list(
                self.deterministic_violations
            ),
            "final_authority": (
                "deterministic-constitutional-validator"
            ),
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


def _validated_advice(
    advice: Mapping[str, Any],
) -> tuple[Mapping[str, str], ...]:
    if not isinstance(advice, Mapping) or set(advice) != {
        "suggestions"
    }:
        raise AdvisoryProtocolError(
            "Claude advice must contain only suggestions"
        )
    raw = advice.get("suggestions")
    if not isinstance(raw, list):
        raise AdvisoryProtocolError(
            "Claude suggestions must be a list"
        )
    normalized: list[Mapping[str, str]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, Mapping) or set(value) != {
            "code",
            "target",
            "change",
        }:
            raise AdvisoryProtocolError(
                f"Claude suggestion {index} is invalid"
            )
        normalized.append({
            "code": str(value["code"]),
            "target": str(value["target"]),
            "change": str(value["change"]),
        })
    return tuple(normalized)


def apply_single_pass_advisory(
    initial_proposal: Mapping[str, Any],
    claude_advice: Mapping[str, Any],
    *,
    synthesize_once: Callable[
        [Mapping[str, Any], Mapping[str, Any]],
        Mapping[str, Any],
    ],
    deterministic_validate: Callable[
        [Mapping[str, Any]],
        Sequence[str],
    ],
) -> AdvisoryResult:
    """Always synthesize Claude advice once, then validate deterministically."""
    if not isinstance(initial_proposal, Mapping) or not initial_proposal:
        raise AdvisoryProtocolError(
            "initial GPT proposal must be a non-empty mapping"
        )
    suggestions = _validated_advice(claude_advice)
    synthesized = synthesize_once(
        dict(initial_proposal),
        {"suggestions": [dict(value) for value in suggestions]},
    )
    if not isinstance(synthesized, Mapping) or not synthesized:
        raise AdvisoryProtocolError(
            "GPT synthesis returned no usable proposal"
        )
    final_proposal = dict(synthesized)
    violations = tuple(
        str(value)
        for value in deterministic_validate(final_proposal)
        if str(value).strip()
    )
    if violations:
        raise AdvisoryProtocolError(
            "final proposal failed deterministic constitutional validation: "
            + "; ".join(violations)
        )
    return AdvisoryResult(
        status="PASS",
        final_proposal=final_proposal,
        initial_proposal_sha256=_canonical_digest(initial_proposal),
        final_proposal_sha256=_canonical_digest(final_proposal),
        claude_suggestions=suggestions,
        gpt_proposal_calls=GPT_PROPOSAL_CALLS,
        claude_red_team_calls=CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK,
        gpt_synthesis_calls=GPT_SYNTHESIS_CALLS,
        deterministic_violations=(),
    )


def governance_call_budget() -> dict[str, int]:
    """Return the fixed three-call governance budget."""
    total = (
        GPT_PROPOSAL_CALLS
        + CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK
        + GPT_SYNTHESIS_CALLS
    )
    return {
        "gpt_proposal_calls": GPT_PROPOSAL_CALLS,
        "claude_red_team_calls": (
            CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK
        ),
        "gpt_synthesis_calls": GPT_SYNTHESIS_CALLS,
        "actual_governance_calls": total,
        "maximum_governance_calls": total,
    }
