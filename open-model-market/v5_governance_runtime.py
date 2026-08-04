"""One-pass GPT/Claude advisory governance with complete paid-call ledger."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from openrouter_api import CHAT_URL, request_json
from v5_claude_red_team_policy import (
    CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
    parse_claude_red_team_advice,
)
from v5_execution_primitives import actual_cost, extract_answer
from v5_production_claude_request import build_claude_red_team_request
from v5_governance_catalog import synthetic_governance_models
from v5_production_governance_policy import (
    build_proposal_request,
    build_synthesis_request,
    claude_unified_review_payload,
    deterministic_violations,
    materialize_proposal,
    parse_proposal,
)
from v5_soft_resource_governance import SOFT_RESOURCE_INSTRUCTION
from v5_structured_output_compat import normalize_strict_response_format
from v5_no_tools_policy import (
    assert_request_has_no_tools,
    assert_response_has_no_tools,
)


class GovernanceRuntimeError(RuntimeError):
    """Fail-closed governance protocol failure."""


def _provider(
    request: Mapping[str, Any],
    response: Mapping[str, Any],
) -> str:
    value = str(response.get("provider") or "").strip()
    if value:
        return value
    provider = request.get("provider")
    if isinstance(provider, Mapping):
        values = provider.get("only") or provider.get("order")
        if isinstance(values, list) and values:
            return str(values[0])
    return ""


def _expected_provider(request: Mapping[str, Any]) -> str:
    provider = request.get("provider")
    if not isinstance(provider, Mapping):
        raise GovernanceRuntimeError("governance provider lock is missing")
    only = provider.get("only")
    if not isinstance(only, list) or len(only) != 1:
        raise GovernanceRuntimeError(
            "governance provider.only must contain exactly one provider"
        )
    if provider.get("allow_fallbacks") is not False:
        raise GovernanceRuntimeError("governance provider fallback is forbidden")
    return str(only[0])


def _assert_provider_lock(
    request: Mapping[str, Any],
    response: Mapping[str, Any],
) -> None:
    expected = _expected_provider(request).casefold()
    actual = str(response.get("provider") or "").strip().casefold()
    if actual and actual != expected:
        raise GovernanceRuntimeError(
            f"governance provider mismatch: expected={expected}, actual={actual}"
        )


def bounded_governance_request(
    request: Mapping[str, Any],
    maximum_completion_tokens: int | None,
) -> dict[str, Any]:
    """Apply prompt-led soft resource governance without local token ceilings.

    ``maximum_completion_tokens`` remains in the compatibility signature so old
    tickets and workflows continue to parse, but it is advisory only and is not
    emitted to the provider request.
    """
    del maximum_completion_tokens
    softened = dict(request)
    softened.pop("max_tokens", None)
    softened.pop("max_completion_tokens", None)

    reasoning = softened.get("reasoning")
    if isinstance(reasoning, Mapping):
        soft_reasoning = dict(reasoning)
        for key in (
            "max_tokens",
            "max_completion_tokens",
            "budget_tokens",
            "token_budget",
        ):
            soft_reasoning.pop(key, None)
        if soft_reasoning:
            softened["reasoning"] = soft_reasoning
        else:
            softened.pop("reasoning", None)

    messages = softened.get("messages")
    if (
        isinstance(messages, list)
        and messages
        and isinstance(messages[0], Mapping)
    ):
        updated = list(messages)
        first = dict(updated[0])
        first["content"] = (
            str(first.get("content") or "") + SOFT_RESOURCE_INSTRUCTION
        )
        updated[0] = first
        softened["messages"] = updated
    return softened


def _bind_governance_request(
    request: Mapping[str, Any],
    endpoint: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a fixed logical protocol to one exact compatible endpoint."""
    logical_model = str(endpoint.get("logical_model") or "").strip()
    resolved_model = str(endpoint.get("resolved_model") or "").strip()
    provider_slug = str(endpoint.get("provider") or "").strip()
    supported = {
        str(value).casefold()
        for value in endpoint.get("supported_parameters", [])
    }
    if not logical_model or not resolved_model or not provider_slug:
        raise GovernanceRuntimeError(
            "governance endpoint binding is incomplete"
        )
    if endpoint.get("provider_fallback_allowed") is not False:
        raise GovernanceRuntimeError(
            "governance endpoint fallback must remain disabled"
        )

    bound = dict(request)
    bound["logical_model"] = logical_model
    bound["model"] = resolved_model
    bound["provider"] = {
        "only": [provider_slug],
        "order": [provider_slug],
        "allow_fallbacks": False,
        "require_parameters": True,
    }
    bound["governance_endpoint"] = {
        "company": str(endpoint.get("company") or ""),
        "provider": provider_slug,
        "official_intelligence_rank": endpoint.get(
            "official_intelligence_rank"
        ),
        "supported_parameters": sorted(supported),
        "synthetic_fixture_only": bool(
            endpoint.get("synthetic_fixture_only")
        ),
    }

    if "temperature" in bound and "temperature" not in supported:
        bound.pop("temperature")
    if "reasoning" in bound and not {
        "reasoning",
        "reasoning_effort",
    }.intersection(supported):
        raise GovernanceRuntimeError(
            "governance endpoint does not support reasoning control"
        )
    if "response_format" in bound and not {
        "response_format",
        "structured_outputs",
    }.intersection(supported):
        raise GovernanceRuntimeError(
            "governance endpoint does not support structured output"
        )
    return bound


