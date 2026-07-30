"""Inject optimizer-selected prompt modules into task-scoped requests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

_INSTALLED = False


def _selected(run: Any, seat_key: str) -> Mapping[str, Any]:
    try:
        plan = json.loads((Path(run.output_dir) / "team-optimization.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    rows = plan.get("selected") if isinstance(plan, Mapping) else None
    row = rows.get(seat_key) if isinstance(rows, Mapping) else None
    return row if isinstance(row, Mapping) else {}


def _append(payload: dict[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages")
    instructions = row.get("prompt_instructions")
    if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
        return payload
    if not isinstance(instructions, list) or not instructions:
        return payload
    modules = row.get("prompt_modules") if isinstance(row.get("prompt_modules"), list) else []
    suffix = "\n本次资源矩阵选定的提示词模块：" + "、".join(str(x) for x in modules) + "。\n"
    suffix += "\n".join(f"- {str(x)}" for x in instructions)
    current = str(messages[0].get("content") or "")
    if suffix not in current:
        messages[0]["content"] = current + suffix
    return payload


def bind() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    import direct_calls
    import expert_team

    original_expert = direct_calls.build_expert_payload
    if not getattr(original_expert, "_resource_prompt_bound", False):
        def expert_payload(run: Any, profile: Any, expert: Any, model: Any) -> dict[str, Any]:
            return _append(original_expert(run, profile, expert, model), _selected(run, str(expert.seat_key)))
        expert_payload._resource_prompt_bound = True
        direct_calls.build_expert_payload = expert_payload

    original_judge = expert_team.build_judge_payload
    if not getattr(original_judge, "_resource_prompt_bound", False):
        def judge_payload(run: Any, profile: Any, judge: Any, judge_model: Any, results: Sequence[Any]) -> dict[str, Any]:
            return _append(original_judge(run, profile, judge, judge_model, results), _selected(run, "judge"))
        judge_payload._resource_prompt_bound = True
        expert_team.build_judge_payload = judge_payload
