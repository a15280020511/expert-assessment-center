#!/usr/bin/env python3
"""Low-cost three-task live cutover benchmark for active V5 versus preserved V3."""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import model_market as market
import v5_live_benchmark as base
import v5_live_benchmark_final as final
import v5_live_benchmark_hardened as hardened
from artifact_manifest import write_manifest
from execution_graph import GraphLimits as OriginalGraphLimits

DEFAULT_TASK_IDS = (
    "retail-expansion-unit-economics",
    "software-job-runner-security",
    "public-health-rumor-response",
)
DEFAULT_MAX_COST_USD = 1.5
HARD_MAX_COST_USD = 2.0
DEFAULT_MAX_CALLS = 45
HARD_MAX_CALLS = 60
DEFAULT_STRATEGY_CAP_USD = 0.25
DEFAULT_OUTPUT_ALLOWANCE = 1800
MAX_TASKS = 3
MAX_PROMPT_PPM = 2.50
MAX_COMPLETION_PPM = 8.00


def _write_json(path: str | Path, value: Any) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_output(name: str, value: Any) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={str(value).replace(chr(10), ' ').replace(chr(13), ' ')}\n")


def prepare(event_path: str | Path, output_dir: str | Path) -> int:
    event = base._load_json(event_path)
    issue = event.get("issue") if isinstance(event.get("issue"), Mapping) else {}
    body = str(issue.get("body") or "").strip()
    raw: Mapping[str, Any] = {}
    if body:
        parsed = json.loads(body)
        if not isinstance(parsed, Mapping):
            raise base.LiveBenchmarkError("Issue body must be one JSON object")
        raw = parsed
    allowed = {
        "benchmark_id", "max_cost_usd", "max_calls", "max_strategy_cost_usd",
        "task_ids", "output_allowance_tokens",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise base.LiveBenchmarkError(f"Unknown benchmark config fields: {unknown}")

    suite = base._load_json(base.DEFAULT_SUITE)
    available = {
        str(row.get("task_id"))
        for row in suite.get("tasks", [])
        if isinstance(row, Mapping) and row.get("task_id")
    }
    task_ids = [str(value) for value in raw.get("task_ids", DEFAULT_TASK_IDS)]
    if not task_ids or len(task_ids) > MAX_TASKS:
        raise base.LiveBenchmarkError(f"Economy benchmark requires 1 to {MAX_TASKS} tasks")
    if len(set(task_ids)) != len(task_ids) or any(value not in available for value in task_ids):
        raise base.LiveBenchmarkError("task_ids must be distinct known benchmark tasks")

    max_cost = max(0.50, min(HARD_MAX_COST_USD, float(raw.get("max_cost_usd", DEFAULT_MAX_COST_USD))))
    max_calls = max(12, min(HARD_MAX_CALLS, int(raw.get("max_calls", DEFAULT_MAX_CALLS))))
    strategy_cap = max(0.10, min(0.40, float(raw.get("max_strategy_cost_usd", DEFAULT_STRATEGY_CAP_USD))))
    allowance = max(1024, min(3000, int(raw.get("output_allowance_tokens", DEFAULT_OUTPUT_ALLOWANCE))))
    config = {
        "version": 2,
        "mode": "economy-cutover",
        "benchmark_id": str(raw.get("benchmark_id") or "v5-economy-cutover-20260730"),
        "max_cost_usd": round(max_cost, 4),
        "max_calls": max_calls,
        "max_strategy_cost_usd": round(min(strategy_cap, max_cost), 4),
        "output_allowance_tokens": allowance,
        "task_ids": task_ids,
        "strategies": ["v5_joint_graph", "v3"],
        "judge_policy": "two independent judges; third only when disagreement exceeds 15 points",
        "issue_number": int(issue.get("number") or 0),
        "production_entrypoint_changed": False,
        "v3_deleted": False,
    }
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "benchmark-config.json", config)
    for key in ("benchmark_id", "max_cost_usd", "max_calls", "output_allowance_tokens"):
        _write_output(key, config[key])
    _write_output("task_count", len(task_ids))
    return 0


