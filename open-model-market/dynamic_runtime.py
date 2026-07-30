"""Runtime binding for the OR-Tools-selected dynamic expert team."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

_ACTIVE_PLAN: dict[str, Any] = {}
_INSTALLED = False


@dataclass(frozen=True)
class DynamicInferencePlan:
    effort: str
    max_tokens: int
    reasoning_tokens: int
    temperature: float
    reasoning_supported: bool
    external_tools_allowed: bool
    verbosity: str
    parameter_template: str
    structured_output: bool
    rationale: tuple[str, ...]

    def evidence(self) -> Dict[str, Any]:
        return {
            "effort": self.effort,
            "provider_max_completion_tokens": self.max_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "temperature": self.temperature,
            "reasoning_supported": self.reasoning_supported,
            "external_tools_allowed": self.external_tools_allowed,
            "verbosity": self.verbosity,
            "parameter_template": self.parameter_template,
            "structured_output": self.structured_output,
            "output_token_policy": "provider-model-limit-only",
            "request_token_ceiling_sent": False,
            "reasoning_token_ceiling_sent": False,
            "rationale": list(self.rationale),
        }


def _selected(seat_key: str) -> Mapping[str, Any]:
    rows = _ACTIVE_PLAN.get("selected") if isinstance(_ACTIVE_PLAN, dict) else None
    if isinstance(rows, Mapping):
        row = rows.get(seat_key)
        if isinstance(row, Mapping):
            return row
    return {}


def _plan_for(seat_key: str, model: Any) -> DynamicInferencePlan:
    selected = _selected(seat_key)
    parameters = selected.get("parameters") if isinstance(selected.get("parameters"), Mapping) else {}
    template = str(selected.get("parameter_template") or "balanced")
    effort = str(parameters.get("effort") or "low")
    verbosity = str(parameters.get("verbosity") or "low")
    temperature = float(parameters.get("temperature") or 0.05)
    structured = bool(parameters.get("structured_output", False))
    return DynamicInferencePlan(
        effort=effort,
        max_tokens=max(0, int(getattr(model, "max_completion_tokens", 0) or 0)),
        reasoning_tokens=0,
        temperature=temperature,
        reasoning_supported="reasoning" in set(getattr(model, "supported_parameters", []) or []),
        external_tools_allowed=False,
        verbosity=verbosity,
        parameter_template=template,
        structured_output=structured,
        rationale=(
            "参数由OR-Tools在受控模板中联合选择",
            f"席位={seat_key}",
            f"模板={template}",
            "不发送人为输出token上限",
        ),
    )


def _expert_plan(run: Any, profile: Any, expert: Any, model: Any) -> DynamicInferencePlan:
    del run, profile
    return _plan_for(str(expert.seat_key), model)


def _judge_plan(run: Any, profile: Any, judge: Any, model: Any) -> DynamicInferencePlan:
    del run, profile, judge
    return _plan_for("judge", model)


def _apply_plan(payload: Dict[str, Any], plan: DynamicInferencePlan, model: Any) -> Dict[str, Any]:
    payload.pop("max_tokens", None)
    payload.pop("max_completion_tokens", None)
    supported = set(getattr(model, "supported_parameters", []) or [])
    if "temperature" in supported:
        payload["temperature"] = plan.temperature
    else:
        payload.pop("temperature", None)
    if plan.reasoning_supported:
        payload["reasoning"] = {"exclude": True, "effort": plan.effort}
    else:
        payload.pop("reasoning", None)
    if "verbosity" in supported:
        payload["verbosity"] = plan.verbosity
    else:
        payload.pop("verbosity", None)
    return payload


def _rewrite_system(payload: Dict[str, Any], replacements: Mapping[str, str], suffix: str = "") -> Dict[str, Any]:
    messages = payload.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        text = str(messages[0].get("content") or "")
        for old, new in replacements.items():
            text = text.replace(old, new)
        if suffix and suffix not in text:
            text += suffix
        messages[0]["content"] = text
    return payload


def _dynamic_remove_token_ceilings(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload.pop("max_tokens", None)
    payload.pop("max_completion_tokens", None)
    model_id = str(payload.get("model") or "")
    selected = _ACTIVE_PLAN.get("selected") if isinstance(_ACTIVE_PLAN, dict) else {}
    row: Mapping[str, Any] = {}
    if isinstance(selected, Mapping):
        row = next(
            (
                item for item in selected.values()
                if isinstance(item, Mapping) and str(item.get("model") or "") == model_id
            ),
            {},
        )
    parameters = row.get("parameters") if isinstance(row.get("parameters"), Mapping) else {}
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict):
        reasoning.pop("max_tokens", None)
        reasoning["effort"] = str(parameters.get("effort") or reasoning.get("effort") or "low")
        reasoning["exclude"] = True
    if "verbosity" in payload:
        payload["verbosity"] = str(parameters.get("verbosity") or payload.get("verbosity") or "low")
    return payload


def _candidate_rows(ranked: Sequence[Any], profile: Any, run: Any, limit: int = 5) -> Dict[str, list[dict[str, Any]]]:
    import seat_scoring as scoring

    selected = _ACTIVE_PLAN.get("selected") if isinstance(_ACTIVE_PLAN, dict) else {}
    pool = scoring._stable_pool(ranked, profile)
    evidence: Dict[str, list[dict[str, Any]]] = {}
    iterator = selected.items() if isinstance(selected, Mapping) else []
    for seat_key, selected_row in iterator:
        if not isinstance(selected_row, Mapping):
            continue
        domain = str(selected_row.get("domain") or profile.primary_domain)
        chosen = str(selected_row.get("model") or "")
        if seat_key == "judge":
            rows = [
                model for model in pool
                if profile.primary_domain == "coding"
                or not any(term in scoring._text(model) for term in scoring.CODE_SPECIALIST_TERMS)
            ] or list(pool)
        else:
            rows = scoring._seat_pool(pool, str(seat_key), domain)
        ordered = scoring._ordered(
            rows,
            str(seat_key),
            domain,
            str(_ACTIVE_PLAN.get("task_input", {}).get("objective") or run.quality_tier),
        )
        chosen_model = next((model for model in ordered if model.id == chosen), None)
        selected_models = ([chosen_model] if chosen_model is not None else []) + [
            model for model in ordered if model.id != chosen
        ][: max(0, limit - 1)]
        evidence[str(seat_key)] = [
            scoring._candidate_row(
                model,
                index=index,
                domain=domain,
                selected=model.id == chosen,
            )
            for index, model in enumerate(selected_models, 1)
        ]
    return evidence


def _rewrite_json(path: Path, update: Mapping[str, Any]) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return
    if not isinstance(data, dict):
        return
    data.update(update)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    import direct_calls
    import expert_team
    import seat_scoring

    _INSTALLED = True
    direct_calls.expert_inference_plan = _expert_plan
    direct_calls.judge_inference_plan = _judge_plan
    direct_calls.apply_plan = _apply_plan
    seat_scoring.expert_inference_plan = _expert_plan
    seat_scoring.judge_inference_plan = _judge_plan
    direct_calls.top_candidates_for_evidence = _candidate_rows

    original_expert_payload = direct_calls.build_expert_payload
    if not getattr(original_expert_payload, "_dynamic_team_bound", False):
        def dynamic_expert_payload(run: Any, profile: Any, expert: Any, model: Any) -> Dict[str, Any]:
            payload = original_expert_payload(run, profile, expert, model)
            count = int(_ACTIVE_PLAN.get("expert_count") or 3)
            template = str(_selected(str(expert.seat_key)).get("parameter_template") or "balanced")
            return _rewrite_system(
                payload,
                {"固定三席专家团中的独立成员。": f"动态{count}席专家团中的独立成员。"},
                f"本席参数模板：{template}。",
            )
        dynamic_expert_payload._dynamic_team_bound = True
        direct_calls.build_expert_payload = dynamic_expert_payload

    original_judge_payload = expert_team.build_judge_payload
    if not getattr(original_judge_payload, "_dynamic_team_bound", False):
        def dynamic_judge_payload(run: Any, profile: Any, judge: Any, judge_model: Any, results: Sequence[Any]) -> Dict[str, Any]:
            payload = original_judge_payload(run, profile, judge, judge_model, results)
            count = int(_ACTIVE_PLAN.get("expert_count") or len(results) or 3)
            replacements = {
                "固定三席一裁结构": f"动态{count}席一裁结构",
                "只比较输入中三名专家的独立结论": f"只比较输入中{count}名专家的独立结论",
                "固定组合：三名专家＋一名裁判": f"动态组合：{count}名专家＋一名裁判",
            }
            return _rewrite_system(payload, replacements, "裁判必须核对各席位职责覆盖，不得因专家人数变化降低完整性。")
        dynamic_judge_payload._dynamic_team_bound = True
        expert_team.build_judge_payload = dynamic_judge_payload

    original_recover = expert_team._recover_substantial_partials
    if not getattr(original_recover, "_dynamic_team_bound", False):
        def dynamic_recover(run: Any, results: Sequence[Any]) -> Sequence[Any]:
            recovered = original_recover(run, results)
            usable = [item for item in recovered if getattr(item, "status", "") in expert_team.USABLE_EXPERT_STATUSES]
            expected = int(_ACTIVE_PLAN.get("expert_count") or len(recovered))
            if len(usable) != expected:
                raise expert_team.ExpertTeamError(
                    f"Dynamic {expected}+1 execution requires {expected}/{expected} usable expert answers; received {len(usable)}/{expected}."
                )
            return recovered
        dynamic_recover._dynamic_team_bound = True
        expert_team._recover_substantial_partials = dynamic_recover

    original_attempt_count = expert_team._expert_attempt_count
    if not getattr(original_attempt_count, "_dynamic_team_bound", False):
        def dynamic_attempt_count(results: Sequence[Any]) -> int:
            actual = original_attempt_count(results)
            expected = int(_ACTIVE_PLAN.get("expert_count") or 3)
            return max(0, actual - expected + 3)
        dynamic_attempt_count._dynamic_team_bound = True
        expert_team._expert_attempt_count = dynamic_attempt_count

    original_selection_writer = expert_team.write_selection_artifacts
    if not getattr(original_selection_writer, "_dynamic_team_bound", False):
        def dynamic_selection_writer(run: Any, profile: Any, source: str, ranked: Sequence[Any], experts: Sequence[Any], judge: Any, estimated: float) -> None:
            original_selection_writer(run, profile, source, ranked, experts, judge, estimated)
            update = {
                "orchestration": "google-or-tools-cp-sat-dynamic-team",
                "team_pattern": _ACTIVE_PLAN.get("team_pattern"),
                "expert_count": _ACTIVE_PLAN.get("expert_count"),
                "optimizer": {
                    "name": _ACTIVE_PLAN.get("optimizer"),
                    "solver_status": _ACTIVE_PLAN.get("solver_status"),
                    "objective_value": _ACTIVE_PLAN.get("objective_value"),
                    "best_objective_bound": _ACTIVE_PLAN.get("best_objective_bound"),
                },
                "task_optimization_input": _ACTIVE_PLAN.get("task_input"),
                "parameter_templates": {
                    key: row.get("parameter_template")
                    for key, row in (_ACTIVE_PLAN.get("selected") or {}).items()
                    if isinstance(row, Mapping)
                },
            }
            _rewrite_json(run.output_dir / "model-selection.json", update)
            path = run.output_dir / "model-ranking.md"
            if path.exists():
                text = path.read_text(encoding="utf-8")
                text = text.replace("# Fixed 3+1 Dynamic Expert Team", "# OR-Tools Dynamic Expert Team")
                text = text.replace(
                    "- Fixed combination: `核心主研席 + 交叉验证席 + 独立反证席 -> 综合裁决席`",
                    f"- Optimized combination: `{_ACTIVE_PLAN.get('team_pattern')}`",
                )
                path.write_text(text, encoding="utf-8")
        dynamic_selection_writer._dynamic_team_bound = True
        expert_team.write_selection_artifacts = dynamic_selection_writer

    original_dry_writer = expert_team.write_dry_run_artifacts
    if not getattr(original_dry_writer, "_dynamic_team_bound", False):
        def dynamic_dry_writer(run: Any, profile: Any, ranked: Sequence[Any], experts: Sequence[Any], judge: Any, estimated: float) -> None:
            original_dry_writer(run, profile, ranked, experts, judge, estimated)
            _rewrite_json(
                run.output_dir / "expert-team-dry-run.json",
                {
                    "team_pattern": _ACTIVE_PLAN.get("team_pattern"),
                    "expert_count": _ACTIVE_PLAN.get("expert_count"),
                    "optimizer": "google-or-tools-cp-sat",
                    "parameter_templates": {
                        key: row.get("parameter_template")
                        for key, row in (_ACTIVE_PLAN.get("selected") or {}).items()
                        if isinstance(row, Mapping)
                    },
                },
            )
        dynamic_dry_writer._dynamic_team_bound = True
        expert_team.write_dry_run_artifacts = dynamic_dry_writer

    original_run_writer = expert_team.write_run_artifacts
    if not getattr(original_run_writer, "_dynamic_team_bound", False):
        def dynamic_run_writer(*args: Any, **kwargs: Any) -> None:
            original_run_writer(*args, **kwargs)
            run = args[0]
            count = int(_ACTIVE_PLAN.get("expert_count") or 3)
            _rewrite_json(
                run.output_dir / "expert-team-result.json",
                {
                    "orchestration": "google-or-tools-cp-sat-dynamic-team",
                    "team_pattern": _ACTIVE_PLAN.get("team_pattern"),
                    "expert_count": count,
                    "optimizer": {
                        "name": _ACTIVE_PLAN.get("optimizer"),
                        "solver_status": _ACTIVE_PLAN.get("solver_status"),
                    },
                },
            )
            report = run.output_dir / "expert-team-report.md"
            if report.exists():
                text = report.read_text(encoding="utf-8")
                text = text.replace("# Fixed 3+1 Dynamic Expert Team Report", "# OR-Tools Dynamic Expert Team Report")
                text = text.replace(
                    "- Combination: `核心主研席 + 交叉验证席 + 独立反证席 -> 综合裁决席`",
                    f"- Combination: `{_ACTIVE_PLAN.get('team_pattern')}`",
                )
                text = text.replace("- Expert calls succeeded: `3/3`", f"- Expert calls succeeded: `{count}/{count}`")
                report.write_text(text, encoding="utf-8")
        dynamic_run_writer._dynamic_team_bound = True
        expert_team.write_run_artifacts = dynamic_run_writer


def _patch_hardened_entrypoint() -> None:
    main_module = sys.modules.get("__main__")
    if main_module is None:
        return
    if hasattr(main_module, "_remove_token_ceilings"):
        setattr(main_module, "_remove_token_ceilings", _dynamic_remove_token_ceilings)
    original_write_ledger = getattr(main_module, "write_ledger", None)
    if callable(original_write_ledger) and not getattr(original_write_ledger, "_dynamic_team_bound", False):
        def dynamic_write_ledger(output_dir: Path) -> dict[str, Any]:
            ledger = original_write_ledger(output_dir)
            count = int(_ACTIVE_PLAN.get("expert_count") or 3)
            summary = ledger.get("summary") if isinstance(ledger, dict) else None
            if isinstance(summary, dict):
                expert_calls = int(summary.get("expert_attempt_calls") or 0)
                judge_calls = int(summary.get("judge_attempt_calls") or 0)
                summary["planned_expert_count"] = count
                summary["minimum_planned_calls_present"] = int(summary.get("call_count") or 0) >= count + 1
                summary["minimum_fixed_calls_present"] = summary["minimum_planned_calls_present"]
                summary["expert_replacement_calls"] = max(0, expert_calls - count)
                summary["replacement_calls"] = summary["expert_replacement_calls"] + max(0, judge_calls - 1)
                Path(output_dir, "call-ledger.json").write_text(
                    json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            return ledger
        dynamic_write_ledger._dynamic_team_bound = True
        setattr(main_module, "write_ledger", dynamic_write_ledger)


def activate_runtime(plan: Mapping[str, Any], run: Any, profile: Any, experts: Sequence[Any], judge: Any) -> None:
    """Activate one audited optimizer plan for subsequent artifact writing and calls."""
    del run, profile, experts, judge
    global _ACTIVE_PLAN
    _ACTIVE_PLAN = json.loads(json.dumps(dict(plan), ensure_ascii=False))
    _install()
    _patch_hardened_entrypoint()
