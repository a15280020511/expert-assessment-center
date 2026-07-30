"""Replace the legacy fixed 4-6 call budget with a dynamic safety ceiling."""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping

import task_router
from model_market import ExpertTeamError, RunConfig

MIN_TOTAL_MODEL_CALLS = 2
MAX_TOTAL_MODEL_CALLS = 16
DEFAULT_TOTAL_MODEL_CALLS = 16


def total_model_calls_from_env(run: RunConfig, environ: Mapping[str, str]) -> int:
    del run
    raw = str(environ.get("TOTAL_MODEL_CALLS") or "").strip()
    try:
        calls = int(raw) if raw else DEFAULT_TOTAL_MODEL_CALLS
    except ValueError as exc:
        raise ExpertTeamError("TOTAL_MODEL_CALLS must be an integer.") from exc
    if not MIN_TOTAL_MODEL_CALLS <= calls <= MAX_TOTAL_MODEL_CALLS:
        raise ExpertTeamError(
            f"TOTAL_MODEL_CALLS must be between {MIN_TOTAL_MODEL_CALLS} and {MAX_TOTAL_MODEL_CALLS}."
        )
    return calls


def execution_run_after_routing(run: RunConfig, outcome, total_model_calls: int) -> RunConfig:
    replacements = max(0, min(2, total_model_calls - MIN_TOTAL_MODEL_CALLS - int(outcome.call_consumed)))
    remaining = run.max_estimated_cost_usd
    if remaining is not None:
        remaining -= outcome.budget_reservation_usd
        if remaining <= 0:
            raise ExpertTeamError("Semantic routing consumed the entire approved cost budget.")
    return replace(run, maximum_replacements=replacements, max_estimated_cost_usd=remaining)


task_router.MIN_TOTAL_MODEL_CALLS = MIN_TOTAL_MODEL_CALLS
task_router.MAX_TOTAL_MODEL_CALLS = MAX_TOTAL_MODEL_CALLS
task_router.total_model_calls_from_env = total_model_calls_from_env
task_router.execution_run_after_routing = execution_run_after_routing