def credit_preflight(config_path: str | Path, output_dir: str | Path) -> int:
    config = base._load_json(config_path)
    required = float(config.get("max_cost_usd", DEFAULT_MAX_COST_USD))
    if required > HARD_MAX_COST_USD + 1e-12:
        raise base.LiveBenchmarkError("economy benchmark reserve exceeds the hard 2 USD ceiling")
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise base.LiveBenchmarkError("OPENROUTER_API_KEY is not set")

    key_payload = hardened.request_json(hardened.CURRENT_KEY_URL, api_key, 30, 0)
    key_data = key_payload.get("data") if isinstance(key_payload.get("data"), Mapping) else {}
    limit = final._number(key_data.get("limit"))
    limit_remaining = final._number(key_data.get("limit_remaining"))
    blockers: list[str] = []
    if limit_remaining is not None and limit_remaining + 1e-12 < required:
        blockers.append("api-key-limit-remaining-below-economy-reserve")

    account_remaining: float | None = None
    account_error: str | None = None
    management_key = os.getenv("OPENROUTER_MANAGEMENT_KEY", "").strip()
    if management_key:
        try:
            payload = hardened.request_json(hardened.CREDITS_URL, management_key, 30, 0)
            data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
            total = final._number(data.get("total_credits"))
            usage = final._number(data.get("total_usage"))
            if total is not None and usage is not None:
                account_remaining = max(0.0, total - usage)
                if account_remaining + 1e-12 < required:
                    blockers.append("account-credits-below-economy-reserve")
        except Exception as exc:  # noqa: BLE001
            account_error = str(exc)

    report = {
        "version": 1,
        "mode": "economy-cutover",
        "required_reserve_usd": required,
        "hard_reserve_ceiling_usd": HARD_MAX_COST_USD,
        "runtime_global_call_ceiling": int(config.get("max_calls", DEFAULT_MAX_CALLS)),
        "output_allowance_tokens": int(config.get("output_allowance_tokens", DEFAULT_OUTPUT_ALLOWANCE)),
        "current_key": {
            "label": key_data.get("label"),
            "limit_usd": limit,
            "limit_remaining_usd": limit_remaining,
            "usage_usd": final._number(key_data.get("usage")),
            "limit_mode": "finite" if limit is not None and limit_remaining is not None else "not-reported-or-unbounded",
        },
        "account_remaining_usd": account_remaining,
        "account_check_error": account_error,
        "blockers": blockers,
        "status": "insufficient" if blockers else (
            "verified" if account_remaining is not None else "bounded-key-accepted"
        ),
        "safety_basis": [
            "hard actual-cost ceiling <= 2 USD",
            "only V5 and V3 are executed",
            "maximum three tasks",
            "affordable endpoint caps",
            "two independent judges per task; third only on disagreement",
            "first HTTP 402 stops the benchmark",
        ],
        "model_inference_calls": 0,
        "production_entrypoint_changed": False,
        "v3_deleted": False,
    }
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "credit-preflight.json", report)
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


def _judge_evidence_valid(row: Mapping[str, Any]) -> bool:
    models = {str(value) for value in row.get("blind_judge_models", []) if str(value)}
    providers = {str(value) for value in row.get("blind_judge_providers", []) if str(value)}
    return bool(
        int(row.get("blind_judge_count", 0) or 0) >= 2
        and len(models) >= 2
        and len(providers) >= 2
        and float(row.get("blind_judge_disagreement_points", 100.0) or 100.0) <= 35.0
    )


