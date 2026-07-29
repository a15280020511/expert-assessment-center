#!/usr/bin/env python3
"""Production entrypoint applying audited control-plane guards."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import direct_calls
import expert_team as base
import task_router
from artifact_manifest import write_manifest
from call_ledger import write_ledger
from hardened_runtime import apply_judge_output_contract, enforce_post_judge_actual_budget
from response_audit import diagnostics
import seat_scoring


CONCISE_EXPERT_RULE = (
    "在完整覆盖本席职责的前提下尽量简短、密集、直接；不要复述题目、不要展示思维过程、"
    "不要重复其他席位内容，优先使用紧凑表格和短段落，相同依据只说明一次，没有新增信息时立即停止。"
    "篇幅服从内容需要，不设置固定字数或Token上限。"
)
CONCISE_JUDGE_RULE = (
    "只输出最终裁判报告，不逐篇复述三名专家；合并相同结论，优先保留结论、数字、条件、风险和否决原因。"
    "表格能表达的内容不要再用长段落重复，没有新增信息时立即停止。"
    "完整性优先，但不设置固定字数或Token上限。"
)
FORBIDDEN_REQUEST_FIELDS = {
    "tools",
    "tool_choice",
    "plugins",
    "web_search_options",
    "file_search",
    "models",
}
_LAST_ROUTER_REQUEST: dict[str, Any] | None = None
_JUDGE_REQUESTS: list[dict[str, Any]] = []


def _hardened_candidate_judges(
    ranked: list[Any],
    profile: Any,
    excluded_ids: set[str],
    excluded_authors: set[str],
    run: Any,
) -> list[Any]:
    # Keep judge recovery inside the same stable top-50 capability pool.
    stable = seat_scoring._stable_pool(ranked, profile)
    eligible = [
        model
        for model in stable
        if model.id not in excluded_ids
        and model.context_length >= profile.requested_context
        and not base._history_rejects_judge(run, model.id)
        and (
            profile.primary_domain == "coding"
            or not any(term in seat_scoring._text(model) for term in seat_scoring.CODE_SPECIALIST_TERMS)
        )
    ]
    distinct = [model for model in eligible if model.author not in excluded_authors]
    return seat_scoring._ordered(
        distinct or eligible,
        "judge",
        profile.primary_domain,
        run.quality_tier,
    )


def _hardened_prefer_reliable_judge(
    run: Any,
    profile: Any,
    ranked: list[Any],
    experts: list[Any],
    judge: Any,
) -> Any:
    # Reject an unreliable judge unless a policy-compliant replacement exists.
    if not base._history_rejects_judge(run, judge.model_id):
        return judge
    by_id = {model.id: model for model in ranked}
    expert_ids = {expert.model_id for expert in experts}
    authors = {by_id[model_id].author for model_id in expert_ids if model_id in by_id}
    candidates = _hardened_candidate_judges(
        ranked,
        profile,
        expert_ids | {judge.model_id},
        authors,
        run,
    )
    if not candidates:
        raise base.ExpertTeamError(
            "No policy-compliant stable intelligence-top-50 judge replacement is available."
        )
    replacement = candidates[0]
    return base.SelectedJudge(
        judge.function,
        judge.profession,
        replacement.id,
        judge.selection_reason + f"；历史完整交付保护替换原候选={judge.model_id}",
    )


base._candidate_judges = _hardened_candidate_judges
base._prefer_reliable_judge = _hardened_prefer_reliable_judge


def _append_system_rule(payload: dict[str, Any], rule: str) -> dict[str, Any]:
    messages = payload.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        content = str(messages[0].get("content") or "")
        if rule not in content:
            messages[0]["content"] = content + rule
    return payload


def _remove_token_ceilings(payload: dict[str, Any]) -> dict[str, Any]:
    payload.pop("max_tokens", None)
    payload.pop("max_completion_tokens", None)
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict):
        reasoning.pop("max_tokens", None)
        reasoning["effort"] = "low"
        reasoning["exclude"] = True
    return payload


def _token_ceiling_paths(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key in {"max_tokens", "max_completion_tokens"}:
                found.append(path)
            found.extend(_token_ceiling_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_token_ceiling_paths(item, f"{prefix}[{index}]"))
    return found


def _copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=False))


_original_expert_payload = direct_calls.build_expert_payload


def _concise_expert_payload(*args: Any, **kwargs: Any) -> dict[str, Any]:
    payload = _original_expert_payload(*args, **kwargs)
    return _remove_token_ceilings(_append_system_rule(payload, CONCISE_EXPERT_RULE))


direct_calls.build_expert_payload = _concise_expert_payload

_original_judge_payload = base.build_judge_payload


def _concise_judge_payload(*args: Any, **kwargs: Any) -> dict[str, Any]:
    payload = _original_judge_payload(*args, **kwargs)
    return _remove_token_ceilings(_append_system_rule(payload, CONCISE_JUDGE_RULE))


base.build_judge_payload = _concise_judge_payload

_original_router_payload = task_router._build_payload


def _unbounded_router_payload(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Keep routing concise and retain the exact safe request for Artifact audit."""
    global _LAST_ROUTER_REQUEST
    payload = _remove_token_ceilings(_original_router_payload(*args, **kwargs))
    _LAST_ROUTER_REQUEST = _copy_payload(payload)
    return payload


