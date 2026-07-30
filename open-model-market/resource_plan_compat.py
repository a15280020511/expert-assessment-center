"""Feasibility guards for dynamic resource planning without restoring fixed modes."""
from __future__ import annotations

import copy
from typing import Any, Mapping

import resource_plan_optimizer as optimizer
import resource_requirements as requirements

_ORIGINAL_PARSE = requirements.parse_constraints
_ORIGINAL_GENERATE = optimizer.generate_packages


def _task_input_has_provider_rule(task: str) -> bool:
    match = optimizer.legacy.INPUT_RE.search(task)
    if not match:
        return False
    try:
        import json
        value = json.loads(match.group(1))
    except (ValueError, TypeError):
        return False
    return isinstance(value, dict) and "strict_provider_diversity" in value


def adaptive_constraints(run: Any) -> dict[str, Any]:
    """Default to preferred diversity; explicit true remains a hard constraint."""
    data = _ORIGINAL_PARSE(run)
    explicit = _task_input_has_provider_rule(str(run.task))
    if not explicit:
        data["strict_provider_diversity"] = False
        data["provider_diversity_mode"] = "prefer"
    else:
        data["provider_diversity_mode"] = "hard" if data["strict_provider_diversity"] else "off"
    return data


def packages_with_independent_replicas(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Create distinct executable replicas when one atomic unit needs two independent views."""
    rows = _ORIGINAL_GENERATE(plan)
    by_singleton = {
        tuple(row.get("unit_ids") or []): row
        for row in rows
        if len(row.get("unit_ids") or []) == 1
    }
    additions: list[dict[str, Any]] = []
    for unit_id, copies in (plan.get("coverage_requirements") or {}).items():
        base = by_singleton.get((unit_id,))
        if base is None:
            continue
        for index in range(2, int(copies) + 1):
            replica = copy.deepcopy(base)
            replica["id"] = requirements.digest("replica", [unit_id, index])
            replica["function"] = f"{base['function']}·独立复核{index}"
            replica["mission"] = f"{base['mission']}；必须独立形成第二意见，不读取同类工作包结论。"
            replica["independence_group"] = f"replica:{unit_id}"
            additions.append(replica)
    combined = rows + additions
    combined.sort(key=lambda row: (-float(row.get("importance_mass") or 0), len(row.get("unit_ids") or []), str(row.get("id") or "")))
    return combined


requirements.parse_constraints = adaptive_constraints
optimizer.generate_packages = packages_with_independent_replicas
