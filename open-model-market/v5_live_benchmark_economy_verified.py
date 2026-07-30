#!/usr/bin/env python3
"""Evidence-aligned configuration layer for the economical live benchmark.

The core economy benchmark remains in ``v5_live_benchmark_economy``. This layer
applies only bounds proven by zero-call diagnostic run 30536650572: at most ten
V5 nodes, concrete Provider Endpoint prices within 5/15 USD per million tokens,
and a 46-call global envelope. It does not raise the 1.5 USD cost ceiling.
"""
from __future__ import annotations

import math
import sys
from typing import Any, Mapping, Sequence

import v5_live_benchmark_economy as economy
import v5_planner
import v5_value_optimizer
from execution_graph import GraphLimits as OriginalGraphLimits

VERIFIED_MAX_V5_NODES = 10
VERIFIED_MAX_JUDGES_PER_TASK = 3
VERIFIED_DEFAULT_MAX_CALLS = 46
VERIFIED_MAX_PROMPT_PPM = 5.0
VERIFIED_MAX_COMPLETION_PPM = 15.0
VERIFIED_MINIMUM_RELIABILITY = 0.80
_INSTALLED = False
_ORIGINAL_MARKET_COMPILER = v5_value_optimizer.compile_model_endpoint_market


def _within_verified_price_cap(row: Mapping[str, Any]) -> bool:
    return bool(
        float(row.get("prompt_price_per_million", math.inf))
        <= VERIFIED_MAX_PROMPT_PPM
        and float(row.get("completion_price_per_million", math.inf))
        <= VERIFIED_MAX_COMPLETION_PPM
        and float(row.get("reliability", 0.0)) >= VERIFIED_MINIMUM_RELIABILITY
        and not bool(row.get("synthetic_fixture_only"))
    )


def filter_verified_endpoint_market(
    market_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Filter concrete provider rows, never aggregate model catalog prices.

    A model can have a high catalog-level blended/default price while one of its
    real providers offers an endpoint inside the economical ceiling. The zero-call
    diagnostic evaluated those concrete endpoint rows. The paid path must use the
    same evidence and must not delete the model before endpoint compilation.
    """
    source = [
        row
        for row in market_bundle.get("endpoints", [])
        if isinstance(row, Mapping)
    ]
    kept = [dict(row) for row in source if _within_verified_price_cap(row)]
    if not kept:
        raise v5_planner.V5PlanningError(
            "No real provider endpoint satisfies the verified economy price and reliability caps."
        )

    rejected = list(market_bundle.get("rejected", []) or [])
    rejected.extend(
        {
            "model": str(row.get("model_id") or ""),
            "provider": str(row.get("provider_slug") or ""),
            "endpoint_id": str(row.get("endpoint_id") or ""),
            "reason": "outside-verified-economy-provider-endpoint-cap",
        }
        for row in source
        if not _within_verified_price_cap(row)
    )
    result = dict(market_bundle)
    result.update(
        {
            "endpoints": kept,
            "endpoint_count": len(kept),
            "real_endpoint_count": len(kept),
            "synthetic_fixture_count": 0,
            "rejected": rejected,
            "verified_economy_market_policy": {
                "scope": "concrete-provider-endpoint-not-model-catalog-aggregate",
                "prompt_usd_per_million_max": VERIFIED_MAX_PROMPT_PPM,
                "completion_usd_per_million_max": VERIFIED_MAX_COMPLETION_PPM,
                "minimum_reliability": VERIFIED_MINIMUM_RELIABILITY,
                "synthetic_endpoints_allowed": False,
                "zero_call_evidence_run": 30536650572,
            },
        }
    )
    return result


def compile_verified_endpoint_market(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Compile the normal live market, then apply the verified endpoint policy."""
    return filter_verified_endpoint_market(
        _ORIGINAL_MARKET_COMPILER(*args, **kwargs)
    )


def verified_graph_limits(**kwargs: Any) -> OriginalGraphLimits:
    """Return the zero-call-proven graph limits without mutating runtime state."""
    kwargs["max_nodes"] = min(
        int(kwargs.get("max_nodes", VERIFIED_MAX_V5_NODES)),
        VERIFIED_MAX_V5_NODES,
    )
    kwargs["max_edges"] = min(int(kwargs.get("max_edges", 40)), 40)
    kwargs["max_stages"] = min(int(kwargs.get("max_stages", 8)), 8)
    kwargs["max_model_calls"] = min(
        int(kwargs.get("max_model_calls", VERIFIED_MAX_V5_NODES)),
        VERIFIED_MAX_V5_NODES,
    )
    kwargs["max_retries"] = 0
    # The benchmark measures selected production candidates without buying
    # replacement calls. Any node failure fails the task closed.
    kwargs["max_replacements"] = 0
    return OriginalGraphLimits(**kwargs)


def install_verified_alignment() -> None:
    """Install exactly the market and resource bounds proven by zero-call evidence."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # Capture the formal task/intelligence ranker before the core economy layer
    # installs its old aggregate model-price prefilter. Endpoint affordability is
    # enforced after real Provider Endpoint compilation instead.
    endpoint_agnostic_rank = economy.base._rank_v5_models

    economy.DEFAULT_MAX_CALLS = VERIFIED_DEFAULT_MAX_CALLS
    economy.MAX_PROMPT_PPM = VERIFIED_MAX_PROMPT_PPM
    economy.MAX_COMPLETION_PPM = VERIFIED_MAX_COMPLETION_PPM
    economy._install_economy_controls()

    economy.base._rank_v5_models = endpoint_agnostic_rank
    v5_value_optimizer.compile_model_endpoint_market = (
        compile_verified_endpoint_market
    )
    economy.base.GraphLimits = verified_graph_limits

    economy_judges = economy.base._judge_endpoints

    def verified_judges(
        market_bundle: Mapping[str, Any],
        used_models: set[str],
    ) -> list[Mapping[str, Any]]:
        # The V5 market is already endpoint-filtered. Recheck judge rows as a
        # defense-in-depth invariant and buy at most the three allowed judges.
        selected = [
            row
            for row in economy_judges(market_bundle, used_models)
            if _within_verified_price_cap(row)
        ][:VERIFIED_MAX_JUDGES_PER_TASK]
        if len(selected) < 2:
            raise economy.base.LiveBenchmarkError(
                "fewer than two independent judges satisfy verified economy endpoint caps"
            )
        return selected

    economy.base._judge_endpoints = verified_judges


def main(argv: Sequence[str] | None = None) -> int:
    install_verified_alignment()
    return economy.hardened.main(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
