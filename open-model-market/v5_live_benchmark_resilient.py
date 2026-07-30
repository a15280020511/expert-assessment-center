#!/usr/bin/env python3
"""R7 entry: R6 comparison controls plus the production-resilient V5 executor."""
from __future__ import annotations

import sys
from typing import Sequence

import v5_live_benchmark_economy as economy
import v5_live_benchmark_economy_r6 as r6
from v5_resilient_executor import execute_v5_graph

_INSTALLED = False


def install_resilient_alignment() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    r6.install_r6_alignment()
    economy.base.execute_v5_graph = execute_v5_graph


def main(argv: Sequence[str] | None = None) -> int:
    install_resilient_alignment()
    return economy.hardened.main(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
