#!/usr/bin/env python3
"""Benchmark-only V3 entry with a model-compatible 10k output allowance.

Production V3 remains unchanged. This module is used only by the five-task live
blind benchmark so OpenRouter can reserve a bounded completion allowance without
forcing every model through the same unsupported token parameter name.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import expert_team_hardened as hardened


def _allowance() -> int:
    raw = os.getenv("V5_BENCHMARK_OUTPUT_ALLOWANCE_TOKENS", "10000")
    try:
        return max(1024, min(10000, int(raw)))
    except ValueError:
        return 10000


ALLOWANCE = _allowance()


def _supported(model: Any) -> set[str]:
    return {str(value).casefold() for value in (getattr(model, "supported_parameters", None) or [])}


def _token_field(model: Any) -> tuple[str, bool]:
    supported = _supported(model)
    if "max_completion_tokens" in supported:
        return "max_completion_tokens", True
    if "max_tokens" in supported:
        return "max_tokens", True
    # OpenRouter accepts max_tokens as its compatibility field. When the model
    # catalog does not advertise it, do not require provider-level exact support;
    # this avoids excluding every endpoint while preserving the 10k allowance.
    return "max_tokens", False


def _apply_allowance(payload: dict[str, Any], model: Any) -> dict[str, Any]:
    payload.pop("max_tokens", None)
    payload.pop("max_completion_tokens", None)
    field, advertised = _token_field(model)
    payload[field] = ALLOWANCE
    provider = payload.get("provider")
    if isinstance(provider, dict) and not advertised:
        provider["require_parameters"] = False
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict):
        reasoning.pop("max_tokens", None)
        reasoning["effort"] = "low"
        reasoning["exclude"] = True
    return payload


_original_expert_payload = hardened.direct_calls.build_expert_payload


def _benchmark_expert_payload(run: Any, profile: Any, expert: Any, model: Any) -> dict[str, Any]:
    return _apply_allowance(_original_expert_payload(run, profile, expert, model), model)


hardened.direct_calls.build_expert_payload = _benchmark_expert_payload

_original_judge_payload = hardened.base.build_judge_payload


def _benchmark_judge_payload(
    run: Any,
    profile: Any,
    judge: Any,
    judge_model: Any,
    results: Any,
) -> dict[str, Any]:
    return _apply_allowance(
        _original_judge_payload(run, profile, judge, judge_model, results),
        judge_model,
    )


hardened.base.build_judge_payload = _benchmark_judge_payload

_original_token_paths = hardened._token_ceiling_paths


def _benchmark_token_paths(value: Any, prefix: str = "") -> list[str]:
    paths = _original_token_paths(value, prefix)
    if isinstance(value, dict):
        for key in ("max_tokens", "max_completion_tokens"):
            if value.get(key) == ALLOWANCE:
                allowed_path = f"{prefix}.{key}" if prefix else key
                paths = [path for path in paths if path != allowed_path]
    return paths


hardened._token_ceiling_paths = _benchmark_token_paths


def _output_dir(argv: list[str]) -> Path | None:
    try:
        index = argv.index("--output-dir")
        return Path(argv[index + 1])
    except (ValueError, IndexError):
        return None


def _annotate(root: Path | None) -> None:
    if root is None:
        return
    audit_path = root / "request-audit.json"
    if not audit_path.exists():
        return
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    entries = audit.get("entries") if isinstance(audit.get("entries"), list) else []
    fields = sorted(
        {
            key
            for row in entries
            if isinstance(row, dict)
            for key in ("max_tokens", "max_completion_tokens")
            if isinstance(row.get("payload"), dict) and row["payload"].get(key) == ALLOWANCE
        }
    )
    audit["benchmark_output_allowance_tokens"] = ALLOWANCE
    audit["benchmark_output_allowance_parameters"] = fields or ["model-compatible-token-field"]
    audit["benchmark_output_allowance_policy"] = "maximum-permitted-not-required"
    audit["production_policy_changed"] = False
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    code = hardened.main()
    _annotate(_output_dir(sys.argv[1:]))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