def _api_payload(request: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        str(key): value
        for key, value in request.items()
        if key
        not in {
            "governance_policy",
            "red_team_policy",
            "logical_model",
            "governance_endpoint",
        }
    }
    response_format = payload.get("response_format")
    if isinstance(response_format, Mapping):
        normalized, _ = normalize_strict_response_format(response_format)
        payload["response_format"] = normalized
    return payload


def _request_receipt(request: Mapping[str, Any]) -> dict[str, Any]:
    """Persist request structure and hashes, never raw task/catalog/prompt text."""
    messages = request.get("messages")
    message_rows: list[dict[str, Any]] = []
    if isinstance(messages, list):
        for row in messages:
            if not isinstance(row, Mapping):
                continue
            content = str(row.get("content") or "")
            message_rows.append(
                {
                    "role": str(row.get("role") or ""),
                    "characters": len(content),
                    "sha256": hashlib.sha256(
                        content.encode("utf-8")
                    ).hexdigest(),
                }
            )
    response_format = request.get("response_format")
    schema_name = ""
    schema_compatibility: dict[str, Any] = {}
    if isinstance(response_format, Mapping):
        normalized, schema_compatibility = normalize_strict_response_format(
            response_format
        )
        schema = normalized.get("json_schema")
        if isinstance(schema, Mapping):
            schema_name = str(schema.get("name") or "")
    provider = request.get("provider")
    provider = dict(provider) if isinstance(provider, Mapping) else {}
    endpoint = request.get("governance_endpoint")
    endpoint = dict(endpoint) if isinstance(endpoint, Mapping) else {}
    policy = request.get("governance_policy")
    policy = dict(policy) if isinstance(policy, Mapping) else {}
    return {
        "logical_model": str(request.get("logical_model") or ""),
        "model": str(request.get("model") or ""),
        "temperature_present": "temperature" in request,
        "temperature": request.get("temperature"),
        "max_tokens": request.get("max_tokens"),
        "max_completion_tokens": request.get("max_completion_tokens"),
        "local_token_ceiling_enforced": False,
        "resource_governance_mode": "prompt-led-soft-governance",
        "reasoning": dict(request.get("reasoning") or {})
        if isinstance(request.get("reasoning"), Mapping)
        else {},
        "provider": provider,
        "governance_endpoint": endpoint,
        "governance_policy": policy,
        "response_schema": schema_name,
        "schema_compatibility": schema_compatibility,
        "messages": message_rows,
        "raw_message_content_persisted": False,
        "request_fields": sorted(str(key) for key in _api_payload(request)),
    }


@dataclass(frozen=True)
class GovernanceCall:
    sequence: int
    kind: str
    requested_model: str
    selected_model: str
    resolved_model: str
    provider: str
    request: Mapping[str, Any]
    usage: Mapping[str, Any]
    actual_cost_usd: float
    latency_seconds: float
    response_id: str | None
    finish_reason: str | None
    visible_output_characters: int
    visible_output_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind,
            "requested_model": self.requested_model,
            "selected_model": self.selected_model,
            "resolved_model": self.resolved_model,
            "provider": self.provider,
            "request": dict(self.request),
            "usage": dict(self.usage),
            "actual_cost_usd": self.actual_cost_usd,
            "latency_seconds": self.latency_seconds,
            "response_id": self.response_id,
            "finish_reason": self.finish_reason,
            "visible_output_characters": self.visible_output_characters,
            "visible_output_sha256": self.visible_output_sha256,
        }


