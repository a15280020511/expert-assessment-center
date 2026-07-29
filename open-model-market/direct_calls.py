"""Secure direct-model runtime, evidence capture, and budget enforcement."""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from artifact_manifest import write_manifest
from model_market import (
    ExpertTeamError,
    ModelInfo,
    RunConfig,
    SelectedExpert,
    SelectedJudge,
    TaskProfile,
    estimate_call_cost,
    ranking_rows,
)
from openrouter_api import CHAT_URL, OpenRouterRequestError, request_json
from performance_history import record
from reasoning_policy import apply_plan, expert_inference_plan, judge_inference_plan
from response_audit import diagnostics, extract_answer, sanitized
from seat_scoring import replacement_candidates, top_candidates_for_evidence

FORBIDDEN_REQUEST_FIELDS = {"tools", "tool_choice", "plugins", "web_search_options", "file_search", "models"}


@dataclass
class ExpertResult:
    seat_key: str
    function: str
    profession: str
    requested_model: str
    resolved_model: Optional[str]
    provider: Optional[str]
    status: str
    answer: Optional[str]
    response_id: Optional[str]
    finish_reason: Optional[str]
    native_finish_reason: Optional[str]
    usage: Dict[str, Any]
    estimated_cost: float
    latency_seconds: float
    attempts: List[Dict[str, Any]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actual_cost(usage: Mapping[str, Any]) -> float:
    for key in ("cost", "total_cost"):
        try:
            if usage.get(key) is not None:
                return float(usage[key])
        except (TypeError, ValueError):
            pass
    return 0.0


def _assert_no_external_tools(payload: Dict[str, Any]) -> None:
    present = sorted(FORBIDDEN_REQUEST_FIELDS.intersection(payload))
    if present:
        raise ExpertTeamError(f"Forbidden external-tool fields in model request: {present}")
    model = str(payload.get("model") or "")
    if model.startswith("openrouter/") or ":online" in model:
        raise ExpertTeamError(f"Router/online model is forbidden: {model}")


def build_expert_payload(run: RunConfig, profile: TaskProfile, expert: SelectedExpert, model: ModelInfo) -> Dict[str, Any]:
    plan = expert_inference_plan(run, profile, expert, model)
    system = (
        "你是固定三席专家团中的独立成员。"
        f"固定席位：{expert.function}。本次动态职业：{expert.profession}。"
        f"聚焦领域：{expert.domain_focus}。本次职责：{expert.mission}。"
        "禁止调用、请求或假装使用任何外部工具，包括网页搜索、插件、文件读取、代码执行、数据库、API、浏览器和其他模型。"
        "只能依据用户任务中实际提供的文字分析；不得声称访问未提供的证据。"
        "必须输出完整可交付的最终正文，不得只输出思考过程或空白内容。"
        "输出中文，明确区分事实、假设、推断、不确定性和需要补充的数据。"
        "不要代替其他席位，也不要自行增加专家。"
    )
    payload: Dict[str, Any] = {
        "model": model.id,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": run.task}],
        "stream": False,
        "provider": run.provider,
    }
    apply_plan(payload, plan, model)
    _assert_no_external_tools(payload)
    return payload


def build_judge_payload(
    run: RunConfig,
    profile: TaskProfile,
    judge: SelectedJudge,
    judge_model: ModelInfo,
    results: Sequence[ExpertResult],
) -> Dict[str, Any]:
    blocks: List[str] = []
    remaining = run.judge_context_budget_chars
    for result in results:
        answer = result.answer or "[专家调用失败]"
        budget = max(1000, remaining // max(1, len(results) - len(blocks)))
        blocks.append(
            f"\n### {result.function}｜职业={result.profession}｜requested={result.requested_model}｜resolved={result.resolved_model}｜status={result.status}\n"
            + answer[:budget]
        )
        remaining -= len(blocks[-1])
        if remaining <= 0:
            break
    plan = judge_inference_plan(run, profile, judge, judge_model)
    system = (
        f"你是固定三席一裁结构中的{judge.function}。本次动态职业：{judge.profession}。"
        "禁止调用、请求或假装使用任何外部工具、网页搜索、插件、文件、代码执行、API或其他模型。"
        "只比较输入中三名专家的独立结论并形成最终报告。"
        "必须输出完整可交付的最终报告，不得只返回推理过程或空白内容。"
        "必须区分共识、分歧、关键假设、证据缺口、风险、推荐方案、否决条件和置信度。"
        "不得因多数一致就自动判定正确；输出中文Markdown。"
    )
    user = (
        f"固定组合：三名专家＋一名裁判\n任务画像：{json.dumps(asdict(profile), ensure_ascii=False)}\n\n"
        f"原始任务：\n{run.task}\n\n专家回答：\n{''.join(blocks)}"
    )
    payload: Dict[str, Any] = {
        "model": judge_model.id,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "provider": run.provider,
    }
    apply_plan(payload, plan, judge_model)
    _assert_no_external_tools(payload)
    return payload


def call_model(run: RunConfig, payload: Dict[str, Any]) -> tuple[Dict[str, Any], float]:
    if not run.api_key:
        raise ExpertTeamError("OPENROUTER_API_KEY is not set.")
    started = time.monotonic()
    try:
        response = request_json(
            CHAT_URL,
            run.api_key,
            run.model_timeout_seconds,
            run.model_max_retries,
            payload,
        )
    except OpenRouterRequestError as exc:
        raise ExpertTeamError(str(exc)) from exc
    return response, time.monotonic() - started


def _record_history(run: RunConfig, model_id: str, estimated: float, response: Optional[Mapping[str, Any]], latency: float, error: Optional[str]) -> None:
    info = diagnostics(response or {})
    record(
        run.history_path,
        model_id=model_id,
        success=bool(response) and not error and info.get("finish_reason") != "length" and info.get("content_present"),
        latency_seconds=latency,
        actual_cost=float(info.get("cost") or 0),
        estimated_cost=estimated,
        finish_reason=info.get("finish_reason"),
        error=error,
        reasoning_tokens=info.get("reasoning_tokens"),
        completion_tokens=info.get("completion_tokens"),
    )


def _attempt_expert(run: RunConfig, profile: TaskProfile, expert: SelectedExpert, model: ModelInfo) -> ExpertResult:
    payload = build_expert_payload(run, profile, expert, model)
    plan = expert_inference_plan(run, profile, expert, model)
    estimated = estimate_call_cost(model, len(run.task) + 1200, plan.max_tokens)
    attempt: Dict[str, Any] = {"model": model.id, "payload": payload, "estimated_cost": estimated}
    response: Optional[Dict[str, Any]] = None
    latency = 0.0
    error: Optional[str] = None
    try:
        response, latency = call_model(run, payload)
        info = diagnostics(response)
        attempt.update({"response_diagnostics": info, "latency_seconds": round(latency, 6)})
        answer = extract_answer(response)
        status = "success_complete" if info.get("finish_reason") != "length" else "success_partial"
        if status != "success_complete":
            error = "Model answer was truncated with finish_reason=length."
            raise ExpertTeamError(error)
        result = ExpertResult(
            expert.seat_key,
            expert.function,
            expert.profession,
            expert.model_id,
            str(response.get("model") or model.id),
            response.get("provider"),
            status,
            answer,
            response.get("id"),
            info.get("finish_reason"),
            info.get("native_finish_reason"),
            dict(response.get("usage") or {}),
            estimated,
            latency,
            [attempt],
        )
        _record_history(run, model.id, estimated, response, latency, None)
        return result
    except Exception as exc:  # noqa: BLE001 - converted into audited result
        error = str(exc)
        attempt["error"] = error
        if response is not None:
            attempt["sanitized_response"] = sanitized(response)
        _record_history(run, model.id, estimated, response, latency, error)
        info = diagnostics(response or {})
        return ExpertResult(
            expert.seat_key,
            expert.function,
            expert.profession,
            expert.model_id,
            str(response.get("model") or model.id) if response else None,
            response.get("provider") if response else None,
            "failed",
            None,
            response.get("id") if response else None,
            info.get("finish_reason"),
            info.get("native_finish_reason"),
            dict((response or {}).get("usage") or {}),
            estimated,
            latency,
            [attempt],
        )


def _result_cost(result: ExpertResult) -> float:
    attempt_costs = []
    for attempt in result.attempts:
        info = attempt.get("response_diagnostics") if isinstance(attempt, dict) else None
        if isinstance(info, dict) and info.get("cost") is not None:
            try:
                attempt_costs.append(float(info["cost"]))
            except (TypeError, ValueError):
                pass
    return sum(attempt_costs) if attempt_costs else _actual_cost(result.usage)


def _spent(results: Sequence[ExpertResult]) -> float:
    return sum(_result_cost(result) for result in results)


def execute_experts(run: RunConfig, profile: TaskProfile, ranked: Sequence[ModelInfo], experts: Sequence[SelectedExpert]) -> List[ExpertResult]:
    by_id = {model.id: model for model in ranked}
    with ThreadPoolExecutor(max_workers=min(run.parallel_workers, len(experts))) as pool:
        future_map = {pool.submit(_attempt_expert, run, profile, expert, by_id[expert.model_id]): expert for expert in experts}
        results = [future.result() for future in as_completed(future_map)]
    order = {expert.seat_key: index for index, expert in enumerate(experts)}
    results.sort(key=lambda item: order[item.seat_key])

    used_ids = {expert.model_id for expert in experts}
    used_authors = {by_id[model_id].author for model_id in used_ids}
    replacements = 0
    expert_by_seat = {expert.seat_key: expert for expert in experts}
    for result in results:
        if result.status == "success_complete":
            continue
        source_expert = expert_by_seat[result.seat_key]
        for candidate in replacement_candidates(ranked, profile, source_expert, used_ids, used_authors):
            if replacements >= run.maximum_replacements:
                break
            if run.max_estimated_cost_usd is not None:
                plan = expert_inference_plan(run, profile, source_expert, candidate)
                candidate_estimate = estimate_call_cost(candidate, len(run.task) + 1200, plan.max_tokens)
                if (_spent(results) + candidate_estimate) * run.budget_safety_factor > run.max_estimated_cost_usd:
                    continue
            replacement = SelectedExpert(
                source_expert.seat_key,
                source_expert.function,
                source_expert.profession,
                source_expert.domain_focus,
                source_expert.mission,
                candidate.id,
                "席位专属故障替换",
            )
            replacement_result = _attempt_expert(run, profile, replacement, candidate)
            replacement_result.requested_model = source_expert.model_id
            replacement_result.attempts = result.attempts + [{"replacement": True}] + replacement_result.attempts
            used_ids.add(candidate.id)
            used_authors.add(candidate.author)
            replacements += 1
            if replacement_result.status == "success_complete":
                index = results.index(result)
                results[index] = replacement_result
                result = replacement_result
                break
            result.attempts.extend(replacement_result.attempts)
    run.output_dir.mkdir(parents=True, exist_ok=True)
    (run.output_dir / "expert-responses.json").write_text(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def enforce_pre_judge_budget(
    run: RunConfig,
    profile: TaskProfile,
    ranked: Sequence[ModelInfo],
    judge: SelectedJudge,
    results: Sequence[ExpertResult],
) -> float:
    by_id = {model.id: model for model in ranked}
    judge_model = by_id[judge.model_id]
    plan = judge_inference_plan(run, profile, judge, judge_model)
    judge_input_chars = len(run.task) + sum(len(result.answer or "") for result in results) + 3000
    estimate = estimate_call_cost(judge_model, judge_input_chars, plan.max_tokens)
    if run.max_estimated_cost_usd is not None:
        projected = _spent(results) + estimate * run.budget_safety_factor
        if projected > run.max_estimated_cost_usd:
            raise ExpertTeamError(
                f"Judge was not invoked because projected actual spend ${projected:.4f} exceeds hard limit ${run.max_estimated_cost_usd:.4f}."
            )
    return estimate


def write_selection_artifacts(
    run: RunConfig,
    profile: TaskProfile,
    source: str,
    ranked: Sequence[ModelInfo],
    experts: Sequence[SelectedExpert],
    judge: SelectedJudge,
    estimated_cost: float,
) -> None:
    run.output_dir.mkdir(parents=True, exist_ok=True)
    by_id = {model.id: model for model in ranked}
    selection = {
        "created_at": utc_now(),
        "orchestration": "self-managed-fixed-three-experts-plus-judge",
        "team_pattern": profile.team_pattern,
        "fixed_functions": [expert.function for expert in experts] + [judge.function],
        "dynamic_professions": [expert.profession for expert in experts] + [judge.profession],
        "openrouter_router_used": False,
        "openrouter_plugins_used": False,
        "external_tools_allowed": False,
        "catalog_source": source,
        "catalog_degraded": "degraded_missing=" in source,
        "task_profile": asdict(profile),
        "quality_tier": run.quality_tier,
        "experts": [asdict(item) for item in experts],
        "judge": asdict(judge),
        "estimated_cost_usd": round(estimated_cost, 6),
        "budget_safety_factor": run.budget_safety_factor,
        "hard_cost_limit_usd": run.max_estimated_cost_usd,
        "inference_parameters": {
            expert.seat_key: expert_inference_plan(run, profile, expert, by_id[expert.model_id]).evidence() for expert in experts
        },
        "ranking": ranking_rows(ranked, None),
        "seat_candidates": top_candidates_for_evidence(ranked, profile, run, 5),
    }
    selection["inference_parameters"]["judge"] = judge_inference_plan(run, profile, judge, by_id[judge.model_id]).evidence()
    (run.output_dir / "model-selection.json").write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Fixed 3+1 Dynamic Expert Team",
        "",
        f"- Catalog source: `{source}`",
        f"- Quality tier: `{run.quality_tier}`",
        "- Fixed combination: `核心主研席 + 交叉验证席 + 独立反证席 -> 综合裁决席`",
        f"- Estimated total cost: `${estimated_cost:.6f}`",
        f"- Budget safety factor: `{run.budget_safety_factor}`",
        "- OpenRouter router/plugin used: `false`",
        "",
        "## Dynamic professions and models",
        "",
    ]
    for expert in experts:
        lines.append(f"- **{expert.function}**｜职业：**{expert.profession}**｜模型：`{expert.model_id}`｜聚焦：`{expert.domain_focus}`｜{expert.selection_reason}")
    lines.append(f"- **{judge.function}**｜职业：**{judge.profession}**｜模型：`{judge.model_id}`｜{judge.selection_reason}")
    lines += ["", "## Full dynamic ranking", "", "| # | Model | Score | Input $/M | Output $/M | Context | Max output |", "|---:|---|---:|---:|---:|---:|---:|"]
    for row in ranking_rows(ranked, None):
        lines.append(f"| {row['dynamic_rank']} | `{row['model']}` | {row['dynamic_score']:.4f} | {row['prompt_usd_per_million']:.4f} | {row['completion_usd_per_million']:.4f} | {row['context_length']} | {row['max_completion_tokens']} |")
    (run.output_dir / "model-ranking.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_dry_run_artifacts(
    run: RunConfig,
    profile: TaskProfile,
    ranked: Sequence[ModelInfo],
    experts: Sequence[SelectedExpert],
    judge: SelectedJudge,
    estimated_cost: float,
) -> None:
    by_id = {model.id: model for model in ranked}
    dry = {
        "status": "dry-run",
        "created_at": utc_now(),
        "team_pattern": profile.team_pattern,
        "external_tools_allowed": False,
        "reasoning_policy": "dynamic-per-task-per-seat-with-explicit-budget",
        "expert_requests": [build_expert_payload(run, profile, expert, by_id[expert.model_id]) for expert in experts],
        "judge_request_preview": {"model": judge.model_id, "profession": judge.profession, "function": judge.function},
        "estimated_cost_usd": round(estimated_cost, 6),
    }
    (run.output_dir / "expert-team-dry-run.json").write_text(json.dumps(dry, ensure_ascii=False, indent=2), encoding="utf-8")
    write_manifest(run.output_dir)


def write_run_artifacts(
    run: RunConfig,
    profile: TaskProfile,
    results: Sequence[ExpertResult],
    judge: SelectedJudge,
    judge_payload: Dict[str, Any],
    judge_response: Dict[str, Any],
    judge_latency: float,
    estimated_cost: float,
    judge_estimated_cost: float,
) -> None:
    run.output_dir.mkdir(parents=True, exist_ok=True)
    clean = sanitized(judge_response)
    info = diagnostics(judge_response)
    answer = extract_answer(judge_response)
    if info.get("finish_reason") == "length":
        raise ExpertTeamError("Judge answer was truncated with finish_reason=length.")
    actual = _spent(results) + _actual_cost(dict(judge_response.get("usage") or {}))
    (run.output_dir / "judge-response-raw.json").write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    (run.output_dir / "judge-response-diagnostics.json").write_text(json.dumps({**info, "latency_seconds": round(judge_latency, 6), "estimated_cost": judge_estimated_cost}, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence = {
        "status": "success",
        "created_at": utc_now(),
        "orchestration": "self-managed-fixed-three-experts-plus-judge",
        "team_pattern": profile.team_pattern,
        "openrouter_router_used": False,
        "openrouter_plugins_used": False,
        "external_tools_allowed": False,
        "task_profile": asdict(profile),
        "expert_results": [asdict(result) for result in results],
        "judge": asdict(judge),
        "judge_request": judge_payload,
        "judge_response": clean,
        "judge_diagnostics": info,
        "estimated_cost_usd": round(estimated_cost, 6),
        "actual_cost_usd": round(actual, 6),
        "final_answer": answer,
    }
    (run.output_dir / "expert-team-result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    report = "\n".join([
        "# Fixed 3+1 Dynamic Expert Team Report",
        "",
        f"- Generated: `{evidence['created_at']}`",
        "- Combination: `核心主研席 + 交叉验证席 + 独立反证席 -> 综合裁决席`",
        "- Expert calls succeeded: `3/3`",
        f"- Judge profession: `{judge.profession}`",
        f"- Judge model: `{judge.model_id}`",
        f"- Estimated cost: `${estimated_cost:.6f}`",
        f"- Actual reported cost: `${actual:.6f}`",
        "- OpenRouter router/plugin used: `false`",
        "",
        "## Final decision",
        "",
        answer,
        "",
    ])
    (run.output_dir / "expert-team-report.md").write_text(report, encoding="utf-8")
    _record_history(run, judge.model_id, judge_estimated_cost, judge_response, judge_latency, None)
    write_manifest(run.output_dir)
