"""Build production evidence for GPT/Claude governance plus expert execution."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from artifact_manifest import write_manifest
from v5_claude_red_team_policy import CLAUDE_RED_TEAM_GOVERNANCE_CALLS

RUNTIME_VERSION = "v5-gpt-claude-runtime-1"


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _canonical_sha(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(rendered.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovedRun:
    total_calls: int
    recovery_calls: int
    cost_anomaly_usd: float | None

    @property
    def expert_total_calls(self) -> int:
        return self.total_calls - CLAUDE_RED_TEAM_GOVERNANCE_CALLS

    @property
    def expert_initial_calls(self) -> int:
        return self.expert_total_calls - self.recovery_calls

    def to_dict(self) -> dict[str, Any]:
        return {
            "maximum_total_calls": self.total_calls,
            "governance_calls_reserved": (
                CLAUDE_RED_TEAM_GOVERNANCE_CALLS
            ),
            "maximum_expert_calls": self.expert_total_calls,
            "maximum_recovery_calls": self.recovery_calls,
            "maximum_expert_initial_calls": self.expert_initial_calls,
            "cost_anomaly_usd": self.cost_anomaly_usd,
        }


@dataclass(frozen=True)
class EvidenceInputs:
    runtime_config: Mapping[str, Any]
    catalog_snapshot: Mapping[str, Any]
    execution_graph: Mapping[str, Any]
    node_results: tuple[Mapping[str, Any], ...]
    final_report: str
    execution_summary: Mapping[str, Any]
    selection: Mapping[str, Any]
    ticket: Mapping[str, Any]
    request_audit: Mapping[str, Any]
    governance_result: Mapping[str, Any]
    governance_ledger: Mapping[str, Any]

    @classmethod
    def from_directory(cls, root: Path) -> "EvidenceInputs":
        def mapping(name: str) -> Mapping[str, Any]:
            value = _load(root / name, {})
            return dict(value) if isinstance(value, Mapping) else {}

        raw_nodes = _load(root / "v5-node-results.json", [])
        nodes = tuple(
            row for row in raw_nodes if isinstance(row, Mapping)
        ) if isinstance(raw_nodes, list) else ()
        report_path = root / "v5-final-report.md"
        report = (
            report_path.read_text(encoding="utf-8")
            if report_path.is_file()
            else ""
        )
        return cls(
            runtime_config=mapping("v5-runtime-config.json"),
            catalog_snapshot=mapping("catalog-snapshot.json"),
            execution_graph=mapping("v5-execution-graph.json"),
            node_results=nodes,
            final_report=report,
            execution_summary=mapping("v5-execution-summary.json"),
            selection=mapping("v5-selection.json"),
            ticket=mapping("ticket-status.json"),
            request_audit=mapping("v5-request-audit.json"),
            governance_result=mapping("v5-governance-result.json"),
            governance_ledger=mapping("v5-governance-calls.json"),
        )


@dataclass(frozen=True)
class PreparedEvidence:
    requests: tuple[Mapping[str, Any], ...]
    request_count: int
    governance_calls: int
    expert_budget: Mapping[str, Any]
    expert_calls: int
    call_count: int
    actual_cost: float
    nodes: tuple[Mapping[str, Any], ...]
    answer: str
    governance_rows: tuple[Mapping[str, Any], ...]
    governance_models: tuple[str, ...]
    expert_models: tuple[str, ...]
    providers: tuple[str, ...]


def _mapping_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(row for row in value if isinstance(row, Mapping))


def _validated_call_context(
    inputs: EvidenceInputs,
    approved: ApprovedRun,
) -> tuple[tuple[Mapping[str, Any], ...], int, int, Mapping[str, Any], int, int]:
    requests = _mapping_rows(inputs.request_audit.get("requests", []))
    request_count = int(
        inputs.request_audit.get("request_count") or len(requests)
    )
    governance_calls = int(
        inputs.governance_ledger.get("actual_governance_calls") or 0
    )
    raw_budget = inputs.execution_summary.get("execution_budget", {})
    expert_budget = dict(raw_budget) if isinstance(raw_budget, Mapping) else {}
    expert_calls = int(expert_budget.get("calls_reserved") or 0)
    call_count = governance_calls + expert_calls
    if request_count != call_count:
        raise RuntimeError(
            "complete request audit and call ledger disagree: "
            f"requests={request_count}, ledger={call_count}"
        )
    if call_count > approved.total_calls:
        raise RuntimeError("approved total paid-call ceiling exceeded")
    if governance_calls != CLAUDE_RED_TEAM_GOVERNANCE_CALLS:
        raise RuntimeError(
            "governance must use exactly GPT proposal, Claude once, and GPT synthesis"
        )
    if int(inputs.governance_ledger.get("claude_red_team_calls") or 0) != 1:
        raise RuntimeError("Claude red-team call count must equal one")
    if int(inputs.governance_ledger.get("gpt_synthesis_calls") or 0) != 1:
        raise RuntimeError("GPT synthesis call count must equal one")
    if inputs.governance_result.get("claude_covers_internal_selection") is not True:
        raise RuntimeError("Claude evidence must cover internal expert selection")
    if inputs.governance_result.get("claude_covers_external_information") is not True:
        raise RuntimeError("Claude evidence must cover external information review")
    return (
        requests,
        request_count,
        governance_calls,
        expert_budget,
        expert_calls,
        call_count,
    )


def _validated_actual_cost(inputs: EvidenceInputs, approved: ApprovedRun) -> float:
    actual_cost = float(inputs.execution_summary.get("actual_cost_usd") or 0.0)
    if not math.isfinite(actual_cost) or actual_cost < 0:
        raise RuntimeError("actual total cost is invalid")
    if (
        approved.cost_anomaly_usd is not None
        and actual_cost > approved.cost_anomaly_usd + 1e-12
    ):
        raise RuntimeError("approved cost anomaly guard exceeded")
    return actual_cost


def _validated_nodes(
    inputs: EvidenceInputs,
    approved: ApprovedRun,
) -> tuple[Mapping[str, Any], ...]:
    nodes = _mapping_rows(inputs.execution_graph.get("nodes", []))
    if len(nodes) > approved.expert_initial_calls:
        raise RuntimeError("GPT proposal exceeds expert initial-call capacity")
    if inputs.selection.get("optimizer_used") is not False:
        raise RuntimeError("selection evidence must prove optimizer absent")
    materialization = inputs.selection.get("materialization", {})
    obsolete_keys = (
        "local_scoring_used",
        "optimizer_used",
        "cp_sat_used",
        "pareto_pruning_used",
        "heuristic_ranking_used",
    )
    if (
        isinstance(materialization, Mapping)
        and any(materialization.get(key) is not False for key in obsolete_keys)
    ):
        raise RuntimeError("obsolete selection algorithm evidence detected")
    return nodes


def _validated_answer(inputs: EvidenceInputs, require_report: bool) -> str:
    answer = str(inputs.execution_summary.get("final_answer") or "").strip()
    if require_report and (not inputs.final_report.strip() or not answer):
        raise RuntimeError("V5 did not produce a final report")
    return answer


def _models_and_providers(
    governance_rows: tuple[Mapping[str, Any], ...],
    nodes: tuple[Mapping[str, Any], ...],
    requests: tuple[Mapping[str, Any], ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    governance_models = tuple(sorted({
        str(row.get("resolved_model") or row.get("requested_model") or "")
        for row in governance_rows
        if row.get("resolved_model") or row.get("requested_model")
    }))
    expert_models = tuple(sorted({
        str(row.get("model")) for row in nodes if row.get("model")
    }))
    providers = {
        str(row.get("provider"))
        for row in governance_rows
        if row.get("provider")
    }
    for request in requests:
        provider = request.get("provider")
        if not isinstance(provider, Mapping):
            continue
        only = provider.get("only")
        if isinstance(only, list) and only:
            providers.add(str(only[0]))
    return governance_models, expert_models, tuple(sorted(value for value in providers if value))


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
    ) = _validated_call_context(inputs, approved)
    actual_cost = _validated_actual_cost(inputs, approved)
    nodes = _validated_nodes(inputs, approved)
    answer = _validated_answer(inputs, require_report)
    governance_rows = _mapping_rows(inputs.governance_ledger.get("calls", []))
    governance_models, expert_models, providers = _models_and_providers(
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


def _request_audit_document(
    inputs: EvidenceInputs,
    approved: ApprovedRun,
    prepared: PreparedEvidence,
) -> dict[str, Any]:
    document = {
        **dict(inputs.request_audit),
        "runtime_version": RUNTIME_VERSION,
        "status": (
            "PASS"
            if inputs.request_audit.get("status") == "PASS"
            and prepared.request_count == prepared.call_count
            else "FAIL"
        ),
        "approved_total_call_ceiling": approved.total_calls,
        "governance_call_count": prepared.governance_calls,
        "expert_call_count": prepared.expert_calls,
    }
    if document["status"] != "PASS":
        raise RuntimeError("complete request audit did not pass")
    return document


def _ledger_document(
    inputs: EvidenceInputs,
    approved: ApprovedRun,
    prepared: PreparedEvidence,
) -> dict[str, Any]:
    return {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "summary": {
            "call_count": prepared.call_count,
            "governance_calls": prepared.governance_calls,
            "expert_calls": prepared.expert_calls,
            "approved_total_call_ceiling": approved.total_calls,
            "approved_recovery_call_ceiling": approved.recovery_calls,
            "provider_actual_cost_usd": round(prepared.actual_cost, 8),
            "conservative_cost_usd": round(prepared.actual_cost, 8),
            "cost_anomaly_usd": approved.cost_anomaly_usd,
            "substantive_providers": list(prepared.providers),
            "substantive_provider_count": len(prepared.providers),
            "replacement_calls": int(
                prepared.expert_budget.get("replacements_reserved") or 0
            ),
            "retry_calls": int(
                prepared.expert_budget.get("retries_reserved") or 0
            ),
            "recovery_calls": int(
                prepared.expert_budget.get("recovery_calls_reserved") or 0
            ),
        },
        "governance": dict(inputs.governance_ledger),
        "node_results": list(inputs.node_results),
    }


def _selection_document(
    inputs: EvidenceInputs,
    prepared: PreparedEvidence,
) -> dict[str, Any]:
    return {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "selection_authority": "~openai/gpt-latest",
        "red_team_authority": "~anthropic/claude-opus-latest",
        "claude_red_team_calls": 1,
        "gpt_synthesis_calls": int(
            inputs.governance_ledger.get("gpt_synthesis_calls") or 0
        ),
        "expert_models": list(prepared.expert_models),
        "governance_models": list(prepared.governance_models),
        "node_count": len(prepared.nodes),
        "catalog_snapshot_id": inputs.catalog_snapshot.get("catalog_snapshot_id"),
        "local_scoring_used": False,
        "optimizer_used": False,
        "cp_sat_used": False,
        "pareto_pruning_used": False,
        "cross_task_history_used": False,
    }


def _routing_document(prepared: PreparedEvidence) -> dict[str, Any]:
    return {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS" if prepared.nodes else "FAIL",
        "mode": "gpt-direct-single-claude-red-team",
        "model_loop_allowed": False,
    }


def _execution_summary_document(
    builder: "EvidenceBundleBuilder",
    prepared: PreparedEvidence,
) -> dict[str, Any]:
    inputs = builder.inputs
    return {
        **dict(inputs.execution_summary),
        "runtime_version": RUNTIME_VERSION,
        "approved_budget": builder.approved.to_dict(),
        "catalog_snapshot_id": inputs.catalog_snapshot.get("catalog_snapshot_id"),
        "evidence_input_sha256": builder.input_sha256(),
    }


def _result_document(
    builder: "EvidenceBundleBuilder",
    prepared: PreparedEvidence,
) -> dict[str, Any]:
    inputs = builder.inputs
    return {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": str(inputs.execution_summary.get("status") or "failed"),
        "completion_mode": str(
            inputs.execution_summary.get("completion_mode") or "none"
        ),
        "quality_status": str(
            inputs.execution_summary.get("quality_status") or "failed"
        ),
        "quality_integrity": inputs.execution_summary.get("quality_integrity"),
        "final_answer": prepared.answer,
        "actual_cost_usd": round(prepared.actual_cost, 8),
        "executor": inputs.execution_summary.get("executor"),
        "work_coverage": inputs.execution_summary.get("work_coverage"),
        "degradation": inputs.execution_summary.get("degradation"),
        "execution_budget": prepared.expert_budget,
        "approved_budget": builder.approved.to_dict(),
        "governance": inputs.execution_summary.get("governance"),
        "catalog_snapshot_id": inputs.catalog_snapshot.get("catalog_snapshot_id"),
        "node_count": len(prepared.nodes),
        "model_count": len(prepared.expert_models),
        "governance_model_count": len(prepared.governance_models),
        "provider_count": len(prepared.providers),
        "production_entrypoint": True,
        "fallback_used": False,
        "legacy_runtime_present": False,
        "ticket_task_id": inputs.ticket.get("task_id"),
        "evidence_input_sha256": builder.input_sha256(),
    }


def _runtime_document(approved: ApprovedRun) -> dict[str, Any]:
    return {
        "runtime_version": RUNTIME_VERSION,
        "entrypoint": "v5_production_ticket.py",
        "architecture": (
            "gpt-latest-once -> claude-opus-latest-once -> "
            "gpt-synthesis-once -> deterministic-validator"
        ),
        **approved.to_dict(),
        "selection_authority": "gpt-direct",
        "local_planner_present": False,
        "optimizer_present": False,
        "fallback_policy": "fail-closed-no-alternate-runtime",
        "legacy_runtime_present": False,
        "cross_task_history_used": False,
    }


class EvidenceBundleBuilder:
    def __init__(
        self,
        inputs: EvidenceInputs,
        approved: ApprovedRun,
    ) -> None:
        if not 4 <= approved.total_calls <= 16:
            raise ValueError(
                "approved total calls must be between 4 and 16"
            )
        if not 0 <= approved.recovery_calls < approved.expert_total_calls:
            raise ValueError("approved recovery reserve is invalid")
        self.inputs = inputs
        self.approved = approved

    def input_sha256(self) -> str:
        return _canonical_sha({
            "runtime_config": self.inputs.runtime_config,
            "catalog_snapshot": self.inputs.catalog_snapshot,
            "execution_graph": self.inputs.execution_graph,
            "node_results": self.inputs.node_results,
            "final_report": self.inputs.final_report,
            "execution_summary": self.inputs.execution_summary,
            "selection": self.inputs.selection,
            "ticket": self.inputs.ticket,
            "request_audit": self.inputs.request_audit,
            "governance_result": self.inputs.governance_result,
            "governance_ledger": self.inputs.governance_ledger,
            "approved": self.approved.to_dict(),
        })

    def build(self, *, require_report: bool) -> dict[str, Any]:
        prepared = _prepare_evidence(
            self.inputs,
            self.approved,
            require_report=require_report,
        )
        return {
            "request-audit.json": _request_audit_document(
                self.inputs,
                self.approved,
                prepared,
            ),
            "call-ledger.json": _ledger_document(
                self.inputs,
                self.approved,
                prepared,
            ),
            "model-selection.json": _selection_document(
                self.inputs,
                prepared,
            ),
            "task-routing.json": _routing_document(prepared),
            "execution-summary.json": _execution_summary_document(self, prepared),
            "expert-team-result.json": _result_document(self, prepared),
            "production-runtime.json": _runtime_document(self.approved),
        }

    def write(
        self,
        root: Path,
        *,
        require_report: bool,
    ) -> dict[str, Any]:
        documents = self.build(require_report=require_report)
        for name, document in documents.items():
            _write(root / name, document)
        if require_report:
            (root / "expert-team-report.md").write_text(
                self.inputs.final_report,
                encoding="utf-8",
            )
        snapshot = {
            "schema_version": "v5-evidence-bundle-2",
            "runtime_version": RUNTIME_VERSION,
            "input_sha256": self.input_sha256(),
            "approved": self.approved.to_dict(),
            "catalog_snapshot_id": (
                self.inputs.catalog_snapshot.get(
                    "catalog_snapshot_id"
                )
            ),
            "generated_documents": {
                name: _canonical_sha(document)
                for name, document in sorted(documents.items())
            },
            "business_evidence_frozen": True,
            "post_upload_fields_pending": [
                "primary_artifact_id",
                "primary_artifact_digest",
                "primary_artifact_url",
            ],
        }
        _write(root / "evidence-bundle.json", snapshot)
        write_manifest(root)
        return documents["expert-team-result.json"]