task_router._build_payload = _unbounded_router_payload

_original_judge_call_model = base.call_model


def _audited_judge_call(run: Any, payload: dict[str, Any]) -> Any:
    """Capture every judge request before the paid call, including failed attempts."""
    _JUDGE_REQUESTS.append(_copy_payload(payload))
    return _original_judge_call_model(run, payload)


base.call_model = _audited_judge_call

_original_attempt_history = base._record_history
_original_final_history = direct_calls._record_history


def _record_failed_or_partial_attempt(
    run: Any,
    model_id: str,
    estimated_cost: float,
    response: dict[str, Any] | None,
    latency: float,
    error: str | None,
) -> None:
    """Attempt layer records only failed or partial calls; complete calls are finalized later."""
    if error:
        _original_attempt_history(run, model_id, estimated_cost, response, latency, error)


def _record_complete_final_once(
    run: Any,
    model_id: str,
    estimated_cost: float,
    response: dict[str, Any] | None,
    latency: float,
    error: str | None,
) -> None:
    """Final layer skips partial output already recorded by the attempt layer."""
    info = diagnostics(response or {})
    if info.get("finish_reason") == "partial_length":
        return
    _original_final_history(run, model_id, estimated_cost, response, latency, error)


base._record_history = _record_failed_or_partial_attempt
direct_calls._record_history = _record_complete_final_once
base.apply_judge_output_contract = apply_judge_output_contract
base.enforce_post_judge_actual_budget = enforce_post_judge_actual_budget


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _request_row(
    call_type: str,
    payload: dict[str, Any] | None,
    *,
    model: str | None = None,
    seat: str | None = None,
    attempt_index: int | None = None,
) -> dict[str, Any]:
    missing = payload is None
    token_paths = _token_ceiling_paths(payload or {})
    forbidden = sorted(FORBIDDEN_REQUEST_FIELDS.intersection(payload or {}))
    request_model = str((payload or {}).get("model") or model or "") or None
    online_model = bool(request_model and (request_model.startswith("openrouter/") or ":online" in request_model))
    return {
        "type": call_type,
        "seat": seat,
        "attempt_index": attempt_index,
        "model": request_model,
        "request_payload_captured": not missing,
        "token_ceiling_paths": token_paths,
        "forbidden_request_fields": forbidden,
        "online_or_router_model": online_model,
        "status": "FAIL" if missing or token_paths or forbidden or online_model else "PASS",
    }


def _annotate_judge_attempts(output: Path) -> list[dict[str, Any]]:
    path = output / "judge-attempts.json"
    rows = _load_json(path)
    if not isinstance(rows, list):
        return []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        payload = _JUDGE_REQUESTS[index] if index < len(_JUDGE_REQUESTS) else None
        row["payload"] = payload
        row["request_token_ceiling_paths"] = _token_ceiling_paths(payload or {})
        row["request_token_ceiling_sent"] = bool(row["request_token_ceiling_paths"])
    _write_json(path, rows)
    return [row for row in rows if isinstance(row, dict)]


