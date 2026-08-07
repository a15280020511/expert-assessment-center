#!/usr/bin/env python3
"""Authoritative production entry for hierarchical expert ticket planning.

The historical ticket parser/transport implementation remains the I/O core, but
its materialization function is explicitly rebound to the hierarchical planner
before any command executes.  This avoids relying on Python sitecustomize startup
semantics in GitHub Actions.
"""
from __future__ import annotations

import v5_price_ranked_issue_ticket_core as _core
from v5_hierarchical_candidate_optimizer import (
    materialize_candidate_pool_selection as _hierarchical_materialize,
)

_core.materialize_candidate_pool_selection = _hierarchical_materialize

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

HIERARCHICAL_PRODUCTION_ENTRY_ACTIVE = True

if __name__ == "__main__":
    _arguments = _core.parser().parse_args()
    raise SystemExit(_arguments.func(_arguments))
