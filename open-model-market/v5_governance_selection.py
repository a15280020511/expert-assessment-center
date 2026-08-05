"""Validate an immutable expert model plan produced by the governance center.

This module performs contract and integrity checks only. It never reads a model
catalog, ranks candidates, changes a model/provider, assigns a role, or invents a
recovery route. Any missing or modified governance plan fails before model calls.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "governance-expert-model-selection-v1"
SELECTION_AUTHORITY = "decision-system-governance"
SOURCE_REPOSITORY = "a15280020511/decision-system-governance"


class GovernanceSelectionError(RuntimeError):
    """Raised when the governance-owned plan is absent, inconsistent or altered."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GovernanceSelectionError(f"{field} must be an object")
    return value


def _rows(value: Any, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise GovernanceSelectionError(f"{field} must be an array")
    rows = [row for row in value if isinstance(row, Mapping)]
    if len(rows) != len(value):
        raise GovernanceSelectionError(f"{field} contains a non-object row")
    return rows


def _company(model: str) -> str:
    text = str(model or "").strip().casefold()
    return text.split("/", 1)[0] if "/" in text else ""


def _verify_hashes(plan: Mapping[str, Any]) -> None:
    material = dict(plan)
    observed_plan = str(material.pop("plan_sha256", ""))
    if not observed_plan or observed_plan != _sha256_json(material):
        raise GovernanceSelectionError("governance plan SHA-256 mismatch")
    catalog = _mapping(plan.get("catalog"), "catalog")
    if str(plan.get("catalog_sha256") or "") != _sha256_json(catalog):
        raise GovernanceSelectionError("governance catalog SHA-256 mismatch")
    proposal = _mapping(plan.get("proposal"), "proposal")
    if str(plan.get("proposal_sha256") or "") != _sha256_json(proposal):
        raise GovernanceSelectionError("governance proposal SHA-256 mismatch")


def _verify_task(plan: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    task = str(plan.get("task_text") or "").strip()
    if not task:
        raise GovernanceSelectionError("governance task text is empty")
    digest = _sha256_text(task)
    if str(plan.get("task_sha256") or "") != digest:
        raise GovernanceSelectionError("governance task SHA-256 mismatch")
    envelope = _mapping(plan.get("task_envelope"), "task_envelope")
    if str(envelope.get("task_sha256") or "") != digest:
        raise GovernanceSelectionError("task envelope is not bound to task text")
    if envelope.get("selection_authority") != SELECTION_AUTHORITY:
        raise GovernanceSelectionError("task envelope selection authority is invalid")
    if envelope.get("decomposition_authority") != SELECTION_AUTHORITY:
        raise GovernanceSelectionError("task envelope decomposition authority is invalid")
    if int(envelope.get("required_context_tokens") or 0) <= 0:
        raise GovernanceSelectionError("task envelope context requirement is invalid")
    return task, envelope


def _catalog_index(catalog: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    index: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in _rows(catalog.get("endpoints"), "catalog.endpoints"):
        model = str(row.get("model") or "").strip()
        provider = str(row.get("provider") or "").strip()
        key = (model, provider)
        if not all(key) or key in index:
            raise GovernanceSelectionError("catalog contains an invalid or duplicate exact route")
        if str(row.get("provider_endpoint") or "") != f"{model}@{provider}":
            raise GovernanceSelectionError("catalog provider endpoint identity is invalid")
        if str(row.get("company") or "").casefold() != _company(model):
            raise GovernanceSelectionError("catalog company does not match model identity")
        if int(row.get("context_length") or 0) <= 0:
            raise GovernanceSelectionError("catalog endpoint context capacity is invalid")
        if int(row.get("max_completion_tokens") or 0) <= 0:
            raise GovernanceSelectionError("catalog endpoint completion capacity is invalid")
        index[key] = row
    if not index:
        raise GovernanceSelectionError("governance catalog is empty")
    return index


def _verify_proposal(
    plan: Mapping[str, Any],
    catalog_index: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    approved_total_calls: int,
    approved_recovery_calls: int,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    proposal = _mapping(plan.get("proposal"), "proposal")
    if proposal.get("schema_version") != "governance-owned-expert-proposal-v1":
        raise GovernanceSelectionError("unsupported governance proposal schema")
    nodes = _rows(proposal.get("nodes"), "proposal.nodes")
    work_items = _rows(proposal.get("work_items"), "proposal.work_items")
    edges = _rows(proposal.get("edges"), "proposal.edges")
    final_nodes = proposal.get("final_nodes")
    if not isinstance(final_nodes, list) or not final_nodes:
        raise GovernanceSelectionError("proposal final_nodes are missing")
    selected_count = int(plan.get("selected_expert_count") or 0)
    maximum_initial = approved_total_calls - approved_recovery_calls
    if not 3 <= selected_count <= 6 or len(nodes) != selected_count:
        raise GovernanceSelectionError("selected expert count is invalid")
    if len(nodes) > maximum_initial:
        raise GovernanceSelectionError("proposal exceeds approved initial-call capacity")
    if not work_items or len(work_items) != len(nodes):
        raise GovernanceSelectionError("proposal work and node counts disagree")
    if len(edges) > 64:
        raise GovernanceSelectionError("proposal edge count exceeds the contract")

    node_ids: set[str] = set()
    work_ids = {str(row.get("work_id") or "") for row in work_items}
    if "" in work_ids or len(work_ids) != len(work_items):
        raise GovernanceSelectionError("proposal has invalid work identifiers")
    companies: list[str] = []
    recoveries: list[Mapping[str, Any]] = []
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        model = str(node.get("model") or "")
        provider = str(node.get("provider") or "")
        assigned = node.get("work_ids")
        if not node_id or node_id in node_ids:
            raise GovernanceSelectionError("proposal has invalid node identifiers")
        node_ids.add(node_id)
        if (model, provider) not in catalog_index:
            raise GovernanceSelectionError("proposal references an unknown exact endpoint")
        if not isinstance(assigned, list) or not assigned:
            raise GovernanceSelectionError("proposal node has no assigned work")
        if any(str(value) not in work_ids for value in assigned):
            raise GovernanceSelectionError("proposal node references unknown work")
        if not str(node.get("role") or "").strip():
            raise GovernanceSelectionError("proposal node role is missing")
        if int(node.get("max_output_tokens") or 0) <= 0:
            raise GovernanceSelectionError("proposal node output allowance is invalid")
        companies.append(_company(model))
        rows = _rows(node.get("recovery", []), f"proposal.nodes[{node_id}].recovery")
        for recovery in rows:
            recovery_model = str(recovery.get("model") or "")
            recovery_provider = str(recovery.get("provider") or "")
            if (recovery_model, recovery_provider) not in catalog_index:
                raise GovernanceSelectionError("proposal recovery route is unknown")
            companies.append(_company(recovery_model))
            recoveries.append(recovery)

    if len(recoveries) != approved_recovery_calls:
        raise GovernanceSelectionError("proposal recovery count is not budget-bound")
    if any(not company for company in companies):
        raise GovernanceSelectionError("one or more model companies are unresolved")
    if len(companies) != len(set(companies)):
        raise GovernanceSelectionError("expert and recovery model companies are not unique")
    if not set(str(value) for value in final_nodes).issubset(node_ids):
        raise GovernanceSelectionError("proposal final node is unknown")
    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in node_ids or target not in node_ids or source == target:
            raise GovernanceSelectionError("proposal edge is invalid")
    return nodes, recoveries


def validate_governance_selection(
    plan: Mapping[str, Any],
    *,
    approved_total_calls: int,
    approved_recovery_calls: int,
) -> dict[str, Any]:
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise GovernanceSelectionError("unsupported governance selection schema")
    if plan.get("status") != "PASS":
        raise GovernanceSelectionError("governance selection did not pass")
    if plan.get("selection_authority") != SELECTION_AUTHORITY:
        raise GovernanceSelectionError("selection authority is not governance")
    if plan.get("source_repository") != SOURCE_REPOSITORY:
        raise GovernanceSelectionError("selection source repository is invalid")
    if int(plan.get("model_calls") or 0) != 0:
        raise GovernanceSelectionError("governance selection must use zero inference calls")
    if plan.get("expert_center_selection_allowed") is not False:
        raise GovernanceSelectionError("expert-center selection must be disabled")
    if plan.get("expert_center_catalog_fetch_allowed") is not False:
        raise GovernanceSelectionError("expert-center catalog fetching must be disabled")
    if plan.get("local_fallback_allowed") is not False:
        raise GovernanceSelectionError("local model-selection fallback must be disabled")
    if int(plan.get("approved_total_calls") or 0) != int(approved_total_calls):
        raise GovernanceSelectionError("governance plan total-call budget mismatch")
    if int(plan.get("approved_recovery_calls") or 0) != int(approved_recovery_calls):
        raise GovernanceSelectionError("governance plan recovery budget mismatch")
    _verify_hashes(plan)
    task, envelope = _verify_task(plan)
    catalog = _mapping(plan.get("catalog"), "catalog")
    if catalog.get("selection_authority") != SELECTION_AUTHORITY:
        raise GovernanceSelectionError("catalog selection authority is invalid")
    catalog_index = _catalog_index(catalog)
    nodes, recoveries = _verify_proposal(
        plan,
        catalog_index,
        approved_total_calls=int(approved_total_calls),
        approved_recovery_calls=int(approved_recovery_calls),
    )
    return {
        "schema_version": "expert-governance-selection-validation-v1",
        "status": "PASS",
        "selection_authority": SELECTION_AUTHORITY,
        "source_repository": SOURCE_REPOSITORY,
        "source_commit": str(plan.get("source_commit") or ""),
        "plan_sha256": str(plan.get("plan_sha256") or ""),
        "catalog_sha256": str(plan.get("catalog_sha256") or ""),
        "proposal_sha256": str(plan.get("proposal_sha256") or ""),
        "task_sha256": str(plan.get("task_sha256") or ""),
        "task_characters": len(task),
        "required_context_tokens": int(envelope["required_context_tokens"]),
        "selected_expert_count": len(nodes),
        "recovery_model_count": len(recoveries),
        "expert_center_selection_performed": False,
        "expert_center_catalog_fetch_performed": False,
        "local_fallback_used": False,
    }


def load_and_validate(
    path: str | Path,
    *,
    approved_total_calls: int,
    approved_recovery_calls: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GovernanceSelectionError("governance selection file is not an object")
    receipt = validate_governance_selection(
        value,
        approved_total_calls=approved_total_calls,
        approved_recovery_calls=approved_recovery_calls,
    )
    return value, receipt


__all__ = [
    "GovernanceSelectionError",
    "SCHEMA_VERSION",
    "SELECTION_AUTHORITY",
    "SOURCE_REPOSITORY",
    "load_and_validate",
    "validate_governance_selection",
]
