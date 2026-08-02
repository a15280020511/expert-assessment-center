"""Provider-compatible strict JSON Schema normalization.

OpenAI-compatible strict structured-output endpoints accept only a subset of
JSON Schema. Unsupported validation keywords are removed from the wire schema;
the existing deterministic parsers remain authoritative for all removed
bounds, patterns, and uniqueness constraints.
"""
from __future__ import annotations

import copy
from collections import Counter
from typing import Any, Mapping

UNSUPPORTED_STRICT_SCHEMA_KEYWORDS = frozenset(
    {
        "uniqueItems",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
    }
)


class StructuredOutputCompatibilityError(RuntimeError):
    """Raised when a response format cannot be normalized safely."""


def _sanitize(value: Any, removed: Counter[str]) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if name in UNSUPPORTED_STRICT_SCHEMA_KEYWORDS:
                removed[name] += 1
                continue
            result[name] = _sanitize(item, removed)
        return result
    if isinstance(value, list):
        return [_sanitize(item, removed) for item in value]
    return copy.deepcopy(value)


def normalize_strict_response_format(
    response_format: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a provider-safe response format and deterministic audit."""
    if not isinstance(response_format, Mapping):
        raise StructuredOutputCompatibilityError(
            "response_format must be an object"
        )
    if response_format.get("type") != "json_schema":
        raise StructuredOutputCompatibilityError(
            "only json_schema response formats are supported"
        )
    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, Mapping):
        raise StructuredOutputCompatibilityError(
            "response_format.json_schema must be an object"
        )
    schema = json_schema.get("schema")
    if not isinstance(schema, Mapping):
        raise StructuredOutputCompatibilityError(
            "response_format.json_schema.schema must be an object"
        )
    if json_schema.get("strict") is not True:
        raise StructuredOutputCompatibilityError(
            "strict structured output must remain enabled"
        )

    removed: Counter[str] = Counter()
    normalized = _sanitize(response_format, removed)
    normalized_schema = normalized["json_schema"]["schema"]
    if normalized_schema.get("type") != "object":
        raise StructuredOutputCompatibilityError(
            "strict response root must remain an object"
        )
    if normalized_schema.get("additionalProperties") is not False:
        raise StructuredOutputCompatibilityError(
            "strict response root must forbid additional properties"
        )
    return normalized, {
        "schema_version": "v5-structured-output-compat-1",
        "status": "PASS",
        "strict": True,
        "removed_keyword_counts": dict(sorted(removed.items())),
        "deterministic_post_parse_validation_required": True,
    }