class GovernanceLedger:
    def __init__(self, maximum_calls: int = 3) -> None:
        self.maximum_calls = int(maximum_calls)
        self.calls: list[GovernanceCall] = []
        self.failure: dict[str, Any] | None = None

    def record(
        self,
        *,
        kind: str,
        request: Mapping[str, Any],
        response: Mapping[str, Any],
        latency_seconds: float,
        visible_output: str,
    ) -> None:
        if len(self.calls) >= self.maximum_calls:
            raise GovernanceRuntimeError("governance call ceiling exceeded")
        _assert_provider_lock(request, response)
        usage = response.get("usage")
        usage = dict(usage) if isinstance(usage, Mapping) else {}
        selected_model = str(request.get("model") or "")
        choices = response.get("choices")
        finish_reason = None
        if (
            isinstance(choices, list)
            and choices
            and isinstance(choices[0], Mapping)
        ):
            finish_reason = (
                str(choices[0].get("finish_reason") or "") or None
            )
        output_sha256 = hashlib.sha256(
            visible_output.encode("utf-8")
        ).hexdigest()
        self.calls.append(
            GovernanceCall(
                sequence=len(self.calls) + 1,
                kind=kind,
                requested_model=str(
                    request.get("logical_model")
                    or selected_model
                ),
                selected_model=selected_model,
                resolved_model=str(
                    response.get("model")
                    or selected_model
                    or ""
                ),
                provider=_provider(request, response),
                request=_request_receipt(request),
                usage=usage,
                actual_cost_usd=round(actual_cost(response), 8),
                latency_seconds=round(max(0.0, latency_seconds), 6),
                response_id=str(response.get("id") or "") or None,
                finish_reason=finish_reason,
                visible_output_characters=len(visible_output),
                visible_output_sha256=output_sha256,
            )
        )

    def mark_failure(
        self,
        *,
        kind: str,
        error: BaseException,
        visible_output: str,
    ) -> None:
        self.failure = {
            "kind": kind,
            "error_type": type(error).__name__,
            "message": str(error),
            "visible_output_characters": len(visible_output),
            "visible_output_sha256": hashlib.sha256(
                visible_output.encode("utf-8")
            ).hexdigest(),
            "raw_visible_output_persisted": False,
        }

    def to_dict(self, *, status: str = "PASS") -> dict[str, Any]:
        return {
            "schema_version": "v5-advisory-governance-call-ledger-6",
            "status": status,
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
            "claude_is_advisory_only": True,
            "claude_gatekeeping_allowed": False,
            "claude_covers_internal_selection": True,
            "claude_covers_external_information": True,
            "second_claude_review_allowed": False,
            "model_loop_allowed": False,
            "resource_governance_mode": "prompt-led-soft-governance",
            "local_token_ceiling_enforced": False,
            "cost_threshold_can_stop_governance": False,
            "actual_cost_usd": round(
                sum(row.actual_cost_usd for row in self.calls), 8
            ),
            "calls": [row.to_dict() for row in self.calls],
            "failure": dict(self.failure) if self.failure else None,
        }


