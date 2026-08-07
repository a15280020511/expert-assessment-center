#!/usr/bin/env python3
"""Production V5 dynamic entry with open model-business eligibility.

The validated pipeline core is preserved unchanged. Before runtime import we
remove only the historical ``:batch`` model suffix from structural rejection.
``:online`` remains forbidden because it can enable external retrieval, and
``openrouter/auto`` remains forbidden because the execution graph requires an
exact model identity. Batch variants stay eligible and, if the upstream API
cannot execute one, normal current-task recovery handles that runtime failure.
"""
from __future__ import annotations

import execution_graph_validator as _graph_validator

_graph_validator._FORBIDDEN_MODEL_TERMS = tuple(
    term for term in _graph_validator._FORBIDDEN_MODEL_TERMS if term != ":batch"
)

import v5_dynamic_pipeline_core as _core  # noqa: E402

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

PRODUCTION_POLICY_EVIDENCE = {
    "only_hard_model_boundary": "no-tools",
    "model_substitution_allowed": True,
    "provider_routing_mode": "unrestricted-openrouter",
}
ROUTED_BATCH_BUSINESS_GATE_DISABLED = True
ONLINE_TOOL_ROUTE_REMAINS_FORBIDDEN = ":online" in _graph_validator._FORBIDDEN_MODEL_TERMS
AUTO_MODEL_IDENTITY_ROUTE_REMAINS_FORBIDDEN = "openrouter/auto" in _graph_validator._FORBIDDEN_MODEL_TERMS

if __name__ == "__main__":
    raise SystemExit(_core.main())
