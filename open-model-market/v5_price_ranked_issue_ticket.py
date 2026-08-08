#!/usr/bin/env python3
"""Authoritative production entry for cost-effective hierarchical expert planning.

The historical ticket parser/transport implementation remains the I/O core, but
its materialization function is explicitly rebound before any command executes.
The active optimizer performs current-task ParameterDesign, cost-effectiveness-first
OR-Tools assignment and request/resource parameter closure.  Provider routing stays
unrestricted and no expert tools are enabled.
"""
from __future__ import annotations

import v5_price_ranked_issue_ticket_core as _core
from v5_cost_effectiveness_candidate_optimizer import (
    materialize_candidate_pool_selection as _hierarchical_materialize,
)

_core.materialize_candidate_pool_selection = _hierarchical_materialize

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

HIERARCHICAL_PRODUCTION_ENTRY_ACTIVE = True
COST_EFFECTIVENESS_RESOURCE_CLOSURE_ACTIVE = True

if __name__ == "__main__":
    _arguments = _core.parser().parse_args()
    raise SystemExit(_arguments.func(_arguments))