def _persist_governance_ledger(
    root: Path | None,
    ledger: GovernanceLedger,
    *,
    status: str,
) -> None:
    if root is None:
        return
    root.mkdir(parents=True, exist_ok=True)
    path = root / "v5-governance-calls.json"
    temporary = root / ".v5-governance-calls.json.tmp"
    temporary.write_text(
        json.dumps(
            ledger.to_dict(status=status),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


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
    artifact_root: Path | None,
) -> Mapping[str, Any]:
    api_request = _api_payload(request)
    try:
        assert_request_has_no_tools(
            api_request, context=f"governance {kind} request"
        )
        response, latency = call_fn(run, api_request)
        assert_response_has_no_tools(
            response, context=f"governance {kind} response"
        )
    except Exception as exc:
        ledger.mark_failure(kind=kind, error=exc, visible_output="")
        _persist_governance_ledger(artifact_root, ledger, status="FAIL")
        raise
    text = extract_answer(response)
    ledger.record(
        kind=kind,
        request=request,
        response=response,
        latency_seconds=latency,
        visible_output=text,
    )
    _persist_governance_ledger(
        artifact_root,
        ledger,
        status="IN_PROGRESS",
    )
    if not text:
        error = GovernanceRuntimeError(
            f"{kind} returned no visible output"
        )
        ledger.mark_failure(
            kind=kind,
            error=error,
            visible_output=text,
        )
        _persist_governance_ledger(
            artifact_root,
            ledger,
            status="FAIL",
        )
        raise error
    try:
        return parser(text)
    except Exception as exc:
        ledger.mark_failure(
            kind=kind,
            error=exc,
            visible_output=text,
        )
        _persist_governance_ledger(
            artifact_root,
            ledger,
            status="FAIL",
        )
        raise


def _validated_governance_models(
    governance_models: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    resolved = governance_models or synthetic_governance_models()
    if not isinstance(resolved, Mapping) or resolved.get("status") != "PASS":
        raise GovernanceRuntimeError(
            "governance model resolution must have PASS status"
        )
    for role in ("gpt", "claude"):
        if not isinstance(resolved.get(role), Mapping):
            raise GovernanceRuntimeError(
                f"governance model resolution is missing {role}"
            )
    if resolved.get("provider_fallback_allowed") is not False:
        raise GovernanceRuntimeError(
            "governance model resolution cannot allow fallback"
        )
    return resolved


def run_single_pass_governance(
    *,
    run: Any,
    task: str,
    task_digest: str,
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
    artifact_root: Path | None = None,
    max_completion_tokens: int | None = None,
    governance_models: Mapping[str, Any] | None = None,
    call_fn: Callable[
        [Any, Mapping[str, Any]],
        tuple[Mapping[str, Any], float],
    ] | None = None,
) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    """Run GPT proposal, Claude advice, GPT synthesis, final validator."""
    if int(governance_calls_reserved) != CLAUDE_RED_TEAM_GOVERNANCE_CALLS:
        raise GovernanceRuntimeError("governance call reserve must equal three")
    call = call_fn or _default_call
    resolved = _validated_governance_models(governance_models)
    gpt_endpoint = resolved["gpt"]
    claude_endpoint = resolved["claude"]
    limits = {
        "approved_total_calls": approved_total_calls,
        "governance_calls_reserved": governance_calls_reserved,
        "approved_recovery_calls": approved_recovery_calls,
        "cost_anomaly_usd": None,
    }
    resource_advisory = {
        "mode": "prompt-led-soft-governance",
        "requested_cost_advisory_usd": cost_anomaly_usd,
        "requested_token_advisory": max_completion_tokens,
        "cost_threshold_can_reject_plan": False,
        "cost_threshold_can_stop_execution": False,
        "local_token_ceiling_enforced": False,
    }
    ledger = GovernanceLedger(maximum_calls=governance_calls_reserved)

    proposal_request = _bind_governance_request(
        bounded_governance_request(
            build_proposal_request(
                task=task,
                task_envelope=task_envelope,
                catalog=catalog,
                **limits,
            ),
            max_completion_tokens,
        ),
        gpt_endpoint,
    )
    initial = _call_and_parse(
        run=run,
        request=proposal_request,
        kind="gpt_proposal",
        ledger=ledger,
        call_fn=call,
        parser=parse_proposal,
        artifact_root=artifact_root,
    )

    claude_input = claude_unified_review_payload(
        initial,
        task,
        task_envelope,
        catalog,
        task_digest=task_digest,
        **limits,
    )
    claude_request = _bind_governance_request(
        bounded_governance_request(
            build_claude_red_team_request(claude_input),
            max_completion_tokens,
        ),
        claude_endpoint,
    )
    claude_advice = _call_and_parse(
        run=run,
        request=claude_request,
        kind="claude_red_team",
        ledger=ledger,
        call_fn=call,
        parser=parse_claude_red_team_advice,
        artifact_root=artifact_root,
    )

    synthesis_request = _bind_governance_request(
        bounded_governance_request(
            build_synthesis_request(
                task=task,
                initial_proposal=initial,
                claude_advice=claude_advice,
                task_envelope=task_envelope,
                catalog=catalog,
                **limits,
            ),
            max_completion_tokens,
        ),
        gpt_endpoint,
    )
    final = dict(
        _call_and_parse(
            run=run,
            request=synthesis_request,
            kind="gpt_synthesis",
            ledger=ledger,
            call_fn=call,
            parser=parse_proposal,
            artifact_root=artifact_root,
        )
    )

    counts = {
        kind: sum(row.kind == kind for row in ledger.calls)
        for kind in (
            "gpt_proposal",
            "claude_red_team",
            "gpt_synthesis",
        )
    }
    if counts != {
        "gpt_proposal": 1,
        "claude_red_team": 1,
        "gpt_synthesis": 1,
    }:
        raise GovernanceRuntimeError(
            "governance must execute exactly GPT-Claude-GPT once"
        )

    violations = deterministic_violations(
        final,
        task,
        task_envelope,
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
        task,
        task_envelope,
        catalog,
        **limits,
    )
    governance = {
        "schema_version": "v5-advisory-governance-result-5",
        "status": "PASS",
        "initial_proposal": initial,
        "claude_advice": dict(claude_advice),
        "final_proposal": final,
        "governance_model_resolution": dict(resolved),
        "claude_review_count": 1,
        "gpt_synthesis_count": 1,
        "claude_is_advisory_only": True,
        "claude_gatekeeping_allowed": False,
        "claude_covers_internal_selection": True,
        "claude_covers_external_information": True,
        "second_claude_review_allowed": False,
        "model_loop_allowed": False,
        "resource_governance": resource_advisory,
        "final_authority": "deterministic-constitutional-validator",
        "materialization": materialization,
    }
    ledger_payload = ledger.to_dict()
    ledger_payload["resource_governance"] = resource_advisory
    return graph, graph_limits, governance, ledger_payload


def write_governance_artifacts(
    root: Path,
    governance: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "v5-governance-result.json").write_text(
        json.dumps(
            governance,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    (root / "v5-governance-calls.json").write_text(
        json.dumps(
            ledger,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
