#!/usr/bin/env python3
"""CLI for the hardened fixed 3+1 dynamically staffed expert team."""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from artifact_manifest import write_manifest  # noqa: E402
from capability_selection import select_team  # noqa: E402
from direct_calls import (  # noqa: E402
    _record_history,
    build_judge_payload,
    call_model,
    enforce_pre_judge_budget,
    execute_experts,
    utc_now,
    write_dry_run_artifacts,
    write_run_artifacts,
    write_selection_artifacts,
)
import model_market as market  # noqa: E402
from model_market import (  # noqa: E402
    DEFAULT_CONFIG,
    ExpertTeamError,
    ModelInfo,
    RunConfig,
    SelectedJudge,
    TaskProfile,
    fetch_catalog,
    rank_models,
)
from performance_history import load_history  # noqa: E402
from response_audit import diagnostics, extract_answer, sanitized  # noqa: E402
from routing_guards import (  # noqa: E402
    enforce_semantic_confidence,
    minimum_semantic_confidence,
    strip_evidence_for_classification,
)
from runtime_guards import apply_judge_output_contract, enforce_post_judge_actual_budget  # noqa: E402
from task_router import (  # noqa: E402
    annotate_selection_artifacts,
    execution_run_after_routing,
    finalize_run_artifacts,
    load_routing_config,
    route_task,
    total_model_calls_from_env,
    write_routing_artifact,
)

