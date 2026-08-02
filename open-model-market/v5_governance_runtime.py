"""One-pass GPT/Claude governance execution with a complete paid-call ledger."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from openrouter_api import CHAT_URL, request_json
from v5_claude_red_team_policy import (
    RedTeamScope,
    build_claude_red_team_request,
    parse_claude_red_team_verdict,
)
from v5_execution_primitives import extract_answer
from v5_gpt_expert_selector import (
    build_proposal_request,
    build_synthesis_request,
    parse_proposal,
)
from v5_proposal_materializer import (
    claude_internal_review_payload,
    compact_resources_for_gpt,
    deterministic_violations,
    materialize_proposal,
)


class GovernanceRuntimeError(RuntimeError):
    """Fail-closed governance protocol failure."""


def _actual_cost(response: Mapping[str, Any]) -> float:
    usage = response.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    for key in ("cost", "total_cost"):
        try:
            if usage.get(key) is not None:
                return max(0.0, float(usage[key]))
        except (TypeError, ValueError):
            continue
    return 0.0


def _provider(request: Mapping[str, Any], response: Mapping[str, Any]) -> str:
    value = str(response.get("provider") or "").strip()
    if value:
        return value
    provider = request.get("provider")
    if isinstance(provider, Mapping):
        values = provider.get("only") or provider.get("order")
        if isinstance(values, list) and values:
            return str(values[0])
    return ""


def _api_payload(request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in request.items()
        if key not in {"governance_policy", "red_team_policy"}
    }


@dataclass(frozen=True)
class GovernanceCall:
    sequence: int
    kind: str
    requested_model: str
    resolved_model: str
    provider: str
    request: Mapping[str, Any]
    usage: Mapping[str, Any]
    actual_cost_usd: float
    latency_seconds: float
    response_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind,
            "requested_model": self.requested_model,
            "resolved_model": self.resolved_model,
            "provider": self.provider,
            "request": dict(self.request),
            "usage": dict(self.usage),
            "actual_cost_usd": self.actual_cost_usd,
            "latency_seconds": self.latency_seconds,
            "response_id": self.response_id,
        }


class GovernanceLedger:
    def __init__(self, maximum_calls: int = 3) -> None:
        self.maximum_calls = int(maximum_calls)
        self.calls: list[GovernanceCall] = []

    def record(
        self,
        *,
        kind: str,
        request: Mapping[str, Any],
        response: Mapping[str, Any],
        latency_seconds: float,
    ) -> None:
        if len(self.calls) >= self.maximum_calls:
            raise GovernanceRuntimeError("governance call ceiling exceeded")
        usage = response.get("usage")
        usage = dict(usage) if isinstance(usage, Mapping) else {}
        self.calls.append(
            GovernanceCall(
                sequence=len(self.calls) + 1,
                kind=kind,
                requested_model=str(request.get("model") or ""),
                resolved_model=str(
                    response.get("model") or request.get("model") or ""
                ),
                provider=_provider(request, response),
                request=dict(request),
                usage=usage,
                actual_cost_usd=round(_actual_cost(response), 8),
                latency_seconds=round(max(0.0, latency_seconds), 6),
                response_id=str(response.get("id") or "") or None,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "v5-governance-call-ledger-1",
            "status": "PASS",
            "maximum_governance_calls": self.maximum_calls,
            "actual_governance_calls": len(self.calls),
            "gpt_proposal_calls": sum(
                row.kind == "gpt_proposal" for row in self.calls
            ),
            "claude_red_team_calls": sum(
                row.kind == "claude_red_team" for row in self.calls
            ),
            "gpt_synthesis_calls": sum(
                row.kind == "gpt_synthesis" for row in self.calls
            ),
            "second_claude_review_allowed": False,
            "model_loop_allowed": False,
            "actual_cost_usd": round(
                sum(row.actual_cost_usd for row in self.calls), 8
            ),
            "calls": [row.to_dict() for row in self.calls],
        }


def _default_call(
    run: Any,
    request: Mapping[str, Any],
) -> tuple[Mapping[str, Any], float]:
    api_key = getattr(run, "api_key", None)
    if not api_key:
        raise GovernanceRuntimeError("OPENROUTER_API_KEY is not set")
    started = time.monotonic()
    response = request_json(
        CHAT_URL,
        api_key,
        int(getattr(run, "model_timeout_seconds", 240)),
        0,
        _api_payload(request),
    )
    return response, time.monotonic() - started


def _call_and_parse(
    *,
    run: Any,
    request: Mapping[str, Any],
    kind: str,
    ledger: GovernanceLedger,
    call_fn: Callable[
        [Any, Mapping[str, Any]],
        tuple[Mapping[str, Any], float],
    ],
    parser: Callable[[str], Mapping[str, Any]],
) -> Mapping[str, Any]:
    api_request = _api_payload(request)
    response, latency = call_fn(run, api_request)
    ledger.record(
        kind=kind,
        request=api_request,
        response=response,
        latency_seconds=latency,
    )
    text = extract_answer(response)
    if not text:
        raise GovernanceRuntimeError(f"{kind} returned no visible output")
    return parser(text)


def run_single_pass_governance(
    *,
    run: Any,
    task: str,
    task_digest: str,
    resources: Mapping[str, Any],
    catalog: Mapping[str, Any],
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
    call_fn: Callable[
        [Any, Mapping[str, Any]],
        tuple[Mapping[str, Any], float],
    ] | None = None,
) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    """Run GPT proposal, Claude once, optional GPT synthesis, final validator."""
    call = call_fn or _default_call
    compact_resources = compact_resources_for_gpt(resources)
    limits = {
        "approved_total_calls": approved_total_calls,
        "governance_calls_reserved": governance_calls_reserved,
        "approved_recovery_calls": approved_recovery_calls,
        "cost_anomaly_usd": cost_anomaly_usd,
    }
    ledger = GovernanceLedger(maximum_calls=governance_calls_reserved)

    proposal_request = build_proposal_request(
        task=task,
        resources=compact_resources,
        catalog=catalog,
        **limits,
    )
    initial = _call_and_parse(
        run=run,
        request=proposal_request,
        kind="gpt_proposal",
        ledger=ledger,
        call_fn=call,
        parser=parse_proposal,
    )

    claude_input = claude_internal_review_payload(
        initial,
        resources,
        catalog,
        task_digest=task_digest,
        **limits,
    )
    claude_request = build_claude_red_team_request(
        RedTeamScope.INTERNAL_SELECTION,
        claude_input,
    )
    claude = _call_and_parse(
        run=run,
        request=claude_request,
        kind="claude_red_team",
        ledger=ledger,
        call_fn=call,
        parser=lambda text: parse_claude_red_team_verdict(
            RedTeamScope.INTERNAL_SELECTION,
            text,
        ),
    )

    final = dict(initial)
    if claude.get("decision") == "REJECT":
        synthesis_request = build_synthesis_request(
            task=task,
            initial_proposal=initial,
            claude_verdict=claude,
            resources=compact_resources,
            catalog=catalog,
            **limits,
        )
        final = dict(
            _call_and_parse(
                run=run,
                request=synthesis_request,
                kind="gpt_synthesis",
                ledger=ledger,
                call_fn=call,
                parser=parse_proposal,
            )
        )

    if sum(row.kind == "claude_red_team" for row in ledger.calls) != 1:
        raise GovernanceRuntimeError("Claude must execute exactly once")
    if sum(row.kind == "gpt_synthesis" for row in ledger.calls) > 1:
        raise GovernanceRuntimeError("GPT synthesis exceeded one call")

    violations = deterministic_violations(
        final,
        resources,
        catalog,
        **limits,
    )
    if violations:
        raise GovernanceRuntimeError(
            "final proposal failed deterministic validation: "
            + "; ".join(violations)
        )
    graph, graph_limits, materialization = materialize_proposal(
        final,
        resources,
        catalog,
        **limits,
    )
    governance = {
        "schema_version": "v5-single-pass-governance-result-1",
        "status": "PASS",
        "initial_proposal": initial,
        "claude_verdict": dict(claude),
        "final_proposal": final,
        "claude_review_count": 1,
        "gpt_synthesis_count": sum(
            row.kind == "gpt_synthesis" for row in ledger.calls
        ),
        "second_claude_review_allowed": False,
        "model_loop_allowed": False,
        "final_authority": "deterministic-constitutional-validator",
        "materialization": materialization,
    }
    return graph, graph_limits, governance, ledger.to_dict()


def write_governance_artifacts(
    root: Path,
    governance: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "v5-governance-result.json").write_text(
        json.dumps(governance, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (root / "v5-governance-calls.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
