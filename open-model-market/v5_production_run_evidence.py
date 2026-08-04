"""Production evidence builder with exact structural legacy detection."""
from __future__ import annotations

from typing import Any, Mapping

import v5_run_evidence as _base

ApprovedRun = _base.ApprovedRun
EvidenceInputs = _base.EvidenceInputs
PreparedEvidence = _base.PreparedEvidence

_OBSOLETE_FLAGS = (
    "local_scoring_used",
    "optimizer_used",
    "cp_sat_used",
    "pareto_pruning_used",
    "heuristic_ranking_used",
)


def _mapping_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    return _base._mapping_rows(value)


def _validated_nodes(
    inputs: EvidenceInputs,
    approved: ApprovedRun,
) -> tuple[Mapping[str, Any], ...]:
    nodes = _mapping_rows(inputs.execution_graph.get("nodes", []))
    if len(nodes) > approved.expert_initial_calls:
        raise RuntimeError("GPT proposal exceeds expert initial-call capacity")
    if inputs.selection.get("optimizer_used") is not False:
        raise RuntimeError("selection evidence must prove optimizer absent")

    metadata = inputs.execution_graph.get("metadata")
    if not isinstance(metadata, Mapping):
        raise RuntimeError("execution graph metadata is missing")
    for key in _OBSOLETE_FLAGS:
        if metadata.get(key) is not False:
            raise RuntimeError(f"execution graph does not prove {key}=false")

    materialization = inputs.selection.get("materialization")
    if isinstance(materialization, Mapping):
        contradictory = [
            key
            for key in _OBSOLETE_FLAGS
            if key in materialization and materialization.get(key) is not False
        ]
        if contradictory:
            raise RuntimeError(
                "obsolete selection algorithm evidence detected: "
                + ",".join(contradictory)
            )
    return nodes


def _prepare_evidence(
    inputs: EvidenceInputs,
    approved: ApprovedRun,
    *,
    require_report: bool,
) -> PreparedEvidence:
    (
        requests,
        request_count,
        governance_calls,
        expert_budget,
        expert_calls,
        call_count,
    ) = _base._validated_call_context(inputs, approved)
    actual_cost = _base._validated_actual_cost(inputs, approved)
    nodes = _validated_nodes(inputs, approved)
    answer = _base._validated_answer(inputs, require_report)
    governance_rows = _mapping_rows(inputs.governance_ledger.get("calls", []))
    governance_models, expert_models, providers = _base._models_and_providers(
        governance_rows,
        nodes,
        requests,
    )
    return PreparedEvidence(
        requests=requests,
        request_count=request_count,
        governance_calls=governance_calls,
        expert_budget=expert_budget,
        expert_calls=expert_calls,
        call_count=call_count,
        actual_cost=actual_cost,
        nodes=nodes,
        answer=answer,
        governance_rows=governance_rows,
        governance_models=governance_models,
        expert_models=expert_models,
        providers=providers,
    )


class EvidenceBundleBuilder(_base.EvidenceBundleBuilder):
    """Use exact structural flags instead of treating absent fields as legacy."""

    def build(self, *, require_report: bool) -> dict[str, Any]:
        prepared = _prepare_evidence(
            self.inputs,
            self.approved,
            require_report=require_report,
        )
        return {
            "request-audit.json": _base._request_audit_document(
                self.inputs, self.approved, prepared
            ),
            "call-ledger.json": _base._ledger_document(
                self.inputs, self.approved, prepared
            ),
            "model-selection.json": _base._selection_document(
                self.inputs, prepared
            ),
            "task-routing.json": _base._routing_document(prepared),
            "execution-summary.json": _base._execution_summary_document(
                self, prepared
            ),
            "expert-team-result.json": _base._result_document(self, prepared),
            "production-runtime.json": _base._runtime_document(self.approved),
        }


__all__ = [
    "ApprovedRun",
    "EvidenceInputs",
    "EvidenceBundleBuilder",
    "PreparedEvidence",
    "_validated_nodes",
]
