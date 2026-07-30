#!/usr/bin/env python3
"""Low-cost one-task live pilot for the V5 benchmark pipeline.

The pilot validates real catalog -> real provider endpoints -> V5/V3/baselines ->
independent blind judges under a strict per-run cost envelope. It never authorizes
production cutover and does not weaken the full five-task benchmark gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import model_market as market
import v5_live_benchmark as base
import v5_live_benchmark_hardened as hardened
from artifact_manifest import write_manifest

DEFAULT_MAX_COST_USD = 0.50
DEFAULT_MAX_CALLS = 40
DEFAULT_STRATEGY_CAP_USD = 0.12
DEFAULT_OUTPUT_ALLOWANCE = 2000
MAX_PROMPT_PPM = 1.50
MAX_COMPLETION_PPM = 4.00


class PilotError(RuntimeError):
    pass


def _write_json(path: str | Path, value: Any) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _number(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None


def _write_output(name: str, value: Any) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={str(value).replace(chr(10), ' ').replace(chr(13), ' ')}\n")


def _load_suite(path: str | Path) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    suite = base._load_json(path)
    tasks = [row for row in suite.get("tasks", []) if isinstance(row, Mapping)]
    if not tasks:
        raise PilotError("pilot suite contains no tasks")
    return suite, tasks


def prepare(event_path: str | Path, output_dir: str | Path, suite_path: str | Path = base.DEFAULT_SUITE) -> int:
    event = base._load_json(event_path)
    issue = event.get("issue") if isinstance(event.get("issue"), Mapping) else {}
    body = str(issue.get("body") or "").strip()
    raw: Mapping[str, Any] = {}
    if body:
        parsed = json.loads(body)
        if not isinstance(parsed, Mapping):
            raise PilotError("Issue body must be one JSON object")
        raw = parsed
    allowed = {
        "pilot_id", "task_id", "max_cost_usd", "max_calls",
        "max_strategy_cost_usd", "output_allowance_tokens",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise PilotError(f"Unknown pilot config fields: {unknown}")
    suite, tasks = _load_suite(suite_path)
    available = [str(row.get("task_id")) for row in tasks]
    task_id = str(raw.get("task_id") or available[0])
    if task_id not in available:
        raise PilotError(f"Unknown task_id: {task_id}")
    max_cost = max(0.10, min(DEFAULT_MAX_COST_USD, float(raw.get("max_cost_usd", DEFAULT_MAX_COST_USD))))
    max_calls = max(16, min(DEFAULT_MAX_CALLS, int(raw.get("max_calls", DEFAULT_MAX_CALLS))))
    strategy_cap = max(0.03, min(DEFAULT_STRATEGY_CAP_USD, float(raw.get("max_strategy_cost_usd", DEFAULT_STRATEGY_CAP_USD))))
    allowance = max(1024, min(2500, int(raw.get("output_allowance_tokens", DEFAULT_OUTPUT_ALLOWANCE))))
    config = {
        "version": 1,
        "mode": "low-cost-pilot",
        "pilot_id": str(raw.get("pilot_id") or f"v5-pilot-{task_id}"),
        "suite_id": suite.get("benchmark_id"),
        "task_id": task_id,
        "max_cost_usd": round(max_cost, 4),
        "max_calls": max_calls,
        "max_strategy_cost_usd": round(min(strategy_cap, max_cost), 4),
        "output_allowance_tokens": allowance,
        "endpoint_price_caps": {
            "prompt_usd_per_million": MAX_PROMPT_PPM,
            "completion_usd_per_million": MAX_COMPLETION_PPM,
        },
        "issue_number": int(issue.get("number") or 0),
        "production_cutover_eligible": False,
    }
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "pilot-config.json", config)
    for key in ("pilot_id", "task_id", "max_cost_usd", "max_calls", "output_allowance_tokens"):
        _write_output(key, config[key])
    return 0


def credit_preflight(config_path: str | Path, output_dir: str | Path) -> int:
    config = base._load_json(config_path)
    required = float(config.get("max_cost_usd", DEFAULT_MAX_COST_USD))
    if required > DEFAULT_MAX_COST_USD + 1e-12:
        raise PilotError("pilot reserve exceeds the hard 0.50 USD ceiling")
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise PilotError("OPENROUTER_API_KEY is not set")
    key_payload = hardened.request_json(hardened.CURRENT_KEY_URL, api_key, 30, 0)
    key_data = key_payload.get("data") if isinstance(key_payload.get("data"), Mapping) else {}
    limit = _number(key_data.get("limit"))
    remaining = _number(key_data.get("limit_remaining"))
    blockers: list[str] = []
    if remaining is not None and remaining + 1e-12 < required:
        blockers.append("api-key-limit-remaining-below-pilot-reserve")
    account_remaining: float | None = None
    management_key = os.getenv("OPENROUTER_MANAGEMENT_KEY", "").strip()
    if management_key:
        try:
            credits = hardened.request_json(hardened.CREDITS_URL, management_key, 30, 0)
            data = credits.get("data") if isinstance(credits.get("data"), Mapping) else {}
            total = _number(data.get("total_credits"))
            usage = _number(data.get("total_usage"))
            if total is not None and usage is not None:
                account_remaining = max(0.0, total - usage)
                if account_remaining + 1e-12 < required:
                    blockers.append("account-credits-below-pilot-reserve")
        except Exception as exc:  # noqa: BLE001
            account_error = str(exc)
        else:
            account_error = None
    else:
        account_error = None
    report = {
        "version": 1,
        "mode": "low-cost-pilot",
        "required_reserve_usd": required,
        "hard_reserve_ceiling_usd": DEFAULT_MAX_COST_USD,
        "current_key": {
            "label": key_data.get("label"),
            "limit_usd": limit,
            "limit_remaining_usd": remaining,
            "usage_usd": _number(key_data.get("usage")),
            "finite_limit_required": False,
        },
        "account_remaining_usd": account_remaining,
        "account_check_error": account_error,
        "blockers": blockers,
        "status": "insufficient" if blockers else (
            "verified" if account_remaining is not None else "bounded-pilot-key-accepted"
        ),
        "safety_basis": [
            "hard run ceiling <= 0.50 USD",
            "per-strategy estimated budget",
            "cheap endpoint price caps",
            "per-direct-call worst-case reservation",
            "first HTTP 402 stops the whole pilot",
        ],
        "model_inference_calls": 0,
        "production_entrypoint_changed": False,
    }
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "pilot-credit-preflight.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 3 if blockers else 0


def _affordable_model(model: Any) -> bool:
    prompt = float(getattr(model, "prompt_price_per_million", math.inf) or math.inf)
    completion = float(getattr(model, "completion_price_per_million", math.inf) or math.inf)
    return prompt <= MAX_PROMPT_PPM and completion <= MAX_COMPLETION_PPM


def _affordable_endpoints(market_bundle: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = [
        row for row in market_bundle.get("endpoints", [])
        if isinstance(row, Mapping)
        and float(row.get("prompt_price_per_million", math.inf)) <= MAX_PROMPT_PPM
        and float(row.get("completion_price_per_million", math.inf)) <= MAX_COMPLETION_PPM
        and float(row.get("reliability", 0.0)) >= 0.80
    ]
    rows.sort(key=lambda row: (
        -float(row.get("benchmark_score", 0.0)),
        -float(row.get("reliability", 0.0)),
        base._endpoint_cost(row),
        str(row.get("endpoint_id")),
    ))
    return rows


def _payload_prompt_chars(payload: Mapping[str, Any]) -> int:
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    return sum(
        len(str(row.get("content") or ""))
        for row in messages
        if isinstance(row, Mapping)
    )


def _worst_case_direct_cost(endpoint: Mapping[str, Any], payload: Mapping[str, Any]) -> float:
    prompt_tokens = max(512, int(_payload_prompt_chars(payload) * 1.25) + 512)
    completion_tokens = int(
        payload.get("max_completion_tokens")
        or payload.get("max_tokens")
        or DEFAULT_OUTPUT_ALLOWANCE
    )
    prompt_ppm = float(endpoint.get("prompt_price_per_million", 0.0))
    completion_ppm = float(endpoint.get("completion_price_per_million", 0.0))
    raw = (prompt_tokens * prompt_ppm + completion_tokens * completion_ppm) / 1_000_000
    return round(raw * 1.25, 8)


def _install_pilot_controls() -> None:
    hardened.install_hardening()
    original_rank = base._rank_v5_models

    def affordable_rank(models: Mapping[str, Any], profile: Any, run: Any) -> list[Any]:
        ranked = original_rank(models, profile, run)
        filtered = [row for row in ranked if _affordable_model(row)]
        if len(filtered) < 4:
            raise PilotError("fewer than four affordable ranked models satisfy the pilot price caps")
        return filtered

    base._rank_v5_models = affordable_rank
    original_direct = base._direct_call

    def bounded_direct(
        run: Any,
        endpoint: Mapping[str, Any],
        payload: Mapping[str, Any],
        ledger: base.GlobalLedger,
        *,
        task_id: str,
        strategy: str,
    ) -> tuple[Mapping[str, Any], float]:
        estimate = _worst_case_direct_cost(endpoint, payload)
        if ledger.actual_cost_usd + estimate > ledger.max_cost_usd + 1e-12:
            raise base.BenchmarkLimitExceeded(
                f"pilot direct-call worst-case estimate {estimate:.6f} USD would exceed remaining run ceiling"
            )
        ledger.events.append({
            "kind": "pilot_worst_case_call_checked",
            "task_id": task_id,
            "strategy": strategy,
            "model": endpoint.get("model_id"),
            "provider": endpoint.get("provider_slug"),
            "estimated_max_cost_usd": estimate,
            "remaining_before_call_usd": round(ledger.remaining_cost(), 8),
        })
        return original_direct(
            run, endpoint, payload, ledger, task_id=task_id, strategy=strategy
        )

    base._direct_call = bounded_direct
    original_judges = base._judge_endpoints

    def affordable_judges(market_bundle: Mapping[str, Any], used_models: set[str]) -> list[Mapping[str, Any]]:
        scoped = dict(market_bundle)
        scoped["endpoints"] = _affordable_endpoints(market_bundle)
        return original_judges(scoped, used_models)

    base._judge_endpoints = affordable_judges


def _pilot_gate(outcomes: Sequence[base.StrategyOutcome], evaluation: Mapping[str, Any], ledger: base.GlobalLedger) -> dict[str, Any]:
    by_strategy = {row.strategy: row for row in outcomes}
    successes = [row for row in outcomes if row.status == "success"]
    blockers: list[str] = []
    if by_strategy.get("v5_joint_graph") is None or by_strategy["v5_joint_graph"].status != "success":
        blockers.append("v5-pilot-execution-failed")
    if by_strategy.get("v3") is None or by_strategy["v3"].status != "success":
        blockers.append("v3-pilot-execution-failed")
    if len(successes) < 4:
        blockers.append("fewer-than-4-of-6-strategies-succeeded")
    if int(evaluation.get("judge_count", 0)) < 2:
        blockers.append("fewer-than-2-valid-blind-judges")
    if any(row.safety_failure for row in outcomes):
        blockers.append("safety-failure")
    if ledger.actual_cost_usd > ledger.max_cost_usd + 1e-12:
        blockers.append("pilot-cost-ceiling-exceeded")
    return {
        "pilot_gate_passed": not blockers,
        "blockers": blockers,
        "successful_strategies": sorted(row.strategy for row in successes),
        "required_successes": 4,
        "judge_count": int(evaluation.get("judge_count", 0)),
        "production_cutover_allowed": False,
        "reason": "A one-task low-cost pilot is operational evidence only and cannot authorize V5 production cutover.",
    }


def run_pilot(config_path: str | Path, suite_path: str | Path, output_dir: str | Path) -> int:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise PilotError("OPENROUTER_API_KEY is not set")
    config = base._load_json(config_path)
    os.environ["V5_BENCHMARK_OUTPUT_ALLOWANCE_TOKENS"] = str(config["output_allowance_tokens"])
    _install_pilot_controls()
    _, tasks = _load_suite(suite_path)
    task = next((row for row in tasks if str(row.get("task_id")) == str(config["task_id"])), None)
    if task is None:
        raise PilotError("configured pilot task is absent from suite")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    task_id = str(task["task_id"])
    task_root = root / "task" / task_id
    task_root.mkdir(parents=True, exist_ok=True)
    ledger = base.GlobalLedger(float(config["max_cost_usd"]), int(config["max_calls"]))
    strategy_cap = min(float(config["max_strategy_cost_usd"]), ledger.max_cost_usd)
    started = time.monotonic()
    outcomes: list[base.StrategyOutcome] = []
    evaluation: Mapping[str, Any] = {}
    scores: Mapping[str, float] = {}
    status = "complete"
    error: str | None = None
    catalog_run = market.build_run_config(base._namespace(base._task_text(task), root / "catalog", ranking_limit=24))
    models, catalog_source = market.fetch_catalog(catalog_run)
    endpoint_cache: dict[str, Mapping[str, Any]] = {}
    market_bundle: Mapping[str, Any] = {}
    try:
        v5, market_bundle = base._v5_strategy(
            task,
            task_root / "v5_joint_graph",
            ledger,
            models,
            endpoint_cache,
            min(strategy_cap, max(0.03, ledger.remaining_cost())),
        )
        outcomes.append(v5)
        outcomes.append(base._v3_strategy(
            task,
            task_root / "v3",
            ledger,
            min(strategy_cap, max(0.03, ledger.remaining_cost())),
        ))
        endpoints = _affordable_endpoints(market_bundle)
        if len({str(row.get("model_id")) for row in endpoints}) < 4:
            raise PilotError("fewer than four affordable direct model endpoints are available")
        strongest = endpoints[0]
        cheapest = min(endpoints, key=lambda row: (
            base._endpoint_cost(row),
            -float(row.get("benchmark_score", 0.0)),
        ))
        direct_run = market.build_run_config(base._namespace(base._task_text(task), task_root / "direct", ranking_limit=24))
        outcomes.append(base._single_strategy(direct_run, task, strongest, ledger, "strongest_single_model"))
        outcomes.append(base._single_strategy(direct_run, task, cheapest, ledger, "lowest_price_single_model"))
        fixed = base._select_distinct(
            endpoints,
            (
                ("quantitative_reasoning", "evidence_validation", "statistics"),
                ("general_analysis", "decision_comparison", "delivery"),
                ("adversarial_reasoning", "risk_discovery", "evidence_validation"),
                ("synthesis", "complex_reasoning", "delivery"),
            ),
        )
        outcomes.append(base._team_strategy(direct_run, task, fixed, ledger, "fixed_3_plus_1"))
        seed = int(hashlib.sha256(task_id.encode()).hexdigest()[:12], 16)
        randomized = base._select_distinct(
            endpoints,
            (("general_analysis",), ("general_analysis",), ("general_analysis",), ("synthesis",)),
            random_seed=seed,
        )
        outcomes.append(base._team_strategy(direct_run, task, randomized, ledger, "random_feasible"))
        scores, evaluation = base._evaluate_task(
            direct_run, task, outcomes, market_bundle, ledger, task_root
        )
    except base.BenchmarkLimitExceeded as exc:
        status = "bounded_stop"
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        status = "technical_failure"
        error = str(exc)
    gate = _pilot_gate(outcomes, evaluation, ledger)
    records = []
    for outcome in outcomes:
        row = outcome.record()
        row["blind_quality_score"] = float(scores.get(outcome.strategy, 0.0))
        records.append(row)
    result = {
        "version": 1,
        "mode": "low-cost-pilot",
        "pilot_id": config["pilot_id"],
        "task_id": task_id,
        "status": status,
        "error": error,
        "catalog_source": catalog_source,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "records": records,
        "evaluation": dict(evaluation),
        "ledger": ledger.snapshot(),
        "pilot_gate": gate,
        "endpoint_price_caps": config["endpoint_price_caps"],
        "output_allowance_tokens": config["output_allowance_tokens"],
        "production_entrypoint_changed": False,
        "production_cutover_allowed": False,
        "full_benchmark_still_required": True,
    }
    _write_json(root / "v5-low-cost-pilot-result.json", result)
    lines = [
        "# V5 Low-Cost Pilot",
        "",
        f"- Status: `{status}`",
        f"- Pilot ID: `{config['pilot_id']}`",
        f"- Task: `{task_id}`",
        f"- Calls: `{ledger.calls}` / `{ledger.max_calls}`",
        f"- Actual cost: `${ledger.actual_cost_usd:.6f}` / `${ledger.max_cost_usd:.2f}`",
        f"- Pilot gate passed: `{str(bool(gate['pilot_gate_passed'])).lower()}`",
        "- Production cutover allowed: `false`",
        "",
        "## Strategy results",
        "",
        "| Strategy | Status | Cost USD | Calls | Blind quality |",
        "|---|---|---:|---:|---:|",
    ]
    for row in records:
        lines.append(
            f"| {row['strategy']} | {row['status']} | {float(row['actual_cost_usd']):.6f} | "
            f"{row['call_count']} | {float(row.get('blind_quality_score', 0.0)):.3f} |"
        )
    lines.extend(["", "## Pilot blockers", ""])
    blockers = gate.get("blockers") or []
    lines.extend(f"- `{item}`" for item in blockers) if blockers else lines.append("- None")
    if error:
        lines.extend(["", "## Error", "", f"`{error}`"])
    (root / "v5-low-cost-pilot-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_manifest(root)
    return 0 if status == "complete" else 2


def render(output_dir: str | Path, run_url: str) -> int:
    root = Path(output_dir)
    summary = root / "v5-low-cost-pilot-summary.md"
    if not summary.exists():
        print("## V5_PILOT_FAILED\n\nPilot summary was not generated.\n")
        return 2
    result = base._load_json(root / "v5-low-cost-pilot-result.json")
    print(
        "## V5_PILOT_COMPLETED\n\n"
        + summary.read_text(encoding="utf-8")
        + f"\n- Run: `{run_url}`\n"
        + "- Production entrypoint changed: `false`\n"
        + "- Full five-task blind benchmark still required: `true`\n"
    )
    return 0 if result.get("status") == "complete" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded low-cost V5 live pilot")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--event-path", required=True)
    prepare_parser.add_argument("--output-dir", required=True)
    prepare_parser.add_argument("--suite", default=str(base.DEFAULT_SUITE))
    credit_parser = sub.add_parser("credit-check")
    credit_parser.add_argument("--config", required=True)
    credit_parser.add_argument("--output-dir", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--suite", default=str(base.DEFAULT_SUITE))
    run_parser.add_argument("--output-dir", required=True)
    render_parser = sub.add_parser("render")
    render_parser.add_argument("--output-dir", required=True)
    render_parser.add_argument("--run-url", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            return prepare(args.event_path, args.output_dir, args.suite)
        if args.command == "credit-check":
            return credit_preflight(args.config, args.output_dir)
        if args.command == "run":
            return run_pilot(args.config, args.suite, args.output_dir)
        if args.command == "render":
            return render(args.output_dir, args.run_url)
        raise PilotError(f"unsupported command: {args.command}")
    except (PilotError, base.LiveBenchmarkError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