def _write_request_audit(output: Path, routing: dict[str, Any], judge_rows: list[dict[str, Any]]) -> None:
    entries: list[dict[str, Any]] = []
    if routing.get("call_consumed"):
        payload = routing.get("request_payload") if isinstance(routing.get("request_payload"), dict) else None
        entries.append(
            _request_row(
                "semantic_router",
                payload,
                model=str(routing.get("model_id") or "") or None,
                attempt_index=1,
            )
        )

    experts = _load_json(output / "expert-responses.json")
    if isinstance(experts, list):
        for result in experts:
            if not isinstance(result, dict):
                continue
            attempts = result.get("attempts") if isinstance(result.get("attempts"), list) else []
            real_index = 0
            for attempt in attempts:
                if not isinstance(attempt, dict) or attempt.get("replacement") is True:
                    continue
                real_index += 1
                payload = attempt.get("payload") if isinstance(attempt.get("payload"), dict) else None
                entries.append(
                    _request_row(
                        "expert",
                        payload,
                        model=str(attempt.get("model") or "") or None,
                        seat=str(result.get("seat_key") or "") or None,
                        attempt_index=real_index,
                    )
                )

    for index, attempt in enumerate(judge_rows, 1):
        payload = attempt.get("payload") if isinstance(attempt.get("payload"), dict) else None
        entries.append(
            _request_row(
                "judge",
                payload,
                model=str(attempt.get("model") or "") or None,
                attempt_index=int(attempt.get("attempt_index") or index),
            )
        )

    failures = [entry for entry in entries if entry["status"] != "PASS"]
    audit = {
        "version": 1,
        "status": "FAIL" if failures else "PASS",
        "policy": "no-artificial-token-ceiling-no-tools-direct-model-only",
        "captured_request_count": sum(entry["request_payload_captured"] for entry in entries),
        "expected_request_count": len(entries),
        "entries": entries,
        "failures": failures,
    }
    _write_json(output / "request-audit.json", audit)


def _annotate_post_run_artifacts(output: Path) -> None:
    """Make request and estimate semantics explicit before report publication."""
    routing_path = output / "task-routing.json"
    routing = _load_json(routing_path)
    if not isinstance(routing, dict):
        routing = {}
    if routing:
        if _LAST_ROUTER_REQUEST is not None:
            routing["request_payload"] = _LAST_ROUTER_REQUEST
            routing["request_token_ceiling_paths"] = _token_ceiling_paths(_LAST_ROUTER_REQUEST)
            routing["request_token_ceiling_sent"] = bool(routing["request_token_ceiling_paths"])
        else:
            routing["request_payload"] = None
            routing["request_token_ceiling_paths"] = []
            routing["request_token_ceiling_sent"] = False
        _write_json(routing_path, routing)

    judge_rows = _annotate_judge_attempts(output)
    _write_request_audit(output, routing, judge_rows)

    for filename in ("model-selection.json", "expert-team-result.json", "expert-team-dry-run.json"):
        path = output / filename
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        estimate = data.get("estimated_cost_usd")
        data["estimated_cost_policy"] = "provider-max-theoretical-not-a-limit"
        data["provider_max_theoretical_estimated_cost_usd"] = estimate
        data["hard_cost_limit_usd"] = None
        _write_json(path, data)

    replacements = {
        "- Estimated total cost: `": "- Provider-max theoretical estimate (not a limit): `",
        "- Estimated cost: `": "- Provider-max theoretical estimate (not a limit): `",
    }
    for filename in ("model-ranking.md", "expert-team-report.md"):
        path = output / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")


def _output_dir(argv: list[str]) -> Path:
    for index, value in enumerate(argv):
        if value == "--output-dir" and index + 1 < len(argv):
            return Path(argv[index + 1])
        if value.startswith("--output-dir="):
            return Path(value.split("=", 1)[1])
    return Path("artifacts")


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    output = _output_dir(arguments)
    code = 3
    unhandled: Exception | None = None
    try:
        code = base.main(arguments)
    except Exception as exc:  # noqa: BLE001 - preserve structured production evidence
        unhandled = exc
        output.mkdir(parents=True, exist_ok=True)
        _write_json(
            output / "unhandled-exception.json",
            {
                "type": type(exc).__name__,
                "message": str(exc),
                "stage": "expert_team_hardened_wrapper",
            },
        )
    post_errors: list[dict[str, str]] = []
    for name, operation in (
        ("annotate_post_run_artifacts", lambda: _annotate_post_run_artifacts(output)),
        ("write_call_ledger", lambda: None if "--dry-run" in arguments else write_ledger(output)),
        ("write_artifact_manifest", lambda: write_manifest(output)),
    ):
        try:
            operation()
        except Exception as exc:  # noqa: BLE001 - each post-process failure is audited
            post_errors.append({"operation": name, "type": type(exc).__name__, "message": str(exc)})
    if post_errors:
        output.mkdir(parents=True, exist_ok=True)
        _write_json(output / "postprocess-errors.json", {"version": 1, "errors": post_errors})
        try:
            write_manifest(output)
        except Exception:
            pass
        code = max(code, 3)
    if unhandled is not None:
        code = max(code, 3)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