COMPLEX_ANALYSIS_TERMS = (
    "短期", "中期", "长期", "底线", "约束", "交易", "替代解释", "竞争性解释",
    "观察指标", "情景", "推演", "博弈", "因果", "置信度", "证据缺口",
    "short-term", "medium-term", "long-term", "constraint", "alternative explanation",
    "scenario", "game theory", "confidence", "indicator",
)
GEOPOLITICAL_TERMS = (
    "外交", "国务卿", "外长", "制裁", "战争", "军事", "乌克兰", "俄罗斯", "俄方",
    "美国", "北约", "国际关系", "geopolit", "foreign minister", "secretary of state",
    "sanction", "military", "war",
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
OPERATIONAL_METADATA_RE = re.compile(
    r"\b(?:github|openrouter|issue\s*runner|github\s*actions?|artifact)\b|"
    r"GitHub专家团|GitHub执行|执行票据|工作流|运行编号|模型网关",
    re.IGNORECASE,
)
MIN_USABLE_PARTIAL_CHARS = 400
MIN_USABLE_JUDGE_CHARS = 800
USABLE_EXPERT_STATUSES = {"success_complete", "success_partial"}


def build_run_config(args: argparse.Namespace) -> RunConfig:
    """Build one validated run config and preserve the call replacement ceiling."""
    run = market.build_run_config(args)
    raw = os.getenv("EXPERT_MAX_REPLACEMENTS")
    if raw in {None, ""}:
        return run
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ExpertTeamError("EXPERT_MAX_REPLACEMENTS must be an integer.") from exc
    if not 0 <= limit <= 2:
        raise ExpertTeamError("EXPERT_MAX_REPLACEMENTS must be between 0 and 2.")
    return replace(run, maximum_replacements=limit)


def _semantic_task_text(task: str) -> str:
    """Remove evidence, execution plumbing, and URL tokens before classification."""
    text = strip_evidence_for_classification(task)
    text = URL_RE.sub(" ", text)
    text = OPERATIONAL_METADATA_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify_task(task: str, run: RunConfig) -> TaskProfile:
    """Classify the substantive task while preserving full context capacity."""
    semantic_text = _semantic_task_text(task)
    profile = market.classify_task(semantic_text, run)
    text = semantic_text.lower()
    complex_hits = sum(1 for term in COMPLEX_ANALYSIS_TERMS if term in text)
    geopolitical = any(term in text for term in GEOPOLITICAL_TERMS)
    high_stakes = profile.high_stakes or geopolitical
    complexity = profile.complexity
    score = profile.complexity_score
    if high_stakes or complex_hits >= 2:
        complexity = "complex"
        score = max(score, 4 + int(high_stakes))
    elif complex_hits == 1 and complexity == "simple":
        complexity = "medium"
        score = max(score, 2)
    actual_context = max(
        run.minimum_context_length,
        int(len(task) / 2.5) + 3 * run.max_completion_tokens,
        profile.requested_context,
    )
    return replace(
        profile,
        complexity=complexity,
        complexity_score=score,
        high_stakes=high_stakes,
        requested_context=actual_context,
    )


def _recover_substantial_partials(run: RunConfig, results: Sequence[Any]) -> Sequence[Any]:
    """Keep substantive expert text if a provider stops at its own output limit."""
    changed = False
    for result in results:
        if result.status != "failed" or result.finish_reason != "length":
            continue
        for attempt in reversed(result.attempts):
            response = attempt.get("sanitized_response") if isinstance(attempt, Mapping) else None
            if not isinstance(response, Mapping):
                continue
            try:
                answer = extract_answer(response)
            except ExpertTeamError:
                continue
            if len(answer) < MIN_USABLE_PARTIAL_CHARS:
                continue
            result.answer = answer
            result.status = "success_partial"
            attempt["recovered_as_usable_partial"] = True
            attempt["partial_answer_chars"] = len(answer)
            changed = True
            break
    if changed:
        run.output_dir.mkdir(parents=True, exist_ok=True)
        (run.output_dir / "expert-responses.json").write_text(
            json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return results


def _prepare_judge_response(
    run: RunConfig,
    response: Mapping[str, Any],
    latency_seconds: float,
    estimated_cost: float,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], str, str]:
    """Persist judge evidence before enforcing completeness."""
    run.output_dir.mkdir(parents=True, exist_ok=True)
    clean = sanitized(response)
    info = diagnostics(response)
    audit = {**info, "latency_seconds": round(latency_seconds, 6), "estimated_cost": estimated_cost}
    (run.output_dir / "judge-response-raw.json").write_text(
        json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run.output_dir / "judge-response-diagnostics.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    answer = extract_answer(response)
    if info.get("finish_reason") != "length":
        return dict(response), clean, info, answer, "success_complete"
    if len(answer) < MIN_USABLE_JUDGE_CHARS:
        raise ExpertTeamError(
            f"Judge answer was truncated and too short to publish: {len(answer)} characters. Raw evidence was preserved."
        )
    adjusted = copy.deepcopy(dict(response))
    choices = adjusted.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choices[0]["finish_reason"] = "partial_length"
    return adjusted, clean, info, answer, "success_partial"


def _history_rejects_judge(run: RunConfig, model_id: str) -> bool:
    stats = load_history(run.history_path).get(model_id, {})
    calls = int(stats.get("calls") or 0)
    if calls < 3:
        return False
    truncated = int(stats.get("truncated") or 0)
    empty = int(stats.get("empty_answers") or 0)
    reasoning_share = float(stats.get("avg_reasoning_share") or 0.0)
    consecutive = int(stats.get("consecutive_failures") or 0)
    return (
        truncated / calls > 0.20
        or empty / calls > 0.10
        or consecutive >= 2
        or (truncated > 0 and reasoning_share > 0.55)
    )


def _candidate_judges(
    ranked: Sequence[ModelInfo],
    profile: TaskProfile,
    excluded_ids: set[str],
    excluded_authors: set[str],
    run: RunConfig,
) -> list[ModelInfo]:
    eligible = [
        model
        for model in ranked
        if model.id not in excluded_ids
        and model.context_length >= profile.requested_context
        and not _history_rejects_judge(run, model.id)
    ]
    distinct = [model for model in eligible if model.author not in excluded_authors]
    return distinct or eligible


def _prefer_reliable_judge(
    run: RunConfig,
    profile: TaskProfile,
    ranked: Sequence[ModelInfo],
    experts: Sequence[Any],
    judge: SelectedJudge,
) -> SelectedJudge:
    if not _history_rejects_judge(run, judge.model_id):
        return judge
    by_id = {model.id: model for model in ranked}
    expert_ids = {expert.model_id for expert in experts}
    authors = {by_id[model_id].author for model_id in expert_ids if model_id in by_id}
    candidates = _candidate_judges(ranked, profile, expert_ids | {judge.model_id}, authors, run)
    if not candidates:
        return judge
    replacement = candidates[0]
    return SelectedJudge(
        judge.function,
        judge.profession,
        replacement.id,
        judge.selection_reason + f"；历史完整交付保护替换原候选={judge.model_id}",
    )


def _expert_attempt_count(results: Sequence[Any]) -> int:
    return sum(
        1
        for result in results
        for attempt in (getattr(result, "attempts", []) or [])
        if isinstance(attempt, Mapping) and attempt.get("replacement") is not True
    )


def _judge_error_code(message: str, info: Mapping[str, Any]) -> str:
    text = message.casefold()
    if "truncated and too short" in text:
        return "JUDGE_OUTPUT_TOO_SHORT"
    if "timeout" in text or "timed out" in text:
        return "JUDGE_TIMEOUT"
    if "empty" in text or "no final answer" in text:
        return "JUDGE_EMPTY_ANSWER"
    if info.get("finish_reason") == "length":
        return "JUDGE_OUTPUT_TRUNCATED"
    return "JUDGE_CALL_FAILED"


def _write_judge_attempts(run: RunConfig, attempts: Sequence[Mapping[str, Any]]) -> None:
    run.output_dir.mkdir(parents=True, exist_ok=True)
    (run.output_dir / "judge-attempts.json").write_text(
        json.dumps(list(attempts), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _run_judge_attempt(
    run: RunConfig,
    profile: TaskProfile,
    ranked: Sequence[ModelInfo],
    judge: SelectedJudge,
    results: Sequence[Any],
    attempt_index: int,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    by_id = {model.id: model for model in ranked}
    model = by_id[judge.model_id]
    estimated = enforce_pre_judge_budget(run, profile, ranked, judge, results)
    payload = build_judge_payload(run, profile, judge, model, results)
    apply_judge_output_contract(payload)
    response: Dict[str, Any] = {}
    latency = 0.0
    error = ""
    adjusted: Dict[str, Any] | None = None
    clean: Dict[str, Any] = {}
    info: Dict[str, Any] = {}
    answer = ""
    status = "failed"
    try:
        response, latency = call_model(run, payload)
        adjusted, clean, info, answer, status = _prepare_judge_response(
            run, response, latency, estimated
        )
    except Exception as exc:  # noqa: BLE001 - converted into audited attempt
        error = str(exc)
        info = diagnostics(response)
        clean = sanitized(response) if response else {}
        if response:
            try:
                answer = extract_answer(response)
            except Exception:  # noqa: BLE001 - evidence already preserved
                answer = ""
    error_code = _judge_error_code(error, info) if error else ("JUDGE_OUTPUT_PARTIAL" if status == "success_partial" else "NONE")
    _record_history(run, judge.model_id, estimated, response or None, latency, error or ("partial output" if status == "success_partial" else None))
    attempt = {
        "attempt_index": attempt_index,
        "model": judge.model_id,
        "profession": judge.profession,
        "status": status,
        "error_code": error_code,
        "error": error or None,
        "answer_chars": len(answer),
        "estimated_cost": estimated,
        "latency_seconds": round(latency, 6),
        "response_diagnostics": {**info, "model": judge.model_id},
    }
    attempts.append(attempt)
    _write_judge_attempts(run, attempts)
    if clean:
        (run.output_dir / f"judge-attempt-{attempt_index:03d}-response.json").write_text(
            json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (run.output_dir / f"judge-attempt-{attempt_index:03d}-diagnostics.json").write_text(
        json.dumps(attempt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "judge": judge,
        "payload": payload,
        "response": response,
        "adjusted": adjusted,
        "clean": clean,
        "info": info,
        "answer": answer,
        "status": status,
        "estimated": estimated,
        "latency": latency,
        "error": error,
        "error_code": error_code,
    }


def _choose_judge_result(candidates: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    complete = [item for item in candidates if item.get("status") == "success_complete"]
    if complete:
        return complete[-1]
    partial = [item for item in candidates if item.get("status") == "success_partial"]
    if partial:
        return max(partial, key=lambda item: len(str(item.get("answer") or "")))
    return None


def _finalize_judge_artifacts(
    run: RunConfig,
    judge_model_id: str,
    original_clean: Dict[str, Any],
    original_info: Dict[str, Any],
    answer: str,
    judge_status: str,
    latency_seconds: float,
    estimated_cost: float,
    attempt_count: int,
    replacement_used: bool,
) -> None:
    """Restore final diagnostics and disclose partial or recovered judge status."""
    audit = {
        **original_info,
        "model": judge_model_id,
        "latency_seconds": round(latency_seconds, 6),
        "estimated_cost": estimated_cost,
    }
    (run.output_dir / "judge-response-raw.json").write_text(
        json.dumps(original_clean, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run.output_dir / "judge-response-diagnostics.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result_path = run.output_dir / "expert-team-result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["status"] = "success" if judge_status == "success_complete" else "success_partial"
    result["judge_status"] = judge_status
    result["judge_response"] = original_clean
    result["judge_diagnostics"] = original_info
    result["final_answer"] = answer
    result["judge_attempt_count"] = attempt_count
    result["judge_replacement_used"] = replacement_used
    cost = json.loads((run.output_dir / "cost-evidence.json").read_text(encoding="utf-8")) if (run.output_dir / "cost-evidence.json").exists() else {}
    if cost:
        result["actual_cost_usd"] = cost.get("provider_actual_team_cost_usd", result.get("actual_cost_usd"))
        result["conservative_cost_usd"] = cost.get("conservative_team_cost_usd")
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = run.output_dir / "expert-team-report.md"
    report = report_path.read_text(encoding="utf-8")
    report = report.replace(
        "- OpenRouter router/plugin used: `false`",
        (
            "- OpenRouter router/plugin used: `false`"
            f"\n- Judge status: `{judge_status}`"
            f"\n- Judge attempts: `{attempt_count}`"
            f"\n- Judge replacement used: `{str(replacement_used).lower()}`"
        ),
    )
    if judge_status == "success_partial":
        report = report.replace(
            "## Final decision\n\n",
            "## Final decision\n\n> ⚠️ 裁判正文因模型或Provider自身输出限制停止；以下为已保存的部分裁决，不得视为完整裁决。\n\n",
        )
    report_path.write_text(report, encoding="utf-8")
    write_manifest(run.output_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a hardened fixed 3+1 expert topology with conditional semantic task routing."
    )
    parser.add_argument("--task", help="Task text. Can also use EXPERT_TASK.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output-dir", default="artifacts")
    parser.add_argument("--quality-tier", choices=["budget", "value", "quality"])
    parser.add_argument("--ranking-limit", type=int)
    parser.add_argument("--max-estimated-cost-usd")
    parser.add_argument("--max-completion-tokens", type=int)
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"])
    parser.add_argument("--catalog-file", help="Deterministic catalog fixture for tests.")
    parser.add_argument("--require-live-catalog", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    current_stage = "startup"
    try:
        current_stage = "configuration"
        base_run = build_run_config(args)
        original_cost_limit = None
        total_model_calls = total_model_calls_from_env(base_run, os.environ)
        routing_config = load_routing_config(Path(args.config))
        semantic_confidence_floor = minimum_semantic_confidence(Path(args.config))
        policy = market.load_json(market.POLICY_FILE)

        current_stage = "catalog_and_routing"
        initial_profile = classify_task(base_run.task, base_run)
        models, source = fetch_catalog(base_run)
        ranked = rank_models(models, initial_profile, base_run)
        routing_run = replace(base_run, task=_semantic_task_text(base_run.task))
        routing = route_task(
            routing_run,
            initial_profile,
            ranked,
            policy,
            routing_config,
            total_model_calls,
        )
        routing = enforce_semantic_confidence(routing, initial_profile, semantic_confidence_floor)
        write_routing_artifact(output_dir, routing)

        run = execution_run_after_routing(base_run, routing, total_model_calls)
        profile = routing.profile
        if profile != initial_profile:
            ranked = rank_models(models, profile, run)

        current_stage = "selection"
        experts, judge, estimated = select_team(
            ranked,
            profile,
            run,
            routing.required_capabilities,
        )
        judge = _prefer_reliable_judge(run, profile, ranked, experts, judge)
        write_selection_artifacts(run, profile, source, ranked, experts, judge, estimated)
        if run.dry_run:
            write_dry_run_artifacts(run, profile, ranked, experts, judge, estimated)
            annotate_selection_artifacts(
                output_dir,
                routing,
                total_model_calls=total_model_calls,
                original_cost_limit=None,
                remaining_team_cost_limit=None,
                team_estimated_cost=estimated,
                maximum_replacements=run.maximum_replacements,
            )
            print(f"Dry-run artifacts written to {run.output_dir}")
            return 0

        annotate_selection_artifacts(
            output_dir,
            routing,
            total_model_calls=total_model_calls,
            original_cost_limit=None,
            remaining_team_cost_limit=None,
            team_estimated_cost=estimated,
            maximum_replacements=run.maximum_replacements,
        )

        current_stage = "experts"
        results = list(_recover_substantial_partials(run, execute_experts(run, profile, ranked, experts)))
        usable = [result for result in results if result.status in USABLE_EXPERT_STATUSES]
        if run.require_all_experts and len(usable) != 3:
            raise ExpertTeamError(
                f"Fixed 3+1 execution requires 3/3 usable expert answers; received {len(usable)}/3."
            )

        current_stage = "judge"
        judge_attempts: list[dict[str, Any]] = []
        outcomes: list[Mapping[str, Any]] = []
        first = _run_judge_attempt(run, profile, ranked, judge, results, 1, judge_attempts)
        outcomes.append(first)

        expert_replacements = max(0, _expert_attempt_count(results) - 3)
        remaining_replacements = max(0, run.maximum_replacements - expert_replacements)
        if first.get("status") != "success_complete" and remaining_replacements > 0:
            by_id = {model.id: model for model in ranked}
            used_ids = {
                str(attempt.get("model"))
                for result in results
                for attempt in (getattr(result, "attempts", []) or [])
                if isinstance(attempt, Mapping) and attempt.get("model")
            }
            used_ids.add(judge.model_id)
            used_authors = {by_id[model_id].author for model_id in used_ids if model_id in by_id}
            candidates = _candidate_judges(ranked, profile, used_ids, used_authors, run)
            if candidates:
                candidate = candidates[0]
                replacement_judge = SelectedJudge(
                    judge.function,
                    judge.profession,
                    candidate.id,
                    f"受控裁判故障替换；原裁判={judge.model_id}",
                )
                outcomes.append(
                    _run_judge_attempt(
                        run,
                        profile,
                        ranked,
                        replacement_judge,
                        results,
                        2,
                        judge_attempts,
                    )
                )

        chosen = _choose_judge_result(outcomes)
        if chosen is None:
            last = outcomes[-1]
            raise ExpertTeamError(str(last.get("error") or "All judge attempts failed without a usable report."))

        final_judge = chosen["judge"]
        final_response = chosen["response"]
        enforce_post_judge_actual_budget(run, results, final_response)
        write_run_artifacts(
            run,
            profile,
            results,
            final_judge,
            chosen["payload"],
            chosen["adjusted"],
            chosen["latency"],
            estimated,
            chosen["estimated"],
        )
        replacement_used = int(chosen.get("judge", judge).model_id != judge.model_id) == 1 or len(judge_attempts) > 1
        _finalize_judge_artifacts(
            run,
            final_judge.model_id,
            chosen["clean"],
            chosen["info"],
            chosen["answer"],
            chosen["status"],
            chosen["latency"],
            chosen["estimated"],
            len(judge_attempts),
            replacement_used,
        )
        finalize_run_artifacts(
            output_dir,
            routing,
            total_model_calls=total_model_calls,
            original_cost_limit=original_cost_limit,
            maximum_replacements=run.maximum_replacements,
        )
        print(f"Expert-team artifacts written to {run.output_dir}")
        return 0
    except (ExpertTeamError, ValueError) as exc:
        output_dir.mkdir(parents=True, exist_ok=True)
        attempts = json.loads((output_dir / "judge-attempts.json").read_text(encoding="utf-8")) if (output_dir / "judge-attempts.json").exists() else []
        last = attempts[-1] if isinstance(attempts, list) and attempts else {}
        info = last.get("response_diagnostics") if isinstance(last, Mapping) and isinstance(last.get("response_diagnostics"), Mapping) else {}
        message = str(exc)
        error_code = str(last.get("error_code") or _judge_error_code(message, info)) if current_stage == "judge" else (
            "EXPERT_QUORUM_FAILED" if current_stage == "experts" else "EXECUTION_ERROR"
        )
        error = {
            "status": "error",
            "created_at": utc_now(),
            "error_code": error_code,
            "stage": current_stage,
            "message": message,
            "error": message,
            "model": last.get("model") if isinstance(last, Mapping) else None,
            "provider": info.get("provider") if isinstance(info, Mapping) else None,
            "finish_reason": info.get("finish_reason") if isinstance(info, Mapping) else None,
            "completion_tokens": info.get("completion_tokens") if isinstance(info, Mapping) else None,
            "reasoning_tokens": info.get("reasoning_tokens") if isinstance(info, Mapping) else None,
            "answer_chars": last.get("answer_chars") if isinstance(last, Mapping) else None,
            "retryable": error_code in {"JUDGE_OUTPUT_TOO_SHORT", "JUDGE_OUTPUT_TRUNCATED", "JUDGE_TIMEOUT", "JUDGE_EMPTY_ANSWER"},
        }
        (output_dir / "expert-team-error.json").write_text(
            json.dumps(error, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_manifest(output_dir)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
