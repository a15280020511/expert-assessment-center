#!/usr/bin/env python3
"""No-business-gate entrypoint for the V5 dynamic expert pipeline."""
from __future__ import annotations

from typing import Any, Sequence

import v5_price_ranked_pipeline as pipeline


def _dynamic_validate_budget(args: Any) -> tuple[int, int]:
    """Treat CLI call counts as graph sizing telemetry, not admission thresholds."""
    total = int(args.maximum_total_calls)
    recovery = int(args.maximum_recovery_calls)
    if total < 1:
        total = 1
    if recovery < 0:
        recovery = 0
    if recovery >= total:
        recovery = max(0, total - 1)
    return total, recovery


def main(argv: Sequence[str] | None = None) -> int:
    # The active facade delegates to the legacy implementation for I/O and
    # evidence writing. Replace only its historical fixed 4..16/min-3 budget
    # admission function; graph/materializer/runtime limits are already dynamic.
    legacy_runtime = getattr(pipeline, "_legacy")
    setattr(legacy_runtime, "_validate_budget", _dynamic_validate_budget)
    return int(pipeline.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
