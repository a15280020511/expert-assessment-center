#!/usr/bin/env python3
"""Build one auditable ledger containing every paid model call attempt."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _money(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _entry(
    *,
    call_type: str,
    model: str | None,
    diagnostics: Mapping[str, Any],
    estimated: Any,
    seat: str | None = None,
    attempt_index: int | None = None,
    status: str | None = None,
    error_code: str | None = None,
    error: str | None = None,
    answer_chars: int | None = None,
) -> dict[str, Any]:
    actual = _money(diagnostics.get("cost"))
    estimated_cost = _money(estimated)
    if actual is not None:
        conservative = actual
        evidence = "provider_actual"
    elif estimated_cost is not None:
        conservative = estimated_cost
        evidence = "estimated_only"
    else:
        conservative = None
        evidence = "unknown"
    return {
        "type": call_type,
        "seat": seat,
        "attempt_index": attempt_index,
        "model": model,
        "response_id": diagnostics.get("response_id"),
        "provider": diagnostics.get("provider"),
        "finish_reason": diagnostics.get("finish_reason"),
        "native_finish_reason": diagnostics.get("native_finish_reason"),
        "prompt_tokens": diagnostics.get("prompt_tokens"),
        "completion_tokens": diagnostics.get("completion_tokens"),
        "reasoning_tokens": diagnostics.get("reasoning_tokens"),
        "answer_chars": answer_chars,
        "status": status,
        "error_code": error_code,
        "error": error,
        "actual_cost_usd": actual,
        "estimated_cost_usd": estimated_cost,
        "conservative_cost_usd": conservative,
        "cost_evidence": evidence,
    }


def _judge_rows(output_dir: Path) -> list[dict[str, Any]]:
    rows = _load(output_dir / "judge-attempts.json", [])
    if isinstance(rows, list) and rows:
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    judge = _load(output_dir / "judge-response-diagnostics.json", {})
    result = _load(output_dir / "expert-team-result.json", {})
    if not isinstance(judge, dict) or not judge:
        return []
    judge_model = None
    if isinstance(result, dict) and isinstance(result.get("judge"), dict):
        judge_model = str(result["judge"].get("model_id") or "") or None
    if not judge_model:
        judge_model = str(judge.get("model") or judge.get("requested_model") or "") or None
    return [
        {
            "attempt_index": 1,
            "model": judge_model,
            "response_diagnostics": judge,
            "estimated_cost": judge.get("estimated_cost"),
            "status": result.get("judge_status") if isinstance(result, dict) else None,
        }
    ]


def _providers(entries: list[dict[str, Any]], call_types: set[str] | None = None) -> list[str]:
    values = {
        str(item.get("provider") or "").strip()
        for item in entries
        if (call_types is None or item.get("type") in call_types)
        and str(item.get("provider") or "").strip()
    }
    return sorted(values)


def build_ledger(output_dir: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    routing = _load(output_dir / "task-routing.json", {})
    if isinstance(routing, dict) and routing.get("call_consumed"):
        info = routing.get("response_diagnostics") if isinstance(routing.get("response_diagnostics"), dict) else {}
        entries.append(
            _entry(
                call_type="semantic_router",
                model=str(routing.get("model_id") or "") or None,
                diagnostics=info,
                estimated=routing.get("estimated_cost_usd"),
                attempt_index=1,
                status=str(routing.get("status") or "") or None,
                error=str(routing.get("error") or "") or None,
            )
        )

    experts = _load(output_dir / "expert-responses.json", [])
    if isinstance(experts, list):
        for result in experts:
            if not isinstance(result, dict):
                continue
            attempts = result.get("attempts") if isinstance(result.get("attempts"), list) else []
            real_attempts = [item for item in attempts if isinstance(item, dict) and item.get("replacement") is not True]
            for real_index, attempt in enumerate(real_attempts, 1):
                info = attempt.get("response_diagnostics") if isinstance(attempt.get("response_diagnostics"), dict) else {}
                status = str(result.get("status") or "") or None if real_index == len(real_attempts) else "failed"
                entries.append(
                    _entry(
                        call_type="expert",
                        seat=str(result.get("seat_key") or "") or None,
                        attempt_index=real_index,
                        model=str(attempt.get("model") or "") or None,
                        diagnostics=info,
                        estimated=attempt.get("estimated_cost"),
                        status=status,
                        error=str(attempt.get("error") or "") or None,
                        answer_chars=int(attempt.get("partial_answer_chars") or 0) or None,
                    )
                )

    for index, attempt in enumerate(_judge_rows(output_dir), 1):
        info = attempt.get("response_diagnostics") if isinstance(attempt.get("response_diagnostics"), Mapping) else {}
        entries.append(
            _entry(
                call_type="judge",
                model=str(attempt.get("model") or info.get("model") or "") or None,
                diagnostics=info,
                estimated=attempt.get("estimated_cost"),
                attempt_index=int(attempt.get("attempt_index") or index),
                status=str(attempt.get("status") or "") or None,
                error_code=str(attempt.get("error_code") or "") or None,
                error=str(attempt.get("error") or "") or None,
                answer_chars=int(attempt.get("answer_chars") or 0) or None,
            )
        )

    actual_values = [item["actual_cost_usd"] for item in entries if item["actual_cost_usd"] is not None]
    conservative_values = [item["conservative_cost_usd"] for item in entries if item["conservative_cost_usd"] is not None]
    unknown = [item for item in entries if item["cost_evidence"] == "unknown"]
    estimated_only = [item for item in entries if item["cost_evidence"] == "estimated_only"]
    expert_calls = sum(item["type"] == "expert" for item in entries)
    judge_calls = sum(item["type"] == "judge" for item in entries)
    expert_replacements = max(0, expert_calls - 3)
    judge_replacements = max(0, judge_calls - 1)
    all_providers = _providers(entries)
    substantive_providers = _providers(entries, {"expert", "judge"})
    summary = {
        "call_count": len(entries),
        "semantic_router_calls": sum(item["type"] == "semantic_router" for item in entries),
        "expert_attempt_calls": expert_calls,
        "judge_attempt_calls": judge_calls,
        "expert_replacement_calls": expert_replacements,
        "judge_replacement_calls": judge_replacements,
        "replacement_calls": expert_replacements + judge_replacements,
        "failed_paid_calls": sum(str(item.get("status") or "").lower() in {"failed", "error", "success_partial"} for item in entries),
        "provider_actual_cost_usd": round(sum(actual_values), 9),
        "conservative_cost_usd": round(sum(conservative_values), 9),
        "cost_evidence_status": "unknown" if unknown else "estimated_only" if estimated_only else "known",
        "unknown_cost_calls": len(unknown),
        "estimated_only_calls": len(estimated_only),
        "minimum_fixed_calls_present": len(entries) >= 4,
        "all_providers": all_providers,
        "all_provider_count": len(all_providers),
        "substantive_providers": substantive_providers,
        "substantive_provider_count": len(substantive_providers),
        "hard_cost_limit_usd": None,
    }
    return {"version": 3, "entries": entries, "summary": summary}


def write_ledger(output_dir: Path) -> dict[str, Any]:
    ledger = build_ledger(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "call-ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return ledger


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    args = parser.parse_args()
    ledger = write_ledger(Path(args.output_dir))
    print(json.dumps(ledger["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
