#!/usr/bin/env python3
"""Hardened live-benchmark wrapper: credit preflight, output allowance, and 402 fail-fast."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, Sequence

import v5_executor as executor
import v5_live_benchmark as base
from openrouter_api import request_json

CURRENT_KEY_URL = "https://openrouter.ai/api/v1/key"
CREDITS_URL = "https://openrouter.ai/api/v1/credits"
_JUDGE_ATTEMPTS: list[dict[str, Any]] = []
_JUDGE_LOCK = Lock()


def _allowance() -> int:
    raw = os.getenv("V5_BENCHMARK_OUTPUT_ALLOWANCE_TOKENS", "10000")
    try:
        return max(1024, min(10000, int(raw)))
    except ValueError:
        return 10000


ALLOWANCE = _allowance()


def _write_json(path: str | Path, value: Any) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _number(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None


def credit_preflight(config_path: str | Path, output_dir: str | Path) -> int:
    config = base._load_json(config_path)
    required = float(config.get("max_cost_usd", 20.0))
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise base.LiveBenchmarkError("OPENROUTER_API_KEY is not set")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "version": 1,
        "required_reserve_usd": required,
        "output_allowance_tokens": ALLOWANCE,
        "status": "unverified",
        "production_entrypoint_changed": False,
    }
    key_payload = request_json(CURRENT_KEY_URL, api_key, 30, 0)
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
    }
    management_key = os.getenv("OPENROUTER_MANAGEMENT_KEY", "").strip()
    account_remaining: float | None = None
    if management_key:
        try:
            credit_payload = request_json(CREDITS_URL, management_key, 30, 0)
            credit_data = credit_payload.get("data") if isinstance(credit_payload.get("data"), Mapping) else {}
            total = _number(credit_data.get("total_credits"))
            usage = _number(credit_data.get("total_usage"))
            if total is not None and usage is not None:
                account_remaining = max(0.0, total - usage)
            report["account_credits"] = {
                "total_credits_usd": total,
                "total_usage_usd": usage,
                "remaining_usd": account_remaining,
                "verified_with_management_key": True,
            }
        except Exception as exc:  # noqa: BLE001 - preflight evidence is preserved
            report["account_credits"] = {
                "verified_with_management_key": False,
                "error": str(exc),
            }
    blockers: list[str] = []
    if limit_remaining is not None and limit_remaining + 1e-12 < required:
        blockers.append("api-key-limit-remaining-below-benchmark-reserve")
    if account_remaining is not None and account_remaining + 1e-12 < required:
        blockers.append("account-credits-below-benchmark-reserve")
    if limit_remaining is None and account_remaining is None:
        report["warning"] = "Neither a finite API-key remaining limit nor account credits could be verified."
    report["blockers"] = blockers
    report["status"] = "insufficient" if blockers else ("verified" if account_remaining is not None else "partially_verified")
    _write_json(root / "credit-preflight.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 3 if blockers else 0


def _is_credit_error(value: Any) -> bool:
    text = str(value or "").casefold()
    return "http 402" in text or "insufficient credits" in text or "requires more credits" in text


def _raise_if_credit_failure(outcome: Any, *, artifact_root: str | Path | None = None) -> None:
    if _is_credit_error(getattr(outcome, "error", None)):
        raise base.BenchmarkLimitExceeded("OpenRouter available credits or API-key spending limit are insufficient.")
    if artifact_root is None:
        return
    node_path = Path(artifact_root) / "v5-node-results.json"
    if not node_path.exists():
        return
    try:
        rows = json.loads(node_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for row in rows if isinstance(rows, list) else []:
        for attempt in row.get("attempts", []) if isinstance(row, Mapping) else []:
            if _is_credit_error(attempt.get("error")):
                raise base.BenchmarkLimitExceeded("OpenRouter available credits or API-key spending limit are insufficient.")


def _annotate_v5_audit(output_dir: str | Path | None) -> None:
    if output_dir is None:
        return
    audit_path = Path(output_dir) / "v5-request-audit.json"
    if not audit_path.exists():
        return
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    requests = audit.get("requests") if isinstance(audit.get("requests"), list) else []
    valid = bool(requests) and all(
        isinstance(row, Mapping) and int(row.get("max_completion_tokens", -1)) == ALLOWANCE
        for row in requests
    )
    audit["benchmark_output_allowance_tokens"] = ALLOWANCE
    audit["benchmark_output_allowance_policy"] = "maximum-permitted-not-required"
    audit["benchmark_output_allowance_consistent"] = valid
    audit["artificial_token_ceiling_sent"] = False if valid else audit.get("artificial_token_ceiling_sent", False)
    audit["production_policy_changed"] = False
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def _install_output_allowance() -> None:
    original_safe = base._safe_payload

    def allowed_safe(endpoint: Mapping[str, Any], system: str, user: str) -> dict[str, Any]:
        payload = original_safe(endpoint, system, user)
        payload["max_completion_tokens"] = ALLOWANCE
        return payload

    base._safe_payload = allowed_safe
    original_node_payload = executor.build_node_payload

    def allowed_node_payload(node: Any, original_task: str, upstream: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        payload = original_node_payload(node, original_task, upstream)
        payload["max_completion_tokens"] = ALLOWANCE
        return payload

    executor.build_node_payload = allowed_node_payload
    original_execute = base.execute_v5_graph

    def allowed_execute(*args: Any, **kwargs: Any) -> Any:
        output_dir = kwargs.get("output_dir")
        try:
            return original_execute(*args, **kwargs)
        finally:
            _annotate_v5_audit(output_dir)

    base.execute_v5_graph = allowed_execute


def _install_v3_entry() -> None:
    original_run = base.subprocess.run
    benchmark_entry = str(Path(__file__).with_name("v3_benchmark_entry.py"))

    def benchmark_subprocess(command: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(command, list) and len(command) > 1 and str(command[1]).endswith("expert_team_hardened.py"):
            command = list(command)
            command[1] = benchmark_entry
        return original_run(command, *args, **kwargs)

    base.subprocess.run = benchmark_subprocess


def _install_credit_fail_fast() -> None:
    original_single = base._single_strategy
    original_team = base._team_strategy
    original_v3 = base._v3_strategy
    original_v5 = base._v5_strategy

    def single(*args: Any, **kwargs: Any) -> Any:
        outcome = original_single(*args, **kwargs)
        _raise_if_credit_failure(outcome)
        return outcome

    def team(*args: Any, **kwargs: Any) -> Any:
        outcome = original_team(*args, **kwargs)
        _raise_if_credit_failure(outcome)
        return outcome

    def v3(*args: Any, **kwargs: Any) -> Any:
        outcome = original_v3(*args, **kwargs)
        _raise_if_credit_failure(outcome)
        return outcome

    def v5(task: Mapping[str, Any], root: Path, *args: Any, **kwargs: Any) -> Any:
        outcome, market = original_v5(task, root, *args, **kwargs)
        _raise_if_credit_failure(outcome, artifact_root=root)
        return outcome, market

    base._single_strategy = single
    base._team_strategy = team
    base._v3_strategy = v3
    base._v5_strategy = v5


def _install_affordable_judges() -> None:
    original = base._judge_endpoints

    def judges(market_bundle: Mapping[str, Any], used_models: set[str]) -> list[Mapping[str, Any]]:
        endpoints = [row for row in market_bundle.get("endpoints", []) if isinstance(row, Mapping)]
        affordable = [
            row for row in endpoints
            if float(row.get("completion_price_per_million", 1e9)) <= 15.0
            and float(row.get("prompt_price_per_million", 1e9)) <= 5.0
        ]
        scoped = dict(market_bundle)
        scoped["endpoints"] = affordable if len({str(row.get("model_id")) for row in affordable}) >= 3 else endpoints
        return original(scoped, used_models)

    base._judge_endpoints = judges


def _install_judge_telemetry() -> None:
    original_direct = base._direct_call
    original_evaluate = base._evaluate_task

    def direct(*args: Any, **kwargs: Any) -> Any:
        strategy = str(kwargs.get("strategy") or "")
        try:
            response, latency = original_direct(*args, **kwargs)
            if strategy == "blind_judge":
                endpoint = args[1] if len(args) > 1 and isinstance(args[1], Mapping) else {}
                with _JUDGE_LOCK:
                    _JUDGE_ATTEMPTS.append({
                        "status": "response_received",
                        "model": endpoint.get("model_id"),
                        "provider": endpoint.get("provider_slug"),
                        "latency_seconds": latency,
                        "cost_usd": base._actual_cost(response),
                        "finish_reason": base._finish_reason(response),
                        "answer_chars": len(base._answer(response)),
                        "answer": base._answer(response),
                    })
            return response, latency
        except Exception as exc:
            if strategy == "blind_judge":
                endpoint = args[1] if len(args) > 1 and isinstance(args[1], Mapping) else {}
                with _JUDGE_LOCK:
                    _JUDGE_ATTEMPTS.append({
                        "status": "request_failed",
                        "model": endpoint.get("model_id"),
                        "provider": endpoint.get("provider_slug"),
                        "error": str(exc),
                    })
            raise

    def evaluate(*args: Any, **kwargs: Any) -> Any:
        root = args[5] if len(args) > 5 else kwargs.get("root")
        with _JUDGE_LOCK:
            _JUDGE_ATTEMPTS.clear()
        try:
            return original_evaluate(*args, **kwargs)
        finally:
            if root is not None:
                with _JUDGE_LOCK:
                    snapshot = list(_JUDGE_ATTEMPTS)
                _write_json(Path(root) / "blind-evaluation-attempts.json", {
                    "version": 1,
                    "output_allowance_tokens": ALLOWANCE,
                    "attempts": snapshot,
                })

    base._direct_call = direct
    base._evaluate_task = evaluate


def install_hardening() -> None:
    _install_output_allowance()
    _install_v3_entry()
    _install_credit_fail_fast()
    _install_affordable_judges()
    _install_judge_telemetry()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hardened V5 live benchmark wrapper")
    sub = parser.add_subparsers(dest="command", required=True)
    credit = sub.add_parser("credit-check")
    credit.add_argument("--config", required=True)
    credit.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    if arguments and arguments[0] == "credit-check":
        args = build_parser().parse_args(arguments)
        try:
            return credit_preflight(args.config, args.output_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    install_hardening()
    return base.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
