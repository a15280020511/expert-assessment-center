#!/usr/bin/env python3
"""Production V5 dynamic entry with open model-business eligibility.

The validated pipeline core is preserved unchanged.  Before it is imported we
remove only the historical ``:batch`` routed-model rejection from the active
runtime's ignored-business-gate set.  ``:online`` remains forbidden because it
would enable external retrieval, and ``openrouter/auto`` remains forbidden
because the execution graph requires an exact model identity.
"""
from __future__ import annotations

import v5_runtime as _runtime

_runtime.ExecutionEngine._IGNORED_BUSINESS_LIMIT_CODES = set(
    _runtime.ExecutionEngine._IGNORED_BUSINESS_LIMIT_CODES
) | {"router_model"}

import v5_dynamic_pipeline_core as _core  # noqa: E402

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

ROUTED_BATCH_BUSINESS_GATE_DISABLED = True

if __name__ == "__main__":
    raise SystemExit(_core.main())
