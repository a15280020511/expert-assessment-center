"""Ensure the final production audit retains runtime and assignment truth.

Run #387 proved that the execution engine computed Runtime Knob Coverage but a later
pipeline writer replaced ``v5-request-audit.json``. Live Governance #300 then proved
that the same final writer could also retain the historical
``selection_authority=decision-system-governance`` field after Expert Center OR-Tools
had actually assigned the current-task models. This wrapper runs after the last
ordinary request-audit writer and restores both observable truths.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import v5_price_ranked_pipeline_legacy as pipeline_legacy
import v5_quality_status_integrity as quality_integrity
from v5_dynamic_assignment_truth import (
    assignment_fields,
    expert_dynamic_assignment_active,
)
from v5_json_io import load_json_or_default, write_json


def _integrity_status(root: Path) -> str:
    for filename in ("v5-result.json", "v5-execution-summary.json"):
        value = load_json_or_default(root / filename, {})
        if not isinstance(value, Mapping):
            continue
        integrity = value.get("quality_integrity")
        if isinstance(integrity, Mapping) and str(integrity.get("status") or ""):
            return str(integrity.get("status"))
    return "UNKNOWN"


def _materialized_plan(root: Path) -> dict[str, Any]:
    value = load_json_or_default(root / "governance-model-plan.json", {})
    if isinstance(value, Mapping) and value:
        return dict(value)
    ticket = load_json_or_default(root / "ticket.json", {})
    if isinstance(ticket, Mapping) and isinstance(
        ticket.get("governance_model_plan"), Mapping
    ):
        return dict(ticket["governance_model_plan"])
    return {}


def rewrite_request_audit_assignment_truth(root: Path) -> dict[str, Any]:
    """Rewrite only assignment-authority telemetry from the materialized plan."""
    path = Path(root) / "v5-request-audit.json"
    raw = load_json_or_default(path, {})
    if not isinstance(raw, Mapping):
        raise RuntimeError("final request audit is missing")
    document = dict(raw)
    plan = _materialized_plan(Path(root))
    if plan:
        fields = assignment_fields(plan)
        document.update(fields)
        active = expert_dynamic_assignment_active(plan)
        document["assignment_truth_status"] = "PASS"
        document["assignment_truth_source"] = "materialized-governance-model-plan"
        document["candidate_pool_authority_is_governance"] = (
            document.get("candidate_pool_authority") == "decision-system-governance"
        )
        document["current_task_assignment_is_expert_owned"] = bool(
            active
            and str(document.get("selection_authority") or "").startswith(
                "expert-assessment-center"
            )
            and document.get("model_selection_performed_locally") is True
        )
        if active and not document["current_task_assignment_is_expert_owned"]:
            raise RuntimeError("final request audit lost Expert assignment authority")
    write_json(path, document)
    return document


def install_final_request_audit_hardening() -> None:
    current = pipeline_legacy._request_audit  # noqa: SLF001
    if getattr(current, "_runtime_knob_final_writer", False):
        return

    def hardened_request_audit(
        output: Path,
        *,
        approved_total_calls: int,
    ) -> None:
        current(output, approved_total_calls=approved_total_calls)
        root = Path(output)
        quality_integrity._rewrite_request_audit(  # noqa: SLF001
            root,
            _integrity_status(root),
        )
        audit = rewrite_request_audit_assignment_truth(root)
        if audit.get("runtime_knob_coverage_status") != "PASS":
            raise RuntimeError(
                "runtime knob coverage failed after final request-audit write"
            )

    setattr(hardened_request_audit, "_runtime_knob_final_writer", True)
    pipeline_legacy._request_audit = hardened_request_audit  # noqa: SLF001


__all__ = [
    "install_final_request_audit_hardening",
    "rewrite_request_audit_assignment_truth",
]
