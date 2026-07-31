"""Deprecated compatibility surface for the pre-runtime V5 hardening chain.

Production, dry-run and tests construct ``ProductionRuntime`` explicitly.
Calling ``install`` is intentionally a no-op and never mutates global symbols.
"""
from __future__ import annotations

from v5_cost_reliability_hardening import (
    COST_UNCERTAINTY_MULTIPLIER,
    MIN_PROVIDER_RELIABILITY,
    conservative_estimated_cost,
    hardened_build_node_payload,
    hardened_candidate_for,
    robust_extract_answer,
)
from v5_runtime import MIN_DEGRADED_WORK_COVERAGE


def install() -> None:
    """Compatibility no-op; retained temporarily for stale external imports."""
    return None
