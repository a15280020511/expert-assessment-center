#!/usr/bin/env python3
"""Hardened execution-ticket entrypoint."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Iterable

import issue_ticket as base

TRUSTED_STATE_ACTORS = {"github-actions[bot]"}
TRUSTED_STATE_PREFIXES = (
    "## EXECUTION_ACCEPTED",
    "## EXECUTION_RETRY_ACCEPTED",
    "## EXECUTION_COMPLETED",
    "## EXECUTION_DEGRADED",
    "## EXECUTION_FAILED",
    "## EXECUTION_REJECTED",
)
_BASE_EXECUTION_STATE = base._execution_state
_BASE_TASK_FINGERPRINT = base.task_fingerprint


def _trusted_issue_comments(repo: str, issue_number: int) -> Iterable[str]:
    """Yield only bot-authored comments that begin with a formal state heading."""
    page = 1
    while page <= 5:
        url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments?per_page=100&page={page}"
        rows = base._api_json(url)
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            user = row.get("user") if isinstance(row.get("user"), dict) else {}
            if str(user.get("login") or "") not in TRUSTED_STATE_ACTORS:
                continue
            body = str(row.get("body") or "").strip()
            if body.startswith(TRUSTED_STATE_PREFIXES):
                yield body
        if len(rows) < 100:
            return
        page += 1


def _execution_state(comments: Iterable[str]) -> dict[str, Any]:
    """Treat DEGRADED as retryable failure, not successful completion."""
    bodies = list(comments)
    state = _BASE_EXECUTION_STATE(bodies)
    degraded = any(body.startswith("## EXECUTION_DEGRADED") for body in bodies)
    state["degraded"] = degraded
    if degraded and not state.get("completed"):
        state["failed"] = True
    return state


def _substantive_task_fingerprint(packet: dict[str, Any]) -> str:
    """Fingerprint only substantive task fields; execution objectives cannot bypass deduplication."""
    task = packet.get("task") if isinstance(packet.get("task"), dict) else {}
    requirements = task.get("requirements") if isinstance(task.get("requirements"), list) else []
    canonical = {
        "question": base._normalize_semantic_text(task.get("question")),
        "requirements": sorted(
            {
                normalized
                for item in requirements
                if isinstance(item, str)
                for normalized in [base._normalize_semantic_text(item)]
                if normalized
            }
        ),
    }
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _structured_evidence_text(evidence: Any) -> str:
    if not isinstance(evidence, list):
        return ""
    rows = []
    for index, item in enumerate(evidence, 1):
        if not isinstance(item, dict):
            continue
        fields = []
        for label, key in (
            ("中心", "center"),
            ("Run", "run_id"),
            ("Artifact", "artifact_id"),
            ("文件", "file"),
            ("SHA256", "sha256"),
            ("观测时间", "observed_at"),
        ):
            value = base._clean_string(item.get(key))
            if value:
                fields.append(f"{label}={value}")
        if fields:
            rows.append(f"- [{index}] " + "；".join(fields))
    if not rows:
        return ""
    return (
        "\n\n结构化证据引用（由网页GPT取回并核验；专家不得自行下载或联网）：\n"
        + "\n".join(rows)
        + "\n- 完整性边界：只有同时提供并核验正文或数据内容时，引用标识才可作为实质证据。"
    )


def _substantive_task_text(packet: dict[str, Any]) -> str:
    """Build the model-facing task without execution/audit objective metadata."""
    task = packet.get("task") if isinstance(packet.get("task"), dict) else {}
    question = base._clean_string(task.get("question"))
    requirements = task.get("requirements") if isinstance(task.get("requirements"), list) else []
    text = base.DELEGATION_NOTICE
    if question:
        text += "\n\n" + question
    cleaned = [base._clean_string(item) for item in requirements if isinstance(item, str)]
    cleaned = [item for item in cleaned if item]
    if cleaned:
        text += "\n\n执行要求：\n" + "\n".join(f"- {item}" for item in cleaned)
    evidence = packet.get("evidence")
    text = base._append_evidence_metadata(text, evidence)
    return text + _structured_evidence_text(evidence)


def _active_lower_run_reason(repo: str, current_run_id: int) -> str:
    """Admit only the earliest active production Run; reject later Runs immediately."""
    if not repo or current_run_id <= 0 or not os.getenv("GITHUB_TOKEN"):
        return ""
    active: list[int] = []
    for status in ("in_progress", "queued"):
        url = (
            f"https://api.github.com/repos/{repo}/actions/workflows/execution-ticket.yml/runs"
            f"?status={status}&per_page=100"
        )
        payload = base._api_json(url)
        rows = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            run_id = int(row.get("id") or 0)
            if run_id and run_id != current_run_id:
                active.append(run_id)
    lower = sorted(run_id for run_id in active if run_id < current_run_id)
    if not lower:
        return ""
    return (
        f"EXECUTION_BUSY: earlier production Run {lower[0]} is queued or in progress; "
        "new tasks are rejected instead of silently queued"
    )


def _rewrite_outputs(status: dict[str, Any]) -> None:
    for key in (
        "accepted",
        "reason",
        "calls",
        "max_cost_usd",
        "cost_policy",
        "quality_tier",
        "maximum_replacements",
        "task_id",
        "task_fingerprint",
        "private_output",
        "is_retry",
        "retry_id",
        "analysis_owner",
    ):
        value = status.get(key, "")
        base._write_output(key, str(value).lower() if isinstance(value, bool) else value)


def _reject(status: dict[str, Any], reason: str) -> None:
    errors = list(status.get("errors") or [])
    if reason not in errors:
        errors.append(reason)
    status["errors"] = errors
    status["reason"] = "; ".join(errors)
    status["accepted"] = False


def prepare(args: argparse.Namespace) -> int:
    original_comments = base._issue_comments
    original_state = base._execution_state
    original_fingerprint = base.task_fingerprint
    base._issue_comments = _trusted_issue_comments
    base._execution_state = _execution_state
    base.task_fingerprint = _substantive_task_fingerprint
    try:
        result = base.prepare(args)
    finally:
        base._issue_comments = original_comments
        base._execution_state = original_state
        base.task_fingerprint = original_fingerprint

    root = Path(args.output_dir)
    status_path = root / "ticket-status.json"
    ticket_path = root / "ticket.json"
    if not status_path.exists() or not ticket_path.exists():
        return result

    status = json.loads(status_path.read_text(encoding="utf-8"))
    packet = json.loads(ticket_path.read_text(encoding="utf-8"))
    requested_private = packet.get("private_output") is True
    status["private_output"] = requested_private
    status["max_cost_usd"] = None
    status["cost_policy"] = "no-hard-monetary-ceiling"
    status["objective_delivery"] = "metadata-only"
    status["model_task_fields"] = ["task.question", "task.requirements", "evidence"]
    status["semantic_routing_default"] = "disabled"
    if status.get("accepted") is True:
        status["reason"] = "ticket, authorization, uniqueness, call ceiling, and no-limit cost policy accepted"
        (root / "task.txt").write_text(_substantive_task_text(packet), encoding="utf-8")
    if requested_private:
        status["errors"] = [
            item
            for item in list(status.get("errors") or [])
            if item != "private_output must be boolean."
        ]
        _reject(
            status,
            "private_output=true is unsupported: this repository and its Issue comments are public, "
            "and no private delivery channel is implemented",
        )

    if status.get("accepted") is True:
        busy = _active_lower_run_reason(
            os.getenv("GITHUB_REPOSITORY", ""),
            int(os.getenv("GITHUB_RUN_ID") or 0),
        )
        if busy:
            _reject(status, busy)

    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    _rewrite_outputs(status)
    return result


def render(args: argparse.Namespace) -> int:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        result = base.render(args)
    lines = []
    for line in buffer.getvalue().splitlines():
        if line.startswith("- 费用上限："):
            line = "- 费用上限：`无`"
        elif line.startswith("- 选模方式："):
            line = "- 选模方式：`稳定和能力硬门槛；通过后value档性价比优先；厂商独立`"
        elif line.startswith("- 推理参数："):
            line = "- 推理参数：`受控动态字段；生产统一low reasoning与low verbosity；不发送人为Token上限`"
        lines.append(line)
    if args.phase == "accepted":
        lines.append("- 语义路由：`默认关闭`")
        lines.append("- 公开回退：`完整裁判报告将分段发布到本Issue；Artifact下载失败时可直接读取评论`")
    print("\n".join(lines))
    return result


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
    render_parser.add_argument("--phase", choices=["accepted", "rejected", "success", "failure"], required=True)
    render_parser.add_argument("--output-dir", default="ticket-artifacts")
    render_parser.add_argument("--run-url", default="")
    render_parser.set_defaults(func=render)
    return root


if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.func(arguments))
