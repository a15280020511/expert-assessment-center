"""Compatibility facade for top-50 optimization and top-20 rollback."""
from __future__ import annotations

from typing import Any, Mapping

import v5_top50_pool_optimizer_legacy as _legacy
from v5_top20_pool_selector import Top20PoolSelectionError

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

Top50PoolOptimizationError = _legacy.Top50PoolOptimizationError
materialize_top50_selection = _legacy.materialize_top50_selection


def materialize_candidate_pool_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prefer top-50; normalize legacy top-20 failures to one admission error."""
    plan = packet.get("governance_model_plan")
    if isinstance(plan, Mapping) and plan.get("top50_reasoning_pool_size") == 50:
        return materialize_top50_selection(packet)
    try:
        return _legacy.materialize_top20_selection(packet)
    except Top20PoolSelectionError as exc:
        raise Top50PoolOptimizationError(
            f"legacy top-20 rollback selection failed: {exc}"
        ) from exc


__all__ = [
    "Top50PoolOptimizationError",
    "materialize_candidate_pool_selection",
    "materialize_top50_selection",
]
