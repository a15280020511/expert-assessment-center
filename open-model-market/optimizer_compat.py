"""Compatibility and safety bindings for the CP-SAT selector.

This module keeps the existing runtime/test contracts intact while changing the
actual selection algorithm from greedy per-seat picking to global optimization.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

import dynamic_runtime
import seat_scoring as scoring
import team_optimizer
from model_market import ModelInfo, RunConfig, SelectedExpert, SelectedJudge, TaskProfile

_ORIGINAL_INFER_TASK_INPUT = team_optimizer.infer_task_input
_ORIGINAL_POOL_FOR_SEAT = team_optimizer._pool_for_seat
_ORIGINAL_ACTIVATE_RUNTIME = dynamic_runtime.activate_runtime


def _conservative_dynamic_input(profile: TaskProfile, run: RunConfig) -> dict[str, Any]:
    """Escalate to 4+1 only when at least two institutional-risk signals agree."""
    data = _ORIGINAL_INFER_TASK_INPUT(profile, run)
    overrides = team_optimizer._json_overrides(run.task)
    if "expert_count" not in overrides and profile.complexity == "complex":
        signals = sum((bool(profile.high_stakes), bool(profile.long_context), len(profile.domains) >= 3))
        if signals < 2:
            data["expert_count"] = 3
    return data


def _broad_global_pool(
    pool: Sequence[ModelInfo],
    seat: Any,
    limit: int,
    tier: str,
) -> list[ModelInfo]:
    """Search the full stable pool; use professional/risk fit in scoring, not as a brittle hard prefilter."""
    rows = list(pool)
    if seat.key == "red":
        risk_rows = [model for model in rows if scoring._term_fit(model, scoring.RISK_TERMS) > 0]
        if risk_rows:
            rows = risk_rows
    return scoring._ordered(rows, seat.key, seat.domain_focus, tier)[:limit]


def _activate_without_global_quorum_pollution(
    plan: dict[str, Any],
    run: Any,
    profile: Any,
    experts: Sequence[Any],
    judge: Any,
) -> None:
    """Keep dynamic runtime bindings, but enforce quorum only for a real optimized execution."""
    import expert_team

    original_recover = expert_team._recover_substantial_partials
    _ORIGINAL_ACTIVATE_RUNTIME(plan, run, profile, experts, judge)

    def execution_scoped_recover(active_run: Any, results: Sequence[Any]) -> Sequence[Any]:
        recovered = original_recover(active_run, results)
        plan_path = Path(active_run.output_dir) / "team-optimization.json"
        if not plan_path.exists():
            return recovered
        expected = int(plan.get("expert_count") or len(results))
        usable = [
            item for item in recovered
            if getattr(item, "status", "") in expert_team.USABLE_EXPERT_STATUSES
        ]
        if len(usable) != expected:
            raise expert_team.ExpertTeamError(
                f"Dynamic {expected}+1 execution requires {expected}/{expected} usable expert answers; "
                f"received {len(usable)}/{expected}."
            )
        return recovered

    expert_team._recover_substantial_partials = execution_scoped_recover


def _audit_reason(
    expert: SelectedExpert,
    model: ModelInfo,
    run: RunConfig,
    effective_pool: int,
) -> SelectedExpert:
    reason = (
        expert.selection_reason
        + "；规则顺序=稳定性与能力硬门槛→席位资格→CP-SAT全局组合效用→费用"
        + f"；候选池上限={run.candidate_pool_per_seat}"
        + f"；优化器有效候选池={effective_pool}"
        + f"；智能排名={model.ranks.get('intelligence-high-to-low')}"
    )
    return replace(expert, selection_reason=reason)


def select_team(
    ranked: Sequence[ModelInfo],
    profile: TaskProfile,
    run: RunConfig,
) -> tuple[list[SelectedExpert], SelectedJudge, float]:
    """Execute CP-SAT with conservative topology and broad feasible candidate pools."""
    original_infer = team_optimizer.infer_task_input
    original_pool = team_optimizer._pool_for_seat
    original_activate = team_optimizer.activate_runtime
    team_optimizer.infer_task_input = _conservative_dynamic_input
    team_optimizer._pool_for_seat = _broad_global_pool
    team_optimizer.activate_runtime = _activate_without_global_quorum_pollution
    try:
        experts, judge, estimated = team_optimizer.select_team(ranked, profile, run)
    finally:
        team_optimizer.infer_task_input = original_infer
        team_optimizer._pool_for_seat = original_pool
        team_optimizer.activate_runtime = original_activate
    by_id = {model.id: model for model in ranked}
    inputs = _conservative_dynamic_input(profile, run)
    annotated = [
        _audit_reason(expert, by_id[expert.model_id], run, inputs["candidate_pool_per_seat"])
        for expert in experts
    ]
    return annotated, judge, estimated
