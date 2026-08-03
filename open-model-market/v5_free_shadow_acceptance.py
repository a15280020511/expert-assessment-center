#!/usr/bin/env python3
"""Run a zero-cost full-chain shadow acceptance with explicit free endpoints."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from artifact_manifest import write_manifest
from v5_free_shadow_compat import (
    CompatibilityFreeCallBoundary,
    compatibility_governance_resolution,
    discover_account_zdr_free_endpoint,
)
from v5_free_shadow_support import (
    FreeCallBoundary,
    FreeEndpoint,
    FreeShadowError,
    choose_shadow_roles,
    discover_free_endpoints,
    expert_catalog,
    governance_resolution,
)
from v5_governance_runtime import (
    run_single_pass_governance,
    write_governance_artifacts,
)
from v5_json_io import write_json
from v5_pipeline import (
    _finalize_result,
    _merge_request_audit,
    _selection_payload,
    _write_final_artifacts,
)
from v5_recovery_runtime import build_production_runtime
from v5_run_evidence import ApprovedRun, EvidenceBundleBuilder, EvidenceInputs
from v5_runtime import RuntimeConfig
from v5_task_envelope import build_task_envelope

DEFAULT_TASK = (
    "仅依据以下题面，不得调用外部工具，不得补充外部事实："
    "A方案月费20元、月流量100GB；B方案月费30元、月流量150GB。"
    "请严格按顺序输出两个Markdown二级标题：已知事实、最终建议。"
    "最终建议必须给出唯一推荐和两条理由，明确区分事实与推断，"
    "不得新增题面外数字。"
)
COST_CAP_USD = 0.000001
STRICT_MODE = "strict-multi-company"
COMPATIBILITY_MODE = "single-endpoint-zdr-compatibility"
SUPPORTED_MODES = {"auto", STRICT_MODE, COMPATIBILITY_MODE}


def _write_shadow_identity(
    root: Path,
    proposal: FreeEndpoint,
    red_team: FreeEndpoint,
    experts: Sequence[FreeEndpoint],
    mode: str,
) -> None:
    strict = mode == STRICT_MODE
    write_json(
        root / "free-shadow-identity.json",
        {
            "schema_version": "v5-free-shadow-identity-2",
            "status": "PASS",
            "mode": mode,
            "proposal_and_synthesis": asdict(proposal),
            "red_team_advice_once": asdict(red_team),
            "expert_candidates": [asdict(row) for row in experts],
            "governance_company_diversity_qualified": strict,
            "wire_parity_qualified": strict,
            "expert_company_uniqueness_required": True,
            "formal_gpt_identity_qualified": False,
            "formal_claude_identity_qualified": False,
            "formal_model_identity_qualified": False,
            "merge_authorized": False,
            "production_promotion_authorized": False,
            "production_ref_moved": False,
        },
    )


def _catalog_digest(catalog: Mapping[str, Any]) -> str:
    value = json.dumps(
        catalog,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(value.encode("utf-8")).hexdigest()


def _write_task_artifacts(
    root: Path,
    task: str,
    envelope: Mapping[str, Any],
) -> str:
    digest = sha256(task.encode("utf-8")).hexdigest()
    write_json(
        root / "ticket.json",
        {
            "schema_version": "v5-free-shadow-ticket-1",
            "task": {
                "question": task,
                "requirements": [
                    "禁止外部工具与外部事实",
                    "严格满足用户交付合同",
                    "免费模型仅作为影子替身",
                ],
                "language": "zh-CN",
            },
        },
    )
    write_json(root / "v5-task-envelope.json", envelope)
    write_json(root / "task-constraints.json", envelope["task_constraints"])
    return digest


def _write_catalog_artifacts(
    root: Path,
    catalog: Mapping[str, Any],
    resolution: Mapping[str, Any],
    task_digest: str,
    mode: str,
) -> None:
    write_json(root / "v5-governance-models.json", resolution)
    write_json(root / "v5-gpt-catalog-view.json", catalog)
    endpoint_source = (
        "openrouter-account-filtered-zero-price-zdr-endpoints"
        if mode == COMPATIBILITY_MODE
        else "openrouter-live-explicit-free-endpoints"
    )
    write_json(
        root / "catalog-snapshot.json",
        {
            "schema_version": "v5-free-shadow-catalog-snapshot-2",
            "catalog_snapshot_id": "free-shadow-" + task_digest[:20],
            "catalog_sha256": _catalog_digest(catalog),
            "catalog_source": "openrouter-live-free-model-catalog",
            "endpoint_source": endpoint_source,
            "shadow_mode": mode,
            "catalog": catalog,
            "local_task_classification_used": False,
            "local_atomic_work_generation_used": False,
            "local_resource_matrix_used": False,
            "local_scoring_used": False,
            "optimizer_used": False,
            "cross_task_history_used": False,
        },
    )


def _write_runtime_artifacts(
    root: Path,
    task: str,
    task_digest: str,
    envelope: Mapping[str, Any],
    red_team: FreeEndpoint,
    maximum_completion_tokens: int,
    mode: str,
) -> None:
    strict = mode == STRICT_MODE
    write_json(
        root / "planning-task.json",
        {
            "schema_version": "v5-free-shadow-planning-task-2",
            "source": "ticket.task",
            "sha256": task_digest,
            "characters": len(task),
            "task_constraints": envelope["task_constraints"],
            "selection_authority": "free-shadow-production-protocol",
            "shadow_mode": mode,
            "red_team_model": red_team.model,
            "claude_red_team_calls": 1,
            "claude_is_advisory_only": True,
            "claude_gatekeeping_allowed": False,
            "gpt_synthesis_calls": 1,
            "local_scoring_used": False,
            "optimizer_used": False,
            "governance_company_diversity_qualified": strict,
            "wire_parity_qualified": strict,
            "formal_model_identity_qualified": False,
        },
    )
    write_json(
        root / "production-runtime.json",
        {
            "runtime_version": "v5-free-shadow-runtime-2",
            "entrypoint": "v5_free_shadow_acceptance.py",
            "pipeline": "production-builders-parsers-materializer-runtime",
            "architecture": (
                "free-proposal -> free-red-team-once -> free-synthesis -> "
                "deterministic-validator -> free-expert"
            ),
            "shadow_mode": mode,
            "maximum_total_calls": 4,
            "governance_calls_reserved": 3,
            "maximum_expert_calls": 1,
            "maximum_recovery_calls": 0,
            "cost_anomaly_usd": COST_CAP_USD,
            "max_completion_tokens": maximum_completion_tokens,
            "claude_is_advisory_only": True,
            "claude_gatekeeping_allowed": False,
            "deterministic_validator_is_only_hard_gate": True,
            "local_planner_present": False,
            "optimizer_present": False,
            "cp_sat_present": False,
            "pareto_pruning_present": False,
            "model_loop_allowed": False,
            "fallback_policy": "fail-closed-no-paid-fallback",
            "cross_task_history_used": False,
            "governance_company_diversity_qualified": strict,
            "wire_parity_qualified": strict,
            "formal_model_identity_qualified": False,
            "production_ref_moved": False,
        },
    )


def _strict_selection(
    api_key: str,
    required_context: int,
    maximum_completion_tokens: int,
) -> tuple[
    FreeEndpoint,
    FreeEndpoint,
    list[FreeEndpoint],
    Mapping[str, Any],
    dict[str, Any],
]:
    endpoints = discover_free_endpoints(
        api_key,
        minimum_context_tokens=required_context,
        minimum_completion_tokens=maximum_completion_tokens,
    )
    proposal, red_team, experts = choose_shadow_roles(endpoints)
    resolution = governance_resolution(
        proposal,
        red_team,
        required_context_tokens=required_context,
        minimum_completion_tokens=maximum_completion_tokens,
    )
    catalog = expert_catalog(
        experts,
        required_context_tokens=required_context,
    )
    catalog["shadow_mode"] = STRICT_MODE
    return proposal, red_team, experts, resolution, catalog


def _compatibility_selection(
    api_key: str,
    required_context: int,
    maximum_completion_tokens: int,
) -> tuple[
    FreeEndpoint,
    FreeEndpoint,
    list[FreeEndpoint],
    Mapping[str, Any],
    dict[str, Any],
]:
    endpoint = discover_account_zdr_free_endpoint(
        api_key,
        minimum_context_tokens=required_context,
        minimum_completion_tokens=maximum_completion_tokens,
    )
    resolution = compatibility_governance_resolution(
        endpoint,
        required_context_tokens=required_context,
        minimum_completion_tokens=maximum_completion_tokens,
    )
    catalog = expert_catalog(
        [endpoint],
        required_context_tokens=required_context,
    )
    catalog.update(
        {
            "shadow_mode": COMPATIBILITY_MODE,
            "governance_companies_excluded": False,
            "expert_company_uniqueness_required": True,
            "single_expert_node_ceiling": True,
            "wire_adapter_required": True,
        }
    )
    return endpoint, endpoint, [endpoint], resolution, catalog


def _select_shadow(
    api_key: str,
    required_context: int,
    maximum_completion_tokens: int,
    requested_mode: str,
) -> tuple[
    str,
    FreeEndpoint,
    FreeEndpoint,
    list[FreeEndpoint],
    Mapping[str, Any],
    dict[str, Any],
]:
    if requested_mode not in SUPPORTED_MODES:
        raise FreeShadowError(f"unsupported free shadow mode: {requested_mode}")
    if requested_mode == STRICT_MODE:
        selected = _strict_selection(
            api_key,
            required_context,
            maximum_completion_tokens,
        )
        return (STRICT_MODE, *selected)
    if requested_mode == COMPATIBILITY_MODE:
        selected = _compatibility_selection(
            api_key,
            required_context,
            maximum_completion_tokens,
        )
        return (COMPATIBILITY_MODE, *selected)
    try:
        selected = _strict_selection(
            api_key,
            required_context,
            maximum_completion_tokens,
        )
        return (STRICT_MODE, *selected)
    except FreeShadowError:
        selected = _compatibility_selection(
            api_key,
            required_context,
            maximum_completion_tokens,
        )
        return (COMPATIBILITY_MODE, *selected)


def _prepare_shadow(
    api_key: str,
    task: str,
    root: Path,
    maximum_completion_tokens: int,
    requested_mode: str,
) -> tuple[
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Any],
    str,
    str,
    FreeEndpoint,
]:
    envelope = build_task_envelope(
        task,
        minimum_context_length=16_384,
        maximum_completion_tokens=maximum_completion_tokens,
    )
    required_context = int(envelope["required_context_tokens"])
    mode, proposal, red_team, experts, resolution, catalog = _select_shadow(
        api_key,
        required_context,
        maximum_completion_tokens,
        requested_mode,
    )
    task_digest = _write_task_artifacts(root, task, envelope)
    _write_catalog_artifacts(
        root,
        catalog,
        resolution,
        task_digest,
        mode,
    )
    _write_runtime_artifacts(
        root,
        task,
        task_digest,
        envelope,
        red_team,
        maximum_completion_tokens,
        mode,
    )
    _write_shadow_identity(root, proposal, red_team, experts, mode)
    return envelope, resolution, catalog, task_digest, mode, proposal


def _run_governance_stage(
    root: Path,
    run: Any,
    task: str,
    task_digest: str,
    envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    resolution: Mapping[str, Any],
    boundary: Any,
    maximum_completion_tokens: int,
) -> tuple[Any, Any, Mapping[str, Any], Mapping[str, Any]]:
    graph, limits, governance, ledger = run_single_pass_governance(
        run=run,
        task=task,
        task_digest=task_digest,
        task_envelope=envelope,
        catalog=catalog,
        approved_total_calls=4,
        governance_calls_reserved=3,
        approved_recovery_calls=0,
        cost_anomaly_usd=COST_CAP_USD,
        max_completion_tokens=maximum_completion_tokens,
        governance_models=resolution,
        call_fn=boundary,
    )
    write_governance_artifacts(root, governance, ledger)
    _merge_request_audit(root, ledger, approved_total_calls=4)
    write_json(
        root / "v5-selection.json",
        _selection_payload(
            governance,
            resolution,
            governance["materialization"],
        ),
    )
    write_json(root / "v5-execution-graph.json", graph.to_dict())
    return graph, limits, governance, ledger


def _run_expert_stage(
    root: Path,
    run: Any,
    task: str,
    graph: Any,
    limits: Any,
    resolution: Mapping[str, Any],
    ledger: Mapping[str, Any],
    boundary: Any,
) -> dict[str, Any]:
    runtime = build_production_runtime(
        RuntimeConfig(
            total_call_limit=1,
            recovery_call_limit=0,
            cost_anomaly_usd=COST_CAP_USD,
            tools_allowed=False,
            live_catalog_required=True,
            provider_lock_required=True,
        )
    )
    result = runtime.execute_graph(
        graph,
        run,
        task,
        call_fn=boundary,
        output_dir=root,
        limits=limits,
    )
    _finalize_result(
        result,
        args=SimpleNamespace(cost_anomaly_usd=COST_CAP_USD),
        total_calls=4,
        governance_models=resolution,
        governance_ledger=ledger,
        governance_cost=0.0,
    )
    _write_final_artifacts(root, result, ledger, 4)
    write_json(root / "free-shadow-call-boundary.json", boundary.receipt())
    return result


def _normalize_and_receipt(
    root: Path,
    result: Mapping[str, Any],
    governance_ledger: Mapping[str, Any],
    boundary: Any,
    mode: str,
) -> dict[str, Any]:
    normalized = EvidenceBundleBuilder(
        EvidenceInputs.from_directory(root),
        ApprovedRun(
            total_calls=4,
            recovery_calls=0,
            cost_anomaly_usd=COST_CAP_USD,
        ),
    ).write(root, require_report=True)
    if normalized.get("status") != "success":
        raise FreeShadowError("free shadow evidence normalization failed")
    strict = mode == STRICT_MODE
    receipt = {
        "schema_version": "v5-free-shadow-acceptance-result-2",
        "status": "PASS",
        "target_sha": os.getenv("TARGET_SHA") or os.getenv("GITHUB_SHA"),
        "workflow_run_id": os.getenv("GITHUB_RUN_ID"),
        "shadow_mode": mode,
        "governance_sequence": [
            row.get("kind") for row in governance_ledger.get("calls", [])
        ],
        "total_model_calls": int(result.get("total_model_calls") or 0),
        "successful_free_model_calls": len(boundary.calls),
        "paid_model_calls": 0,
        "actual_cost_usd": float(result.get("actual_cost_usd") or 0.0),
        "completion_mode": result.get("completion_mode"),
        "quality_status": result.get("quality_status"),
        "expert_company_uniqueness_required": True,
        "governance_company_diversity_qualified": strict,
        "wire_parity_qualified": strict,
        "formal_model_identity_qualified": False,
        "production_promotion_authorized": False,
        "production_ref_moved": False,
        "independent_revalidation_pending": True,
    }
    _validate_receipt(receipt, result, boundary)
    write_json(root / "free-shadow-acceptance-receipt.json", receipt)
    write_manifest(root)
    return receipt


def _validate_receipt(
    receipt: Mapping[str, Any],
    result: Mapping[str, Any],
    boundary: Any,
) -> None:
    if receipt["governance_sequence"] != [
        "gpt_proposal",
        "claude_red_team",
        "gpt_synthesis",
    ]:
        raise FreeShadowError("free shadow governance sequence is invalid")
    if receipt["total_model_calls"] != 4 or len(boundary.calls) != 4:
        raise FreeShadowError("free shadow must complete exactly four calls")
    if abs(float(receipt["actual_cost_usd"])) > 1e-12:
        raise FreeShadowError("free shadow total cost is not zero")
    if result.get("status") != "success":
        raise FreeShadowError("free shadow expert execution did not succeed")


def execute_shadow(
    *,
    api_key: str,
    task: str,
    output_dir: Path,
    maximum_completion_tokens: int,
    mode: str,
) -> dict[str, Any]:
    if not api_key or len(api_key) < 20:
        raise FreeShadowError("OPENROUTER_API_KEY is missing or malformed")
    output_dir.mkdir(parents=True, exist_ok=True)
    (
        envelope,
        resolution,
        catalog,
        task_digest,
        selected_mode,
        compatibility_endpoint,
    ) = _prepare_shadow(
        api_key,
        task,
        output_dir,
        maximum_completion_tokens,
        mode,
    )
    run = SimpleNamespace(
        api_key=api_key,
        model_timeout_seconds=180,
        parallel_workers=1,
        max_completion_tokens=maximum_completion_tokens,
    )
    if selected_mode == STRICT_MODE:
        boundary: Any = FreeCallBoundary(maximum_calls=4)
    else:
        boundary = CompatibilityFreeCallBoundary(
            compatibility_endpoint,
            maximum_calls=4,
        )
    try:
        graph, limits, _, ledger = _run_governance_stage(
            output_dir,
            run,
            task,
            task_digest,
            envelope,
            catalog,
            resolution,
            boundary,
            maximum_completion_tokens,
        )
        result = _run_expert_stage(
            output_dir,
            run,
            task,
            graph,
            limits,
            resolution,
            ledger,
            boundary,
        )
        return _normalize_and_receipt(
            output_dir,
            result,
            ledger,
            boundary,
            selected_mode,
        )
    except Exception:
        write_json(
            output_dir / "free-shadow-call-boundary.json",
            boundary.receipt(),
        )
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--output-dir", default="free-shadow-artifacts")
    parser.add_argument("--max-completion-tokens", type=int, default=2048)
    parser.add_argument("--mode", choices=sorted(SUPPORTED_MODES), default="auto")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.output_dir)
    try:
        receipt = execute_shadow(
            api_key=os.getenv("OPENROUTER_API_KEY", ""),
            task=str(args.task).strip(),
            output_dir=root,
            maximum_completion_tokens=int(args.max_completion_tokens),
            mode=str(args.mode),
        )
    except Exception as exc:  # noqa: BLE001
        root.mkdir(parents=True, exist_ok=True)
        failure = {
            "schema_version": "v5-free-shadow-acceptance-result-2",
            "status": "FAIL",
            "target_sha": os.getenv("TARGET_SHA") or os.getenv("GITHUB_SHA"),
            "workflow_run_id": os.getenv("GITHUB_RUN_ID"),
            "requested_shadow_mode": str(args.mode),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "formal_model_identity_qualified": False,
            "production_promotion_authorized": False,
            "production_ref_moved": False,
        }
        write_json(root / "free-shadow-acceptance-receipt.json", failure)
        write_manifest(root)
        print(json.dumps(failure, ensure_ascii=False))
        return 1
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
