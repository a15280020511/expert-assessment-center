"""Production facade for the fixed GPT selector with finite recovery scaling.

The native selector remains the source of the fixed prompts, schema, catalogue
projection and structural parser. This facade removes one accidental local
restriction: a node may carry up to the system-wide finite recovery maximum of
four candidates, so an approved reserve of three or four does not force GPT to
create artificial extra expert nodes merely to fit a per-node limit of two.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping

import v5_gpt_expert_selector as _native

for _name in dir(_native):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_native, _name)

MAXIMUM_RECOVERY_CANDIDATES_PER_NODE = 4
_NATIVE_RECOVERY_LIMIT = int(_native.GPT_MAX_RECOVERY_PER_NODE)


def _expanded_response_format(request: Mapping[str, Any]) -> dict[str, Any]:
    expanded = deepcopy(dict(request))
    try:
        recovery = expanded["response_format"]["json_schema"]["schema"][
            "properties"
        ]["nodes"]["items"]["properties"]["recovery"]
    except (KeyError, TypeError) as exc:
        raise _native.GPTSelectorError(
            "GPT selector response schema recovery contract is missing"
        ) from exc
    if not isinstance(recovery, dict):
        raise _native.GPTSelectorError(
            "GPT selector recovery schema is invalid"
        )
    recovery["maxItems"] = MAXIMUM_RECOVERY_CANDIDATES_PER_NODE
    governance = expanded.get("governance_policy")
    governance = dict(governance) if isinstance(governance, Mapping) else {}
    governance.update(
        {
            "maximum_recovery_candidates_per_node": (
                MAXIMUM_RECOVERY_CANDIDATES_PER_NODE
            ),
            "recovery_limit_is_finite_operational_safety": True,
            "artificial_expert_nodes_required_to_distribute_recovery": False,
        }
    )
    expanded["governance_policy"] = governance
    return expanded


def build_proposal_request(**kwargs: Any) -> dict[str, Any]:
    return _expanded_response_format(
        _native.build_proposal_request(**kwargs)
    )


def build_synthesis_request(**kwargs: Any) -> dict[str, Any]:
    return _expanded_response_format(
        _native.build_synthesis_request(**kwargs)
    )


def _validate_extra_recovery_rows(rows: list[Any]) -> None:
    if len(rows) > MAXIMUM_RECOVERY_CANDIDATES_PER_NODE:
        raise _native.GPTSelectorError("node recovery is invalid")
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"model", "provider"}:
            raise _native.GPTSelectorError("recovery row has invalid fields")
        _native._identifier(
            row["model"],
            "recovery model",
            _native.GPT_IDENTIFIER_PATTERN,
        )
        _native._identifier(
            row["provider"],
            "recovery provider",
            _native.GPT_IDENTIFIER_PATTERN,
        )


def parse_proposal(text: str) -> dict[str, Any]:
    """Validate native structure while accepting up to four finite candidates."""
    if not isinstance(text, str) or not text.strip():
        raise _native.GPTSelectorError("GPT proposal is empty")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _native.GPTSelectorError("GPT proposal is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise _native.GPTSelectorError("GPT proposal root must be an object")

    full = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    validation_copy = deepcopy(full)
    nodes = validation_copy.get("nodes")
    full_nodes = full.get("nodes")
    if not isinstance(nodes, list) or not isinstance(full_nodes, list):
        return _native.parse_proposal(text)

    for index, (node, full_node) in enumerate(
        zip(nodes, full_nodes, strict=True)
    ):
        if not isinstance(node, Mapping) or not isinstance(full_node, Mapping):
            continue
        rows = full_node.get("recovery")
        if not isinstance(rows, list):
            continue
        _validate_extra_recovery_rows(rows)
        node = dict(node)
        node["recovery"] = rows[:_NATIVE_RECOVERY_LIMIT]
        nodes[index] = node

    parsed = _native.parse_proposal(
        _native._canonical_json(validation_copy)
    )
    parsed_nodes = parsed.get("nodes", [])
    for index, full_node in enumerate(full_nodes):
        if not isinstance(full_node, Mapping):
            continue
        rows = full_node.get("recovery")
        if isinstance(rows, list) and index < len(parsed_nodes):
            parsed_nodes[index]["recovery"] = json.loads(
                json.dumps(rows, ensure_ascii=False, allow_nan=False)
            )
    return json.loads(json.dumps(parsed, ensure_ascii=False, allow_nan=False))


__all__ = [
    "MAXIMUM_RECOVERY_CANDIDATES_PER_NODE",
    "build_proposal_request",
    "build_synthesis_request",
    "parse_proposal",
    "proposal_sha256",
    "governance_prompt_catalog",
]
