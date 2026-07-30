"""Provider-compatibility corrections discovered by the PR #79 Stage-D rerun.

Two output-shape failures are node/request specific and must not open a
provider-wide circuit: output truncation and invalid structured JSON. The same
run also proved that Amazon Bedrock rejects JSON Schema array `maxItems`.
V5 already validates required top-level fields after the response, so removing
array cardinality keywords preserves the delivery contract while making the
request portable across explicit Provider endpoints.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

import v5_cost_reliability_hardening as cost_hardening
import v5_r8_executor as r8_executor

_ORIGINAL_FAILURE_CLASS = r8_executor._failure_class
_ORIGINAL_STRICT_JSON_SCHEMA = cost_hardening._strict_json_schema
_INSTALLED = False

_NODE_SCOPED_FAILURES = {
    "truncated_output": "node_truncated_output",
    "invalid_json": "node_invalid_json",
}
_UNSUPPORTED_PORTABILITY_KEYWORDS = {"minItems", "maxItems"}


def node_scoped_failure_class(attempt: Any, node: Any) -> str:
    """Prevent request-size/schema failures from poisoning an endpoint circuit."""
    failure = _ORIGINAL_FAILURE_CLASS(attempt, node)
    return _NODE_SCOPED_FAILURES.get(failure, failure)


def _portable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _portable(item)
            for key, item in value.items()
            if str(key) not in _UNSUPPORTED_PORTABILITY_KEYWORDS
        }
    if isinstance(value, list):
        return [_portable(item) for item in value]
    return deepcopy(value)


def portable_strict_json_schema(node: Any) -> Mapping[str, Any] | None:
    """Return the existing strict schema without Provider-incompatible array bounds."""
    schema = _ORIGINAL_STRICT_JSON_SCHEMA(node)
    return _portable(schema) if schema is not None else None


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    r8_executor._failure_class = node_scoped_failure_class
    cost_hardening._strict_json_schema = portable_strict_json_schema
    _INSTALLED = True
