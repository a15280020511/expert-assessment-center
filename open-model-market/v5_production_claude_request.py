"""Production Claude request hardening for exact review targets.

The provider structured-output compatibility layer may remove regex keywords.
This module therefore replaces the generic target pattern with a task-specific
enum containing only review objects that exist in the validated payload. The
fixed Claude prompt, one-call policy, parser, provider lock and advisory-only
role remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from v5_claude_red_team_policy import (
    CLAUDE_RED_TEAM_PROVIDER,
    build_claude_red_team_request as _build_native_request,
)


def exact_review_targets(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the complete deterministic target set for one review payload."""
    targets = {"task", "contract"}
    work_items = payload.get("work_items")
    if isinstance(work_items, list):
        for work in work_items:
            if isinstance(work, Mapping):
                work_id = str(work.get("work_id") or "").strip()
                if work_id:
                    targets.add(f"work:{work_id}")
    nodes = payload.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if isinstance(node, Mapping):
                node_id = str(node.get("node_id") or "").strip()
                if node_id:
                    targets.add(f"node:{node_id}")
    edges = payload.get("edges")
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, Mapping):
                continue
            source = str(edge.get("source") or "").strip()
            target = str(edge.get("target") or "").strip()
            if source and target:
                targets.add(f"edge:{source}->{target}")
    return tuple(sorted(targets))


def build_claude_red_team_request(
    payload: Mapping[str, Any],
    *,
    provider_slug: str = CLAUDE_RED_TEAM_PROVIDER,
) -> dict[str, Any]:
    """Build the native request and replace removable regex with exact enum."""
    request = _build_native_request(payload, provider_slug=provider_slug)
    response_format = deepcopy(request["response_format"])
    target_schema = response_format["json_schema"]["schema"]["properties"][
        "suggestions"
    ]["items"]["properties"]["target"]
    target_schema.clear()
    target_schema.update(
        {
            "type": "string",
            "enum": list(exact_review_targets(payload)),
        }
    )
    request["response_format"] = response_format
    request["red_team_policy"] = {
        "target_constraint": "task-specific-exact-enum",
        "target_count": len(target_schema["enum"]),
        "generic_pattern_used": False,
        "parser_remains_fail_closed": True,
    }
    return request


__all__ = ["build_claude_red_team_request", "exact_review_targets"]
