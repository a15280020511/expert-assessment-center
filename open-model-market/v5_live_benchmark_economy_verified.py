#!/usr/bin/env python3
"""Evidence-aligned configuration layer for the economical live benchmark.

The core economy benchmark remains in ``v5_live_benchmark_economy``. This small
layer applies only the bounds proven necessary by zero-call diagnostic run
30536650572: at most ten V5 nodes, bounded-capability endpoint prices, and a
46-call global envelope. It does not raise the 1.5 USD cost ceiling.
"""
from __future__ import annotations

import math
import sys
from typing import Any, Mapping, Sequence

import v5_live_benchmark_economy as economy
from execution_graph import GraphLimits as OriginalGraphLimits

VERIFIED_MAX_V5_NODES = 10
VERIFIED_MAX_JUDGES_PER_TASK = 3
VERIFIED_DEFAULT_MAX_CALLS = 46
VERIFIED_MAX_PROMPT_PPM = 5.0
VERIFIED_MAX_COMPLETION_PPM = 15.0
_INSTALLED = False


def _within_verified_price_cap(row: Mapping[str, Any]) -> bool:
    return bool(
        float(row.get("prompt_price_per_million", math.inf))
        <= VERIFIED_MAX_PROMPT_PPM
        and float(row.get("completion_price_per_million", math.inf))
        <= VERIFIED_MAX_COMPLETION_PPM
        and float(row.get("reliability", 0.0)) >= 0.80
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
    # The benchmark measures the selected production candidates without buying
    # replacement calls. Any node failure fails the task closed.
    kwargs["max_replacements"] = 0
    return OriginalGraphLimits(**kwargs)


def install_verified_alignment() -> None:
    """Install only the zero-call-proven economical bounds."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # These globals are read by prepare(), the ranked-model filter, endpoint
    # filter, preflight evidence, and result annotations in the core module.
    economy.DEFAULT_MAX_CALLS = VERIFIED_DEFAULT_MAX_CALLS
    economy.MAX_PROMPT_PPM = VERIFIED_MAX_PROMPT_PPM
    economy.MAX_COMPLETION_PPM = VERIFIED_MAX_COMPLETION_PPM
    economy._install_economy_controls()
    economy.base.GraphLimits = verified_graph_limits

    economy_judges = economy.base._judge_endpoints

    def verified_judges(
        market_bundle: Mapping[str, Any],
        used_models: set[str],
    ) -> list[Mapping[str, Any]]:
        selected = [
            row
            for row in economy_judges(market_bundle, used_models)
            if _within_verified_price_cap(row)
        ][:VERIFIED_MAX_JUDGES_PER_TASK]
        if len(selected) < 2:
            raise economy.base.LiveBenchmarkError(
                "fewer than two independent judges satisfy verified economy price caps"
            )
        return selected

    economy.base._judge_endpoints = verified_judges


def main(argv: Sequence[str] | None = None) -> int:
    install_verified_alignment()
    return economy.hardened.main(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
