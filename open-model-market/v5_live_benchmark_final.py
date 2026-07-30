#!/usr/bin/env python3
"""Final five-task live benchmark wrapper.

This compatibility layer leaves the V3 production entry untouched while making
live cutover evidence compare the active V3 value optimizer with the active V5
cost-performance graph optimizer. It also treats an OpenRouter key without a
reported finite spending limit as bounded by the benchmark's own global ledger,
while retaining account-credit checks when a management key is available and
immediate fail-fast behavior for HTTP 402 responses.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_live_benchmark as base
import v5_live_benchmark_hardened as hardened
import v5_value_optimizer


def _number(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None


def credit_preflight(config_path: str | Path, output_dir: str | Path) -> int:
    """Verify known limits without falsely rejecting an unbounded API key.

    A finite key limit remains a hard preflight check. If OpenRouter reports no
    finite key limit, the benchmark is still bounded by max_cost_usd and
    max_calls in GlobalLedger. Account-level credits are additionally verified
    when OPENROUTER_MANAGEMENT_KEY is configured. Any real 402 response still
    stops execution immediately through the existing hardened wrapper.
    """
    config = base._load_json(config_path)
    required = float(config.get("max_cost_usd", 20.0))
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise base.LiveBenchmarkError("OPENROUTER_API_KEY is not set")

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "version": 3,
        "required_reserve_usd": required,
        "output_allowance_tokens": hardened.ALLOWANCE,
        "runtime_global_cost_ceiling_usd": required,
        "runtime_global_call_ceiling": int(config.get("max_calls", 200)),
        "status": "unverified",
        "production_entrypoint_changed": False,
    }

    key_payload = hardened.request_json(hardened.CURRENT_KEY_URL, api_key, 30, 0)
    key_data = key_payload.get("data") if isinstance(key_payload.get("data"), Mapping) else {}
    limit = _number(key_data.get("limit"))
    limit_remaining = _number(key_data.get("limit_remaining"))
    report["current_key"] = {
        "label": key_data.get("label"),
        "is_free_tier": bool(key_data.get("is_free_tier")),
        "is_management_key": bool(key_data.get("is_management_key")),
        "limit_usd": limit,
        "limit_remaining_usd": limit_remaining,
        "usage_usd": _number(key_data.get("usage")),
        "expires_at": key_data.get("expires_at"),
        "limit_mode": "finite" if limit is not None and limit_remaining is not None else "not-reported-or-unbounded",
    }

    management_key = os.getenv("OPENROUTER_MANAGEMENT_KEY", "").strip()
    account_remaining: float | None = None
    if management_key:
        try:
            payload = hardened.request_json(hardened.CREDITS_URL, management_key, 30, 0)
            data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
            total = _number(data.get("total_credits"))
            usage = _number(data.get("total_usage"))
            if total is not None and usage is not None:
                account_remaining = max(0.0, total - usage)
            report["account_credits"] = {
                "total_credits_usd": total,
                "total_usage_usd": usage,
                "remaining_usd": account_remaining,
                "verified_with_management_key": True,
            }
        except Exception as exc:  # noqa: BLE001 - preserve preflight evidence
            report["account_credits"] = {
                "verified_with_management_key": False,
                "error": str(exc),
            }

    blockers: list[str] = []
    if limit_remaining is not None and limit_remaining + 1e-12 < required:
        blockers.append("api-key-limit-remaining-below-benchmark-reserve")
    if account_remaining is not None and account_remaining + 1e-12 < required:
        blockers.append("account-credits-below-benchmark-reserve")

    if limit_remaining is None:
        report["api_key_limit_warning"] = (
            "OpenRouter did not report a finite API-key limit. The run is permitted only because "
            "GlobalLedger enforces the configured cost and call ceilings and HTTP 402 is fail-fast."
        )
    if account_remaining is None:
        report["account_credit_warning"] = (
            "Account-level credits are not independently verified. A real HTTP 402 response stops the benchmark immediately."
        )

    report["blockers"] = blockers
    if blockers:
        report["status"] = "insufficient"
    elif account_remaining is not None:
        report["status"] = "account-and-runtime-ledger-verified"
    elif limit_remaining is not None:
        report["status"] = "key-limit-and-runtime-ledger-verified"
    else:
        report["status"] = "runtime-ledger-bounded"

    hardened._write_json(root / "credit-preflight.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 3 if blockers else 0


def _install_final_v3_entry() -> None:
    original_run = base.subprocess.run
    benchmark_entry = str(Path(__file__).with_name("v3_benchmark_entry_final.py"))

    def benchmark_subprocess(command: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(command, list) and len(command) > 1 and str(command[1]).endswith("expert_team_hardened.py"):
            command = list(command)
            command[1] = benchmark_entry
        return original_run(command, *args, **kwargs)

    base.subprocess.run = benchmark_subprocess


def install_final_alignment() -> None:
    base.compile_and_optimize_v5 = v5_value_optimizer.compile_and_optimize_v5
    hardened.credit_preflight = credit_preflight
    hardened._install_v3_entry = _install_final_v3_entry


def main(argv: Sequence[str] | None = None) -> int:
    install_final_alignment()
    return hardened.main(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
