#!/usr/bin/env python3
"""Run a zero-cost full-chain shadow acceptance with explicit free endpoints.

This module exercises the production request builders, strict parsers,
GPT-Claude-GPT governance semantics, deterministic materializer, expert runtime,
evidence bundle, manifest, and independent artifact revalidation inputs.  It
never qualifies formal GPT/Claude identity and never moves a production ref.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from artifact_manifest import write_manifest
from openrouter_api import CHAT_URL, request_json
from v5_execution_primitives import actual_cost
from v5_governance_runtime import (
    run_single_pass_governance,
    write_governance_artifacts,
)
from v5_json_io import write_json
from v5_model_company import canonical_model_company
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

MODELS_URL = "https://openrouter.ai/api/v1/models"
ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{author}/{slug}/endpoints"
OUTPUT_LIMIT_PARAMETERS = frozenset({"max_tokens", "max_completion_tokens"})
STRUCTURED_OUTPUT_PARAMETERS = frozenset(
    {"response_format", "structured_outputs"}
)
REASONING_PARAMETERS = frozenset({"reasoning", "reasoning_effort"})
DEFAULT_TASK = (
    "仅依据以下题面，不得调用外部工具，不得补充外部事实："
    "A方案月费20元、月流量100GB；B方案月费30元、月流量150GB。"
    "请严格按顺序输出两个Markdown二级标题：已知事实、最终建议。"
    "最终建议必须给出唯一推荐和两条理由，明确区分事实与推断，"
    "不得新增题面外数字。"
)


class FreeShadowError(RuntimeError):
    """Fail-closed free shadow validation error."""


@dataclass(frozen=True)
class FreeEndpoint:
    model: str
    company: str
    provider: str
    context_length: int
    max_completion_tokens: int
    supported_parameters: tuple[str, ...]
    official_order: int

    @property
    def provider_endpoint(self) -> str:
        return f"{self.model}@{self.provider}"

    def to_catalog_row(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "company": self.company,
            "official_intelligence_rank": self.official_order,
            "provider": self.provider,
            "provider_endpoint": self.provider_endpoint,
            "context_length": self.context_length,
            "max_completion_tokens": self.max_completion_tokens,
            "prompt_price_per_million": 0.0,
            "completion_price_per_million": 0.0,
            "supported_parameters": list(self.supported_parameters),
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "synthetic_fixture_only": False,
        }


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "HTTP-Referer": os.getenv(
            "OPENROUTER_SITE_URL",
            "https://github.com/a15280020511/expert-assessment-center",
        ),
        "X-Title": os.getenv(
            "OPENROUTER_APP_NAME",
            "expert-center-free-shadow-acceptance",
        ),
    }


def _get_json(url: str, api_key: str, timeout: int = 60) -> Mapping[str, Any]:
    request = urllib.request.Request(url, headers=_auth_headers(api_key))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, Mapping):
        raise FreeShadowError(f"OpenRouter JSON root is not an object: {url}")
    return value


def _endpoint_url(model_id: str) -> str:
    if "/" not in model_id:
        raise FreeShadowError(f"invalid explicit free model id: {model_id}")
    author, slug = model_id.split("/", 1)
    return ENDPOINTS_URL.format(
        author=urllib.parse.quote(author, safe=""),
        slug=urllib.parse.quote(slug, safe=""),
    )


def _endpoint_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = payload.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("endpoints"), list):
        return [row for row in data["endpoints"] if isinstance(row, Mapping)]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)]
    if isinstance(payload.get("endpoints"), list):
        return [row for row in payload["endpoints"] if isinstance(row, Mapping)]
    return []


def _provider_slug(endpoint: Mapping[str, Any]) -> str:
    for key in ("tag", "provider_slug", "provider", "name", "provider_name"):
        value = str(endpoint.get(key) or "").strip()
        if value:
            return value
    return ""


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if result >= 0 else fallback


def _zero_price(value: Any) -> bool:
    return abs(_number(value, 1.0)) <= 1e-15


def _supported(model: Mapping[str, Any], endpoint: Mapping[str, Any]) -> tuple[str, ...]:
    values = endpoint.get("supported_parameters") or model.get(
        "supported_parameters"
    )
    return tuple(sorted({str(value).casefold() for value in (values or [])}))


def _explicit_free_endpoint(
    model: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    official_order: int,
) -> FreeEndpoint | None:
    model_id = str(model.get("id") or "").strip()
    if not model_id.endswith(":free") or model_id == "openrouter/free":
        return None
    pricing = endpoint.get("pricing")
    pricing = pricing if isinstance(pricing, Mapping) else {}
    model_pricing = model.get("pricing")
    model_pricing = model_pricing if isinstance(model_pricing, Mapping) else {}
    prompt_price = pricing.get("prompt", model_pricing.get("prompt"))
    completion_price = pricing.get(
        "completion", model_pricing.get("completion")
    )
    if not _zero_price(prompt_price) or not _zero_price(completion_price):
        return None
    provider = _provider_slug(endpoint)
    if not provider:
        return None
    context_length = int(
        endpoint.get("context_length")
        or model.get("context_length")
        or 0
    )
    top = model.get("top_provider")
    top = top if isinstance(top, Mapping) else {}
    maximum_output = int(
        endpoint.get("max_completion_tokens")
        or top.get("max_completion_tokens")
        or 0
    )
    supported = _supported(model, endpoint)
    if not OUTPUT_LIMIT_PARAMETERS.intersection(supported):
        return None
    return FreeEndpoint(
        model=model_id,
        company=canonical_model_company(model_id),
        provider=provider,
        context_length=context_length,
        max_completion_tokens=maximum_output,
        supported_parameters=supported,
        official_order=max(1, int(official_order)),
    )


def discover_free_endpoints(
    api_key: str,
    *,
    minimum_context_tokens: int,
    minimum_completion_tokens: int,
    maximum_models_inspected: int = 40,
) -> list[FreeEndpoint]:
    payload = _get_json(MODELS_URL, api_key)
    data = payload.get("data")
    if not isinstance(data, list):
        raise FreeShadowError("OpenRouter model catalog has no data array")
    free_models = [
        row
        for row in data
        if isinstance(row, Mapping)
        and str(row.get("id") or "").endswith(":free")
        and str(row.get("id") or "") != "openrouter/free"
    ]
    candidates: list[FreeEndpoint] = []
    for index, model in enumerate(free_models[: max(1, maximum_models_inspected)]):
        model_id = str(model.get("id") or "")
        try:
            endpoint_payload = _get_json(_endpoint_url(model_id), api_key)
        except Exception:  # one stale free listing cannot poison all candidates
            continue
        for endpoint in _endpoint_rows(endpoint_payload):
            candidate = _explicit_free_endpoint(model, endpoint, index + 1)
            if candidate is None:
                continue
            if candidate.context_length < minimum_context_tokens:
                continue
            if candidate.max_completion_tokens < minimum_completion_tokens:
                continue
            candidates.append(candidate)
    unique: dict[tuple[str, str], FreeEndpoint] = {}
    for row in candidates:
        unique.setdefault((row.model, row.provider.casefold()), row)
    result = list(unique.values())
    result.sort(
        key=lambda row: (
            -int(STRUCTURED_OUTPUT_PARAMETERS.intersection(row.supported_parameters) != set()),
            -int(REASONING_PARAMETERS.intersection(row.supported_parameters) != set()),
            -row.max_completion_tokens,
            -row.context_length,
            row.official_order,
            row.model,
            row.provider.casefold(),
        )
    )
    if not result:
        raise FreeShadowError("no live explicit zero-price endpoint is usable")
    return result


def _has_governance_capabilities(endpoint: FreeEndpoint) -> bool:
    supported = set(endpoint.supported_parameters)
    return bool(
        STRUCTURED_OUTPUT_PARAMETERS.intersection(supported)
        and REASONING_PARAMETERS.intersection(supported)
        and OUTPUT_LIMIT_PARAMETERS.intersection(supported)
    )


def choose_shadow_roles(
    endpoints: Sequence[FreeEndpoint],
) -> tuple[FreeEndpoint, FreeEndpoint, list[FreeEndpoint]]:
    governance = [row for row in endpoints if _has_governance_capabilities(row)]
    proposal = next(iter(governance), None)
    if proposal is None:
        raise FreeShadowError("no free endpoint supports governance JSON protocol")
    red_team = next(
        (row for row in governance if row.company != proposal.company),
        None,
    )
    if red_team is None:
        raise FreeShadowError("free governance requires two distinct model companies")
    experts = [
        row
        for row in endpoints
        if row.company
        not in {
            proposal.company,
            red_team.company,
            "openai",
            "anthropic",
            "unknown",
        }
    ]
    distinct_experts: list[FreeEndpoint] = []
    seen_companies: set[str] = set()
    for row in experts:
        if row.company in seen_companies:
            continue
        distinct_experts.append(row)
        seen_companies.add(row.company)
    if not distinct_experts:
        raise FreeShadowError(
            "free shadow requires a third distinct company for expert execution"
        )
    return proposal, red_team, distinct_experts[:6]


def governance_resolution(
    proposal: FreeEndpoint,
    red_team: FreeEndpoint,
    *,
    required_context_tokens: int,
    minimum_completion_tokens: int,
) -> dict[str, Any]:
    def role(logical: str, row: FreeEndpoint) -> dict[str, Any]:
        return {
            "logical_model": logical,
            "resolved_model": row.model,
            "company": row.company,
            "provider": row.provider,
            "official_intelligence_rank": row.official_order,
            "context_length": row.context_length,
            "max_completion_tokens": row.max_completion_tokens,
            "supported_parameters": list(row.supported_parameters),
            "temperature_supported": "temperature" in row.supported_parameters,
            "provider_fallback_allowed": False,
            "synthetic_fixture_only": False,
            "free_shadow_only": True,
        }

    return {
        "schema_version": "v5-free-shadow-governance-model-resolution-1",
        "status": "PASS",
        "selection_basis": "live-explicit-zero-price-endpoints",
        "required_context_tokens": required_context_tokens,
        "minimum_completion_tokens": minimum_completion_tokens,
        "provider_fallback_allowed": False,
        "formal_model_identity_qualified": False,
        "gpt": role("~shadow/free-proposal", proposal),
        "claude": role("~shadow/free-red-team", red_team),
    }


def expert_catalog(
    experts: Sequence[FreeEndpoint],
    *,
    required_context_tokens: int,
) -> dict[str, Any]:
    return {
        "schema_version": "v5-free-shadow-expert-catalog-1",
        "selection_authority": "production-gpt-protocol-with-free-shadow-model",
        "official_order_only": True,
        "local_score_computed": False,
        "optimizer_used": False,
        "pareto_pruning_used": False,
        "heuristic_ranking_used": False,
        "governance_companies_excluded": True,
        "required_context_tokens": required_context_tokens,
        "minimum_completion_tokens": 256,
        "endpoints": [row.to_catalog_row() for row in experts],
        "rejected": [],
    }


class FreeCallBoundary:
    """One shared zero-cost boundary for three governance calls and one expert."""

    def __init__(self, maximum_calls: int = 4) -> None:
        self.maximum_calls = int(maximum_calls)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        run: Any,
        request: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], float]:
        if len(self.calls) >= self.maximum_calls:
            raise FreeShadowError("free shadow call ceiling exceeded")
        payload = dict(request)
        requested_model = str(payload.get("model") or "")
        if not requested_model.endswith(":free"):
            raise FreeShadowError(
                f"non-free model reached free boundary: {requested_model}"
            )
        provider = payload.get("provider")
        provider = dict(provider) if isinstance(provider, Mapping) else {}
        only = provider.get("only")
        if not isinstance(only, list) or len(only) != 1:
            raise FreeShadowError("free shadow request lacks one exact provider lock")
        expected_provider = str(only[0]).strip()
        if not expected_provider:
            raise FreeShadowError("free shadow provider lock is empty")
        if provider.get("allow_fallbacks") is not False:
            raise FreeShadowError("free shadow provider fallback is forbidden")
        provider.update({"data_collection": "allow", "zdr": False})
        payload["provider"] = provider
        started = time.monotonic()
        response = request_json(
            CHAT_URL,
            str(getattr(run, "api_key", "")),
            int(getattr(run, "model_timeout_seconds", 180)),
            0,
            payload,
        )
        latency = time.monotonic() - started
        response_model = str(response.get("model") or "")
        response_provider = str(response.get("provider") or "")
        cost = float(actual_cost(response))
        if response_model != requested_model:
            raise FreeShadowError(
                f"free shadow model mismatch: {requested_model}/{response_model}"
            )
        if response_provider.casefold() != expected_provider.casefold():
            raise FreeShadowError(
                "free shadow provider mismatch: "
                f"{expected_provider}/{response_provider}"
            )
        if abs(cost) > 1e-12:
            raise FreeShadowError(f"free shadow returned positive cost: {cost}")
        self.calls.append(
            {
                "sequence": len(self.calls) + 1,
                "model": response_model,
                "company": canonical_model_company(response_model),
                "provider": response_provider,
                "actual_cost_usd": cost,
                "response_id": str(response.get("id") or "") or None,
                "usage": dict(response.get("usage") or {})
                if isinstance(response.get("usage"), Mapping)
                else {},
            }
        )
        return response, latency

    def receipt(self) -> dict[str, Any]:
        return {
            "schema_version": "v5-free-shadow-call-boundary-1",
            "status": "PASS" if len(self.calls) == self.maximum_calls else "FAIL",
            "maximum_calls": self.maximum_calls,
            "actual_calls": len(self.calls),
            "paid_model_calls": 0,
            "paid_fallback_allowed": False,
            "actual_cost_usd": round(
                sum(float(row["actual_cost_usd"]) for row in self.calls), 12
            ),
            "calls": list(self.calls),
        }


def _write_shadow_identity(
    root: Path,
    proposal: FreeEndpoint,
    red_team: FreeEndpoint,
    experts: Sequence[FreeEndpoint],
) -> None:
    write_json(
        root / "free-shadow-identity.json",
        {
            "schema_version": "v5-free-shadow-identity-1",
            "status": "PASS",
            "proposal_and_synthesis": asdict(proposal),
            "red_team_advice_once": asdict(red_team),
            "expert_candidates": [asdict(row) for row in experts],
            "formal_gpt_identity_qualified": False,
            "formal_claude_identity_qualified": False,
            "formal_model_identity_qualified": False,
            "merge_authorized": False,
            "production_promotion_authorized": False,
            "production_ref_moved": False,
        },
    )


def execute_shadow(
    *,
    api_key: str,
    task: str,
    output_dir: Path,
    maximum_completion_tokens: int,
) -> dict[str, Any]:
    if not api_key or len(api_key) < 20:
        raise FreeShadowError("OPENROUTER_API_KEY is missing or malformed")
    output_dir.mkdir(parents=True, exist_ok=True)
    envelope = build_task_envelope(
        task,
        minimum_context_length=16_384,
        maximum_completion_tokens=maximum_completion_tokens,
    )
    required_context = int(envelope["required_context_tokens"])
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
    catalog = expert_catalog(experts, required_context_tokens=required_context)
    task_digest = sha256(task.encode("utf-8")).hexdigest()
    run = SimpleNamespace(
        api_key=api_key,
        model_timeout_seconds=180,
        parallel_workers=1,
        max_completion_tokens=maximum_completion_tokens,
    )
    boundary = FreeCallBoundary(maximum_calls=4)
    cost_cap = 0.000001

    write_json(
        output_dir / "ticket.json",
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
    write_json(output_dir / "v5-task-envelope.json", envelope)
    write_json(output_dir / "task-constraints.json", envelope["task_constraints"])
    write_json(output_dir / "v5-governance-models.json", resolution)
    write_json(output_dir / "v5-gpt-catalog-view.json", catalog)
    write_json(
        output_dir / "catalog-snapshot.json",
        {
            "schema_version": "v5-free-shadow-catalog-snapshot-1",
            "catalog_snapshot_id": "free-shadow-" + task_digest[:20],
            "catalog_sha256": sha256(
                json.dumps(
                    catalog,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "catalog_source": "openrouter-live-free-model-catalog",
            "endpoint_source": "openrouter-live-explicit-free-endpoints",
            "catalog": catalog,
            "local_task_classification_used": False,
            "local_atomic_work_generation_used": False,
            "local_resource_matrix_used": False,
            "local_scoring_used": False,
            "optimizer_used": False,
            "cross_task_history_used": False,
        },
    )
    write_json(
        output_dir / "planning-task.json",
        {
            "schema_version": "v5-free-shadow-planning-task-1",
            "source": "ticket.task",
            "sha256": task_digest,
            "characters": len(task),
            "task_constraints": envelope["task_constraints"],
            "selection_authority": "free-shadow-production-protocol",
            "red_team_model": red_team.model,
            "claude_red_team_calls": 1,
            "claude_is_advisory_only": True,
            "claude_gatekeeping_allowed": False,
            "gpt_synthesis_calls": 1,
            "local_scoring_used": False,
            "optimizer_used": False,
            "formal_model_identity_qualified": False,
        },
    )
    write_json(
        output_dir / "production-runtime.json",
        {
            "runtime_version": "v5-free-shadow-runtime-1",
            "entrypoint": "v5_free_shadow_acceptance.py",
            "pipeline": "production-builders-parsers-materializer-runtime",
            "architecture": "free-proposal -> free-red-team-once -> free-synthesis -> deterministic-validator -> free-expert",
            "maximum_total_calls": 4,
            "governance_calls_reserved": 3,
            "maximum_expert_calls": 1,
            "maximum_recovery_calls": 0,
            "cost_anomaly_usd": cost_cap,
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
            "formal_model_identity_qualified": False,
            "production_ref_moved": False,
        },
    )
    _write_shadow_identity(output_dir, proposal, red_team, experts)

    graph, limits, governance, governance_ledger = run_single_pass_governance(
        run=run,
        task=task,
        task_digest=task_digest,
        task_envelope=envelope,
        catalog=catalog,
        approved_total_calls=4,
        governance_calls_reserved=3,
        approved_recovery_calls=0,
        cost_anomaly_usd=cost_cap,
        max_completion_tokens=maximum_completion_tokens,
        governance_models=resolution,
        call_fn=boundary,
    )
    write_governance_artifacts(output_dir, governance, governance_ledger)
    _merge_request_audit(output_dir, governance_ledger, approved_total_calls=4)
    write_json(
        output_dir / "v5-selection.json",
        _selection_payload(
            governance,
            resolution,
            governance["materialization"],
        ),
    )
    write_json(output_dir / "v5-execution-graph.json", graph.to_dict())

    runtime = build_production_runtime(
        RuntimeConfig(
            total_call_limit=1,
            recovery_call_limit=0,
            cost_anomaly_usd=cost_cap,
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
        output_dir=output_dir,
        limits=limits,
    )
    args = SimpleNamespace(cost_anomaly_usd=cost_cap)
    _finalize_result(
        result,
        args=args,
        total_calls=4,
        governance_models=resolution,
        governance_ledger=governance_ledger,
        governance_cost=0.0,
    )
    _write_final_artifacts(output_dir, result, governance_ledger, 4)
    write_json(output_dir / "free-shadow-call-boundary.json", boundary.receipt())

    normalized = EvidenceBundleBuilder(
        EvidenceInputs.from_directory(output_dir),
        ApprovedRun(
            total_calls=4,
            recovery_calls=0,
            cost_anomaly_usd=cost_cap,
        ),
    ).write(output_dir, require_report=True)
    if normalized.get("status") != "success":
        raise FreeShadowError("free shadow evidence normalization failed")
    receipt = {
        "schema_version": "v5-free-shadow-acceptance-result-1",
        "status": "PASS",
        "target_sha": os.getenv("GITHUB_SHA"),
        "workflow_run_id": os.getenv("GITHUB_RUN_ID"),
        "governance_sequence": [
            row.get("kind") for row in governance_ledger.get("calls", [])
        ],
        "total_model_calls": int(result.get("total_model_calls") or 0),
        "successful_free_model_calls": len(boundary.calls),
        "paid_model_calls": 0,
        "actual_cost_usd": float(result.get("actual_cost_usd") or 0.0),
        "completion_mode": result.get("completion_mode"),
        "quality_status": result.get("quality_status"),
        "formal_model_identity_qualified": False,
        "production_promotion_authorized": False,
        "production_ref_moved": False,
        "independent_revalidation_pending": True,
    }
    if receipt["governance_sequence"] != [
        "gpt_proposal",
        "claude_red_team",
        "gpt_synthesis",
    ]:
        raise FreeShadowError("free shadow governance sequence is invalid")
    if receipt["total_model_calls"] != 4 or len(boundary.calls) != 4:
        raise FreeShadowError("free shadow must complete exactly four calls")
    if abs(receipt["actual_cost_usd"]) > 1e-12:
        raise FreeShadowError("free shadow total cost is not zero")
    if result.get("status") != "success":
        raise FreeShadowError("free shadow expert execution did not succeed")
    write_json(output_dir / "free-shadow-acceptance-receipt.json", receipt)
    write_manifest(output_dir)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--output-dir", default="free-shadow-artifacts")
    parser.add_argument("--max-completion-tokens", type=int, default=2048)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = execute_shadow(
            api_key=os.getenv("OPENROUTER_API_KEY", ""),
            task=str(args.task).strip(),
            output_dir=Path(args.output_dir),
            maximum_completion_tokens=int(args.max_completion_tokens),
        )
    except Exception as exc:  # noqa: BLE001
        root = Path(args.output_dir)
        root.mkdir(parents=True, exist_ok=True)
        failure = {
            "schema_version": "v5-free-shadow-acceptance-result-1",
            "status": "FAIL",
            "target_sha": os.getenv("GITHUB_SHA"),
            "workflow_run_id": os.getenv("GITHUB_RUN_ID"),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "formal_model_identity_qualified": False,
            "production_promotion_authorized": False,
            "production_ref_moved": False,
        }
        write_json(root / "free-shadow-acceptance-receipt.json", failure)
        print(json.dumps(failure, ensure_ascii=False))
        return 1
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
