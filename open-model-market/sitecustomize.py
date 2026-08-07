"""Production compatibility hook for hierarchical expert planning.

Python loads ``sitecustomize`` from the script/PYTHONPATH search path before
executing the Expert Center entrypoint.  We preserve the historical module name
used by workflows and tests, but replace only its public materialization
functions with the hierarchical planner.  This avoids duplicating admission,
transport or execution code while making the new planning order authoritative.
"""
from __future__ import annotations

import v5_top50_pool_optimizer as _compat_optimizer
from v5_hierarchical_candidate_optimizer import (
    materialize_candidate_pool_selection as _hierarchical_materialize,
)
from v5_hierarchical_candidate_optimizer import (
    materialize_top50_selection as _hierarchical_top50_alias,
)

_compat_optimizer.materialize_candidate_pool_selection = _hierarchical_materialize
_compat_optimizer.materialize_top50_selection = _hierarchical_top50_alias
_compat_optimizer.HIERARCHICAL_TASK_PARAMETER_PLANNER_ACTIVE = True
