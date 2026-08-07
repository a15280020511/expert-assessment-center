#!/usr/bin/env python3
"""Permissive Issue admission for task-dynamic expert execution.

The Issue entrypoint performs only parsing plus dynamic team materialization.
Historical schema, budget, duplicate, retry-count, busy, Top50, 4+4, company,
free-first and optimizer-status admission gates are intentionally absent.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from v5_governance_model_plan import validate_governance_model_plan
from v5_top50_pool_optimizer import materialize_candidate_pool_selection


def _write_output(name: str, value: Any) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    text = str(value).replace("\n", " ").replace("\r", " ")
    if isinstance(value, bool):
        text = text.lower()
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={text}\n")


def _event(args: argparse.Namespace) -> tuple[str, str, int, str]:
    if args.event_path:
        raw = json.loads(Path(args.event_path).read_text(encoding="utf-8"))
        issue = raw.get("issue") if isinstance(raw.get("issue"), Mapping) else {}
        comment = raw.get("comment") if isinstance(raw.get("comment"), Mapping) else {}
        return (
            str(issue.get("title") or ""),
            str(issue.get("body") or ""),
            int(issue.get("number") or 0),
            str(comment.get("body") or ""),
        )
    return args.issue_title, args.issue_body, int(args.issue_number), args.comment_body


def _task_text(packet: Mapping[str, Any]) -> str:
    task = packet.get("task") if isinstance(packet.get("task"), Mapping) else {}
    question = str(task.get("question") or packet.get("question") or "").strip()
    requirements = task.get("requirements")
    rows = [str(value).strip() for value in requirements] if isinstance(requirements, list) else []
    text = question or json.dumps(task or packet, ensure_ascii=False, default=str)
    if rows:
        text += "\n\n执行要求：\n" + "\n".join(f"- {value}" for value in rows if value)
    evidence = packet.get("evidence")
    has_evidence = evidence is not None and evidence != "" and evidence != [] and evidence != {}
    if has_evidence:
        text += "\n\n已提供证据/上下文：\n" + json.dumps(
            evidence, ensure_ascii=False, default=str
        )
    return text


def _cost_advisory(packet: Mapping[str, Any]) -> float | None:
    budget = packet.get("approved_budget")
    if not isinstance(budget, Mapping):
        return None
    raw = budget.get("cost_anomaly_usd")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _status_outputs(status: Mapping[str, Any]) -> None:
    for key in (
        "accepted",
        "reason",
        "calls",
        "maximum_recovery_calls",
        "maximum_initial_calls",
        "cost_anomaly_usd",
        "task_id",
        "task_fingerprint",
        "is_retry",
        "retry_id",
        "execution_id",
        "selected_expert_count",
        "selected_recovery_count",
    ):
        _write_output(key, status.get(key, ""))


def prepare(args: argparse.Namespace) -> int:
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    title, body, issue_number, comment_body = _event(args)
    status: dict[str, Any] = {
        "accepted": False,
        "title": title,
        "issue_number": issue_number,
        "reason": "",
        "errors": [],
        "admission_mode": "dynamic-no-business-gates",
        "free_first_required": False,
        "canary_required": False,
        "schema_gate_required": False,
        "budget_gate_required": False,
        "duplicate_gate_required": False,
        "busy_gate_required": False,
        "top50_gate_required": False,
        "four_plus_four_required": False,
        "company_uniqueness_required": False,
        "optimizer_optimality_required": False,
        "provider_routing_mode": "unrestricted-openrouter",
    }
    try:
        packet = json.loads(body)
        if not isinstance(packet, Mapping):
            raise ValueError("Issue body must be a JSON object")
        materialized, receipt = materialize_candidate_pool_selection(packet)
        plan = validate_governance_model_plan(materialized)
        selected = list(plan.get("selected_models") or [])
        recoveries = list(plan.get("recovery_models") or [])
        if not selected:
            raise ValueError("dynamic planner produced no expert")

        calls = len(selected) + len(recoveries)
        task_id = str(packet.get("task_id") or f"issue-{issue_number}")
        is_retry = comment_body.startswith("/retry-expert-team")
        command_id = comment_body.split(maxsplit=1)[1].strip() if " " in comment_body else task_id
        status.update(
            {
                "accepted": True,
                "reason": "dynamic expert plan materialized; no business admission gates applied",
                "task_id": task_id,
                "task_fingerprint": str(plan.get("plan_sha256") or receipt.get("receipt_sha256") or ""),
                "calls": calls,
                "maximum_recovery_calls": len(recoveries),
                "maximum_initial_calls": len(selected),
                "cost_anomaly_usd": _cost_advisory(packet),
                "cost_policy": "advisory-only",
                "cost_threshold_can_stop_execution": False,
                "selected_expert_count": len(selected),
                "selected_recovery_count": len(recoveries),
                "ordered_standby_count": int(plan.get("expert_center_ordered_standby_count") or 0),
                "is_retry": is_retry,
                "retry_id": command_id if is_retry else "",
                "execution_id": "" if is_retry else command_id,
                "model_assignment_authority": "expert-assessment-center-dynamic-ortools",
                "fixed_team_size_used": False,
                "fixed_role_topology_used": False,
                "company_uniqueness_constraint_used": False,
                "optimizer_optimality_required": False,
            }
        )
        (root / "ticket.json").write_text(
            json.dumps(materialized, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "governance-model-plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        (root / "expert-center-selection-receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        (root / "task.txt").write_text(_task_text(packet), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - malformed execution input still needs a receipt
        status["reason"] = str(exc)
        status["errors"] = [str(exc)]

    (root / "ticket-status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    _status_outputs(status)
    return 0


def render(args: argparse.Namespace) -> int:
    root = Path(args.output_dir)
    status = json.loads((root / "ticket-status.json").read_text(encoding="utf-8"))
    run_line = f"- Run: `{args.run_url}`\n" if args.run_url else ""
    if args.phase == "accepted":
        heading = "EXECUTION_RETRY_ACCEPTED" if status.get("is_retry") else "EXECUTION_ACCEPTED"
        text = (
            f"## {heading}\n\n"
            "任务已进入动态专家执行。\n\n"
            f"- Task ID：`{status.get('task_id')}`\n"
            f"- 动态专家数：`{status.get('selected_expert_count')}`\n"
            f"- 动态恢复专家数：`{status.get('selected_recovery_count')}`\n"
            f"- 其余候选：`{status.get('ordered_standby_count')}`\n"
            "- 固定4+4：`关闭`\n"
            "- Top50-only：`关闭`\n"
            "- 公司去重：`关闭`\n"
            "- OR-Tools OPTIMAL门禁：`关闭；FEASIBLE/启发式回退均可执行`\n"
            "- free-first / 免费Canary前置：`关闭，仅可作为遥测`\n"
            "- Provider：`完全开放，由OpenRouter动态路由`\n"
            "- 费用/调用预算：`不作为入场门禁；由当前动态执行图决定调用规模`\n"
            + run_line
        )
    elif args.phase == "rejected":
        text = (
            "## EXECUTION_REJECTED\n\n"
            f"输入无法形成可执行动态专家图：{status.get('reason', 'unknown')}。\n"
            "该拒绝仅表示输入/结构不可执行，不是资格、预算、Canary或模型门禁。\n"
            + run_line
        )
    else:
        text = "## EXECUTION_STATUS\n\n" + run_line
    print(text)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--event-path")
    prepare_parser.add_argument("--issue-title", default="")
    prepare_parser.add_argument("--issue-body", default="")
    prepare_parser.add_argument("--issue-number", default=0, type=int)
    prepare_parser.add_argument("--actor", default="")
    prepare_parser.add_argument("--author-association", default="")
    prepare_parser.add_argument("--comment-body", default="")
    prepare_parser.add_argument("--output-dir", default="ticket-artifacts")
    prepare_parser.set_defaults(func=prepare)
    render_parser = sub.add_parser("render")
    render_parser.add_argument(
        "--phase", choices=["accepted", "rejected", "success", "failure"], required=True
    )
    render_parser.add_argument("--output-dir", default="ticket-artifacts")
    render_parser.add_argument("--run-url", default="")
    render_parser.set_defaults(func=render)
    return root


if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.func(arguments))