def economy_cutover_gate(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    required_strategies = {"v5_joint_graph", "v3"}
    tasks = sorted({str(row.get("task_id") or "") for row in records if row.get("task_id")})
    expected_tasks = set(tasks)
    by_strategy: dict[str, list[Mapping[str, Any]]] = {}
    for row in records:
        by_strategy.setdefault(str(row.get("strategy") or ""), []).append(row)

    blockers: list[str] = []
    if len(tasks) < 3:
        blockers.append("fewer-than-3-independent-tasks")
    missing = sorted(required_strategies - set(by_strategy))
    if missing:
        blockers.append("missing-strategies:" + ",".join(missing))

    summaries: dict[str, Any] = {}
    per_task_scores: dict[str, dict[str, float]] = {}
    for strategy in sorted(required_strategies):
        rows = by_strategy.get(strategy, [])
        unique_tasks = {str(row.get("task_id") or "") for row in rows if row.get("task_id")}
        missing_tasks = sorted(expected_tasks - unique_tasks)
        valid_rows = [
            row for row in rows
            if row.get("status") == "success"
            and not row.get("safety_failure")
            and not row.get("blind_fatal_error")
            and _judge_evidence_valid(row)
        ]
        for row in rows:
            per_task_scores.setdefault(str(row.get("task_id") or ""), {})[strategy] = float(
                row.get("blind_quality_score", 0.0)
            )
        invalid_judging = sorted({
            str(row.get("task_id") or "") for row in rows if not _judge_evidence_valid(row)
        })
        summaries[strategy] = {
            "task_count": len(rows),
            "unique_task_count": len(unique_tasks),
            "missing_tasks": missing_tasks,
            "success_rate": round(len(valid_rows) / max(1, len(expected_tasks)), 6),
            "mean_blind_quality": round(
                sum(float(row.get("blind_quality_score", 0.0)) for row in valid_rows) / max(1, len(valid_rows)), 6
            ),
            "mean_cost_usd": round(
                sum(float(row.get("actual_cost_usd", 0.0)) for row in valid_rows) / max(1, len(valid_rows)), 8
            ),
            "safety_failures": sum(bool(row.get("safety_failure")) for row in rows),
            "blind_fatal_errors": sum(bool(row.get("blind_fatal_error")) for row in rows),
            "invalid_judge_evidence_tasks": invalid_judging,
        }
        if missing_tasks:
            blockers.append(f"{strategy}:missing-tasks:" + ",".join(missing_tasks))
        if invalid_judging:
            blockers.append(f"{strategy}:invalid-independent-blind-judging")

    v5 = summaries.get("v5_joint_graph", {})
    v3 = summaries.get("v3", {})
    if v5.get("safety_failures", 1):
        blockers.append("v5-safety-failure")
    if v5.get("blind_fatal_errors", 1):
        blockers.append("v5-blind-fatal-error")
    if float(v5.get("success_rate", 0.0)) < 1.0:
        blockers.append("v5-did-not-pass-all-3-tasks")
    if float(v5.get("success_rate", 0.0)) < float(v3.get("success_rate", 1.0)):
        blockers.append("v5-success-rate-below-v3")
    if float(v5.get("mean_blind_quality", 0.0)) < float(v3.get("mean_blind_quality", 1.0)) * 1.02:
        blockers.append("v5-quality-improvement-below-2-percent")
    v3_cost = float(v3.get("mean_cost_usd", 0.0))
    if float(v5.get("mean_cost_usd", 1e9)) > max(v3_cost * 1.25, v3_cost + 0.02):
        blockers.append("v5-cost-regression-above-policy")

    task_wins = sum(
        1 for scores in per_task_scores.values()
        if scores.get("v5_joint_graph", 0.0) > scores.get("v3", 0.0)
    )
    if task_wins < 2:
        blockers.append("v5-won-fewer-than-2-of-3-tasks")

    blockers = sorted(set(blockers))
    return {
        "version": 1,
        "benchmark_type": "economy-live-blind-comparison",
        "task_ids": tasks,
        "summaries": summaries,
        "task_wins_v5": task_wins,
        "production_cutover_allowed": not blockers,
        "blockers": blockers,
        "cutover_policy": {
            "minimum_tasks": 3,
            "required_strategies": ["v5_joint_graph", "v3"],
            "minimum_independent_judges_per_result": 2,
            "third_judge_only_on_disagreement_above_points": 15,
            "minimum_v5_task_wins": 2,
            "minimum_v5_success_rate": 1.0,
            "minimum_quality_improvement_over_v3": 0.02,
            "maximum_relative_cost_regression": 0.25,
            "safety_failures_allowed": 0,
            "blind_fatal_errors_allowed": 0,
            "v3_deleted": False,
        },
    }


def run_benchmark(config_path: str | Path, suite_path: str | Path, output_dir: str | Path) -> int:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise base.LiveBenchmarkError("OPENROUTER_API_KEY is not set")
    config = base._load_json(config_path)
    suite = base._load_json(suite_path)
    requested = [str(value) for value in config.get("task_ids", [])]
    by_id = {
        str(row.get("task_id")): row
        for row in suite.get("tasks", [])
        if isinstance(row, Mapping) and row.get("task_id")
    }
    tasks = [by_id[value] for value in requested if value in by_id]
    if len(tasks) != len(requested):
        raise base.LiveBenchmarkError("one or more configured tasks are absent from the suite")

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    ledger = base.GlobalLedger(float(config["max_cost_usd"]), int(config["max_calls"]))
    strategy_cap = min(float(config["max_strategy_cost_usd"]), ledger.max_cost_usd)
    catalog_run = market.build_run_config(base._namespace(base._task_text(tasks[0]), root / "catalog", ranking_limit=50))
    models, catalog_source = market.fetch_catalog(catalog_run)
    endpoint_cache: dict[str, Mapping[str, Any]] = {}
    records: list[dict[str, Any]] = []
    task_bundles: list[dict[str, Any]] = []
    status = "success"
    error = ""

    for task in tasks:
        task_id = str(task["task_id"])
        task_root = root / "tasks" / task_id
        task_root.mkdir(parents=True, exist_ok=True)
        outcomes: list[base.StrategyOutcome] = []
        try:
            v5_outcome, market_bundle = base._v5_strategy(
                task, task_root / "v5_joint_graph", ledger, models, endpoint_cache,
                min(strategy_cap, max(0.10, ledger.remaining_cost())),
            )
            outcomes.append(v5_outcome)
            outcomes.append(base._v3_strategy(
                task, task_root / "v3", ledger,
                min(strategy_cap, max(0.10, ledger.remaining_cost())),
            ))
            direct_run = market.build_run_config(base._namespace(base._task_text(task), task_root / "judge", ranking_limit=50))
            scores, evaluation = base._evaluate_task(
                direct_run, task, outcomes, market_bundle, ledger, task_root
            )
            for outcome in outcomes:
                row = outcome.record()
                row["blind_quality_score"] = scores.get(outcome.strategy, 0.0)
                row["blind_judge_count"] = evaluation["judge_count"]
                row["blind_judge_models"] = evaluation["judge_models"]
                row["blind_judge_providers"] = evaluation["judge_providers"]
                row["blind_fatal_error"] = bool(evaluation["fatal_by_strategy"].get(outcome.strategy))
                row["blind_judge_disagreement_points"] = float(
                    evaluation["disagreement_points_by_strategy"].get(outcome.strategy, 100.0)
                )
                records.append(row)
            task_bundle = {
                "task_id": task_id,
                "domain": task.get("domain"),
                "outcomes": [outcome.record() for outcome in outcomes],
                "blind_scores": scores,
                "evaluation": evaluation,
                "ledger_after_task": ledger.snapshot(),
            }
            task_bundles.append(task_bundle)
            _write_json(task_root / "task-benchmark-result.json", task_bundle)
        except base.BenchmarkLimitExceeded as exc:
            status = "budget_or_call_limit_exceeded"
            error = str(exc)
            break
        except Exception as exc:  # noqa: BLE001
            status = "technical_failure"
            error = f"task {task_id}: {exc}"
            task_bundles.append({
                "task_id": task_id,
                "status": "technical_failure",
                "error": str(exc),
                "outcomes": [outcome.record() for outcome in outcomes],
            })
            break

    cutover = economy_cutover_gate(records)
    bundle = {
        "version": 2,
        "mode": "economy-cutover",
        "benchmark_id": config["benchmark_id"],
        "status": status,
        "error": error or None,
        "catalog_source": catalog_source,
        "tasks_requested": len(tasks),
        "tasks_completed": len({row.get("task_id") for row in records}),
        "strategies": ["v5_joint_graph", "v3"],
        "records": records,
        "task_bundles": task_bundles,
        "ledger": ledger.snapshot(),
        "cutover_gate": cutover,
        "production_entrypoint_changed": False,
        "v3_deleted": False,
        "legacy_full_benchmark_executed": False,
    }
    _write_json(root / "v5-live-benchmark-results.json", bundle)
    (root / "v5-live-benchmark-summary.md").write_text(base._summary_markdown(bundle), encoding="utf-8")
    write_manifest(root)
    return 0 if status == "success" else 2


def _install_economy_controls() -> None:
    final.install_final_alignment()
    hardened.ALLOWANCE = DEFAULT_OUTPUT_ALLOWANCE
    os.environ.setdefault("V5_BENCHMARK_OUTPUT_ALLOWANCE_TOKENS", str(DEFAULT_OUTPUT_ALLOWANCE))

    original_rank = base._rank_v5_models

    def affordable_rank(models: Mapping[str, Any], profile: Any, run: Any) -> list[Any]:
        ranked = original_rank(models, profile, run)
        filtered = [row for row in ranked if _affordable_model(row)]
        if len(filtered) < 4:
            raise base.LiveBenchmarkError("fewer than four affordable models satisfy economy price caps")
        return filtered

    base._rank_v5_models = affordable_rank

    def economy_graph_limits(**kwargs: Any) -> OriginalGraphLimits:
        kwargs["max_nodes"] = min(int(kwargs.get("max_nodes", 8)), 8)
        kwargs["max_edges"] = min(int(kwargs.get("max_edges", 32)), 32)
        kwargs["max_stages"] = min(int(kwargs.get("max_stages", 6)), 6)
        kwargs["max_model_calls"] = min(int(kwargs.get("max_model_calls", 8)), 8)
        kwargs["max_retries"] = 0
        kwargs["max_replacements"] = min(int(kwargs.get("max_replacements", 1)), 1)
        return OriginalGraphLimits(**kwargs)

    base.GraphLimits = economy_graph_limits

    original_judges = base._judge_endpoints

    def affordable_judges(market_bundle: Mapping[str, Any], used_models: set[str]) -> list[Mapping[str, Any]]:
        scoped = dict(market_bundle)
        affordable = _affordable_endpoints(market_bundle)
        scoped["endpoints"] = affordable if len({str(row.get("model_id")) for row in affordable}) >= 2 else list(
            market_bundle.get("endpoints", [])
        )
        return original_judges(scoped, used_models)

    base._judge_endpoints = affordable_judges

    original_run = base.subprocess.run

    def bounded_v3_subprocess(command: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(command, list) and len(command) > 1 and (
            str(command[1]).endswith("expert_team_hardened.py")
            or str(command[1]).endswith("v3_benchmark_entry_final.py")
        ):
            env = dict(kwargs.get("env") or os.environ)
            env["TOTAL_MODEL_CALLS"] = "4"
            env["EXPERT_MAX_REPLACEMENTS"] = "0"
            env["V5_BENCHMARK_OUTPUT_ALLOWANCE_TOKENS"] = str(DEFAULT_OUTPUT_ALLOWANCE)
            kwargs["env"] = env
        return original_run(command, *args, **kwargs)

    base.subprocess.run = bounded_v3_subprocess
    base.prepare = prepare
    base.run_benchmark = run_benchmark
    hardened.credit_preflight = credit_preflight


def main(argv: Sequence[str] | None = None) -> int:
    _install_economy_controls()
    return hardened.main(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
