#!/usr/bin/env python3
"""Benchmark-only V3 entry with a 10k output allowance, never used by production."""
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


def _benchmark_output_allowance(payload: dict[str, Any]) -> dict[str, Any]:
    payload.pop("max_tokens", None)
    payload.pop("max_completion_tokens", None)
    # OpenRouter currently exposes max_tokens in direct Endpoint capability lists.
    # This is a maximum allowance, not required output length.
    payload["max_tokens"] = ALLOWANCE
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict):
        reasoning.pop("max_tokens", None)
        reasoning["effort"] = "low"
        reasoning["exclude"] = True
    return payload


_original_token_paths = hardened._token_ceiling_paths


def _benchmark_token_paths(value: Any, prefix: str = "") -> list[str]:
    paths = _original_token_paths(value, prefix)
    if isinstance(value, dict) and value.get("max_tokens") == ALLOWANCE:
        allowed_path = f"{prefix}.max_tokens" if prefix else "max_tokens"
        paths = [path for path in paths if path != allowed_path]
    return paths


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
    audit["benchmark_output_allowance_tokens"] = ALLOWANCE
    audit["benchmark_output_allowance_parameter"] = "max_tokens"
    audit["benchmark_output_allowance_policy"] = "maximum-permitted-not-required"
    audit["production_policy_changed"] = False
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    hardened._remove_token_ceilings = _benchmark_output_allowance
    hardened._token_ceiling_paths = _benchmark_token_paths
    code = hardened.main()
    _annotate(_output_dir(sys.argv[1:]))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
