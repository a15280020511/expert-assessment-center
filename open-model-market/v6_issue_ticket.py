#!/usr/bin/env python3
"""Deterministic Issue admission for the governance-signed V6 expert team."""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator

from v5_no_tools_policy import assert_allowed_control_plane_url
from v5_ticket_identity import task_fingerprint
from v6_governed_roster import GovernedRosterError, validate_governed_ticket

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "execution-ticket.schema.json"
MINIMUM_MODEL_CALLS = 2
MAXIMUM_MODEL_CALLS = 12
MAXIMUM_RECOVERY_CALLS = 4
MAXIMUM_RETRIES_PER_ISSUE = 2
MAX_BODY_CHARS = 150_000
RUN_COMMAND_RE = re.compile(r"^/run-expert-team\s+([A-Za-z0-9][A-Za-z0-9._:-]{7,127})$")
RETRY_COMMAND_RE = re.compile(r"^/retry-expert-team\s+([A-Za-z0-9][A-Za-z0-9._:-]{7,63})$")
TRUSTED_STATE_ACTORS = {"github-actions[bot]"}
TRUSTED_STATE_PREFIXES = (
    "## EXECUTION_ACCEPTED",
    "## EXECUTION_RETRY_ACCEPTED",
    "## EXECUTION_COMPLETED",
    "## EXECUTION_FAILED",
    "## EXECUTION_REJECTED",
)
DELEGATION_NOTICE = (
    "委托边界：网页GPT只负责把任务拆成显式工作项；治理中心按实际任务Token成本从低到高"
    "选择不同公司的付费通用旗舰模型并签发不可变名单；专家中心不得改选模型，只用NetworkX"
    "校验与组织DAG、解析名单模型的最低成本ZDR精确端点并执行。Claude红队机制、GPT规划调用、"
    "模型循环、工具调用、Provider fallback、跨任务历史和业务中心直连全部禁止。"
)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _schema() -> dict[str, Any]:
    value = json.loads(SCHEMA_PATH.read_text("utf-8"), parse_constant=_reject_constant)
    Draft202012Validator.check_schema(value)
    return value


VALIDATOR = Draft202012Validator(_schema())


def _write_output(name: str, value: Any) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    text = str(value).replace("\n", " ").replace("\r", " ")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={text}\n")


def _read_event(path: str) -> tuple[str, str, int, str, str, str]:
    event = json.loads(Path(path).read_text("utf-8"), parse_constant=_reject_constant)
    issue = event.get("issue") if isinstance(event.get("issue"), Mapping) else {}
    comment = event.get("comment") if isinstance(event.get("comment"), Mapping) else {}
    actor_source = comment.get("user") or issue.get("user") or {}
    actor = str(actor_source.get("login") or "") if isinstance(actor_source, Mapping) else ""
    return (
        str(issue.get("title") or ""),
        str(issue.get("body") or ""),
        int(issue.get("number") or 0),
        actor,
        str(comment.get("author_association") or issue.get("author_association") or ""),
        str(comment.get("body") or "").strip(),
    )


def _api_json(url: str) -> Any:
    assert_allowed_control_plane_url(url)
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "expert-v6-duplicate-guard",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _issue_comments(repo: str, issue_number: int) -> Iterable[str]:
    for page in range(1, 6):
        rows = _api_json(
            f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments?per_page=100&page={page}"
        )
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            user = row.get("user") if isinstance(row.get("user"), Mapping) else {}
            if str(user.get("login") or "") not in TRUSTED_STATE_ACTORS:
                continue
            body = str(row.get("body") or "").strip()
            if body.startswith(TRUSTED_STATE_PREFIXES):
                yield body
        if len(rows) < 100:
            return


def _execution_state(comments: Iterable[str]) -> dict[str, Any]:
    rows = list(comments)
    return {
        "accepted": any(body.startswith(("## EXECUTION_ACCEPTED", "## EXECUTION_RETRY_ACCEPTED")) for body in rows),
        "completed": any(body.startswith("## EXECUTION_COMPLETED") for body in rows),
        "failed": any(body.startswith("## EXECUTION_FAILED") for body in rows),
        "rejected": any(body.startswith("## EXECUTION_REJECTED") for body in rows),
        "retry_count": sum(body.startswith("## EXECUTION_RETRY_ACCEPTED") for body in rows),
        "retry_ids": {
            match.group(1)
            for body in rows
            for match in [re.search(r"RETRY_ID:\s*`?([A-Za-z0-9._:-]+)`?", body)]
            if match
        },
    }


def _current_submission_reason(state: Mapping[str, Any], *, is_retry: bool, retry_id: str) -> str:
    if not is_retry:
        if any(state.get(key) for key in ("accepted", "completed", "failed", "rejected")):
            return "this Issue was already submitted; use a controlled retry after a failed or rejected execution"
        return ""
    if state.get("completed"):
        return "this Issue already completed; successful tasks cannot be retried"
    if not (state.get("failed") or state.get("rejected")):
        return "controlled retry is allowed only after EXECUTION_FAILED or EXECUTION_REJECTED"
    if int(state.get("retry_count") or 0) >= MAXIMUM_RETRIES_PER_ISSUE:
        return f"this Issue already used the maximum {MAXIMUM_RETRIES_PER_ISSUE} controlled retries"
    if retry_id in set(state.get("retry_ids") or set()):
        return f"retry_id {retry_id} was already used"
    return ""


def _execution_issues(repo: str) -> Iterable[Mapping[str, Any]]:
    for page in range(1, 11):
        rows = _api_json(
            f"https://api.github.com/repos/{repo}/issues?state=all&per_page=100&page={page}"
        )
        if not isinstance(rows, list):
            return
        for row in rows:
            if isinstance(row, Mapping):
                yield row
        if len(rows) < 100:
            return


def _packet_from_issue(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    try:
        packet = json.loads(str(row.get("body") or ""), parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError):
        return None
    return packet if isinstance(packet, Mapping) else None


def _duplicate_reason(
    repo: str,
    current_issue: int,
    packet: Mapping[str, Any],
    *,
    is_retry: bool,
    retry_id: str,
) -> str:
    if not repo or not os.getenv("GITHUB_TOKEN"):
        return ""
    state = _execution_state(_issue_comments(repo, current_issue))
    reason = _current_submission_reason(state, is_retry=is_retry, retry_id=retry_id)
    if reason:
        return reason
    task_id = str(packet.get("task_id") or "")
    fingerprint = task_fingerprint(packet)
    for row in _execution_issues(repo):
        prior_issue = int(row.get("number") or 0)
        if row.get("pull_request") or prior_issue == current_issue:
            continue
        if not str(row.get("title") or "").startswith("[execution]"):
            continue
        prior = _packet_from_issue(row)
        if prior is None:
            continue
        same_id = str(prior.get("task_id") or "") == task_id
        same_fingerprint = task_fingerprint(prior) == fingerprint
        if not (same_id or same_fingerprint):
            continue
        prior_state = _execution_state(_issue_comments(repo, prior_issue))
        rejected_only = prior_state["rejected"] and not any(
            prior_state[key] for key in ("accepted", "completed", "failed")
        )
        if rejected_only:
            continue
        duplicate_type = "task_id" if same_id else "task fingerprint"
        return f"duplicate {duplicate_type}; previously submitted in Issue #{prior_issue}"
    return ""


def _command(body: str) -> tuple[str, str]:
    body = str(body or "").strip()
    match = RUN_COMMAND_RE.fullmatch(body)
    if match:
        return "run", match.group(1)
    match = RETRY_COMMAND_RE.fullmatch(body)
    if match:
        return "retry", match.group(1)
    raise ValueError(
        "execution requires exactly /run-expert-team <ticket_task_id> or "
        "/retry-expert-team <unique_retry_id>"
    )


def _validate_trigger(title: str, body: str, actor: str, association: str) -> None:
    owner = os.getenv("GITHUB_REPOSITORY_OWNER", "")
    if owner and actor != owner:
        raise ValueError("only the repository owner may trigger paid expert execution")
    if association and association != "OWNER":
        raise ValueError("trigger author_association must be OWNER")
    if not title.startswith("[execution]"):
        raise ValueError("Issue title must start with [execution]")
    if len(body) > MAX_BODY_CHARS:
        raise ValueError(f"Issue body exceeds {MAX_BODY_CHARS} characters")


def _load_packet(body: str) -> Mapping[str, Any]:
    packet = json.loads(body, parse_constant=_reject_constant)
    if not isinstance(packet, Mapping):
        raise ValueError("Issue body must be one JSON object")
    return packet


def _schema_errors(packet: Mapping[str, Any]) -> list[str]:
    errors = sorted(
        VALIDATOR.iter_errors(packet),
        key=lambda error: (list(error.absolute_path), str(error.validator), error.message),
    )
    return [
        f"{'.'.join(str(value) for value in error.absolute_path) or 'ticket'}: {error.message}"
        for error in errors
    ]


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _structured_evidence_text(evidence: Any) -> str:
    if not isinstance(evidence, list):
        return ""
    rows: list[str] = []
    for index, item in enumerate(evidence, 1):
        if not isinstance(item, Mapping):
            continue
        fields = [
            f"{label}={_clean(item.get(key))}"
            for label, key in (
                ("级别", "source_level"),
                ("来源", "source"),
                ("URL", "url"),
                ("中心", "center"),
                ("Run", "run_id"),
                ("Artifact", "artifact_id"),
                ("文件", "file"),
                ("SHA256", "sha256"),
                ("观测时间", "observed_at"),
                ("说明", "note"),
            )
            if _clean(item.get(key))
        ]
        if fields:
            rows.append(f"- [{index}] " + "；".join(fields))
    return (
        "\n\n结构化证据引用（专家不得自行下载或联网）：\n"
        + "\n".join(rows)
        + "\n- 只有已核验并随票据提供的正文或数据内容才是实质证据。"
        if rows
        else ""
    )


def _substantive_task_text(packet: Mapping[str, Any]) -> str:
    task = packet.get("task") if isinstance(packet.get("task"), Mapping) else {}
    text = DELEGATION_NOTICE
    question = _clean(task.get("question"))
    if question:
        text += "\n\n" + question
    requirements = task.get("requirements") if isinstance(task.get("requirements"), list) else []
    rows = [_clean(value) for value in requirements if _clean(value)]
    if rows:
        text += "\n\n执行要求：\n" + "\n".join(f"- {row}" for row in rows)
    evidence = packet.get("evidence")
    if isinstance(evidence, Mapping) and _clean(evidence.get("note")):
        text += "\n\n用户提供的执行说明：\n" + _clean(evidence.get("note"))
    return text + _structured_evidence_text(evidence)


def _active_lower_run_reason(repo: str, current_run_id: int) -> str:
    if not repo or current_run_id <= 0 or not os.getenv("GITHUB_TOKEN"):
        return ""
    active: list[int] = []
    for status in ("in_progress", "queued"):
        payload = _api_json(
            f"https://api.github.com/repos/{repo}/actions/workflows/execution-ticket.yml/runs?status={status}&per_page=100"
        )
        rows = payload.get("workflow_runs") if isinstance(payload, Mapping) else []
        if not isinstance(rows, list):
            continue
        active.extend(
            int(row.get("id") or 0)
            for row in rows
            if isinstance(row, Mapping)
            and int(row.get("id") or 0) not in {0, current_run_id}
        )
    lower = sorted(run_id for run_id in active if run_id < current_run_id)
    return (
        f"EXECUTION_BUSY: earlier production Run {lower[0]} is queued or in progress"
        if lower
        else ""
    )


def _base_status(title: str, issue_number: int, actor: str, association: str) -> dict[str, Any]:
    return {
        "accepted": False,
        "title": title,
        "issue_number": issue_number,
        "actor": actor,
        "author_association": association,
        "required_model_calls": MINIMUM_MODEL_CALLS,
        "analysis_owner": "governance-signed-v6-networkx-expert-team",
        "runtime_version": "v6-governed-roster-networkx-1",
        "authoritative_trigger": "issue_comment.created",
        "cross_task_history_used": False,
        "claude_mechanism_enabled": False,
        "claude_calls": 0,
        "gpt_planning_calls": 0,
        "gpt_synthesis_calls": 0,
    }


def _write_outputs(status: Mapping[str, Any]) -> None:
    for key in (
        "accepted", "reason", "calls", "cost_policy", "maximum_replacements",
        "maximum_recovery_calls", "maximum_initial_calls", "cost_anomaly_usd",
        "task_id", "task_fingerprint", "private_output", "is_retry", "retry_id",
        "trigger_mode", "execution_id", "analysis_owner", "governance_roster_sha256",
        "governance_commit_sha", "team_size", "claude_calls",
    ):
        value = status.get(key, "")
        _write_output(key, str(value).lower() if isinstance(value, bool) else value)


def _write_artifacts(output: Path, packet: Mapping[str, Any], status: Mapping[str, Any]) -> None:
    (output / "ticket.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    if status.get("accepted") is True:
        (output / "task.txt").write_text(_substantive_task_text(packet), "utf-8")


def prepare(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if args.event_path:
        title, body, issue_number, actor, association, comment_body = _read_event(args.event_path)
    else:
        title, body, issue_number, actor, association, comment_body = (
            args.issue_title, args.issue_body, args.issue_number, args.actor,
            args.author_association, args.comment_body,
        )
    status = _base_status(title, issue_number, actor, association)
    command_mode = "invalid"
    command_id = ""
    try:
        command_mode, command_id = _command(comment_body)
        _validate_trigger(title, body, actor, association)
        packet = _load_packet(body)
        reasons = _schema_errors(packet)
        validation: Mapping[str, Any] | None = None
        if not reasons:
            try:
                validation = validate_governed_ticket(packet)
            except GovernedRosterError as exc:
                reasons.append(str(exc))
        task_id = str(packet.get("task_id") or "")
        if command_mode == "run" and command_id != task_id:
            reasons.append("run execution_id must exactly equal ticket task_id")
        is_retry = command_mode == "retry"
        retry_id = command_id if is_retry else ""
        duplicate = _duplicate_reason(
            os.getenv("GITHUB_REPOSITORY", ""),
            issue_number,
            packet,
            is_retry=is_retry,
            retry_id=retry_id,
        )
        if duplicate:
            reasons.append(duplicate)
        busy = _active_lower_run_reason(
            os.getenv("GITHUB_REPOSITORY", ""), int(os.getenv("GITHUB_RUN_ID") or 0)
        )
        if busy:
            reasons.append(busy)
        budget = packet.get("approved_budget") if isinstance(packet.get("approved_budget"), Mapping) else {}
        calls = int(budget.get("calls") or 0)
        recovery = int(budget.get("maximum_recovery_calls") or 0)
        if not MINIMUM_MODEL_CALLS <= calls <= MAXIMUM_MODEL_CALLS:
            reasons.append("approved_budget.calls must be between 2 and 12")
        if not 0 <= recovery <= MAXIMUM_RECOVERY_CALLS:
            reasons.append("approved_budget.maximum_recovery_calls must be between 0 and 4")
        if recovery >= calls:
            reasons.append("recovery reserve must leave at least one primary expert call")
        if packet.get("private_output") is True:
            reasons.append("private_output=true is unsupported in this public repository")
        reasons = list(dict.fromkeys(reasons))
        roster = packet.get("governance_roster") if isinstance(packet.get("governance_roster"), Mapping) else {}
        status.update(
            {
                "task_id": task_id,
                "task_fingerprint": task_fingerprint(packet),
                "calls": calls,
                "maximum_recovery_calls": recovery,
                "maximum_replacements": recovery,
                "maximum_initial_calls": max(0, calls - recovery),
                "cost_anomaly_usd": budget.get("cost_anomaly_usd"),
                "cost_policy": str(budget.get("cost_policy") or ""),
                "private_output": bool(packet.get("private_output", False)),
                "is_retry": is_retry,
                "retry_id": retry_id,
                "trigger_mode": command_mode,
                "execution_id": command_id if command_mode == "run" else "",
                "governance_roster_sha256": roster.get("roster_sha256"),
                "governance_commit_sha": roster.get("governance_commit_sha"),
                "team_size": roster.get("team_size"),
                "call_policy": "primary-work-items-plus-preapproved-distinct-company-recovery",
                "fallback_policy": "exact-provider-no-fallback-no-unlisted-model",
                "accepted": not reasons,
                "errors": reasons,
                "reason": "; ".join(reasons) if reasons else (
                    "governance roster, budget, company uniqueness, DAG, command, authorization, "
                    "duplicate protection, and fail-closed policy accepted"
                ),
            }
        )
        if validation is not None:
            status["validated_runtime_version"] = validation["runtime_version"]
        _write_artifacts(output, packet, status)
    except (ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
        status.update(
            {
                "errors": [str(exc)],
                "reason": str(exc),
                "trigger_mode": command_mode,
                "execution_id": command_id if command_mode == "run" else "",
                "retry_id": command_id if command_mode == "retry" else "",
            }
        )
    (output / "ticket-status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    _write_outputs(status)
    return 0


def render(args: argparse.Namespace) -> int:
    root = Path(args.output_dir)
    status = json.loads((root / "ticket-status.json").read_text("utf-8"))
    run_line = f"- Run: `{args.run_url}`\n" if args.run_url else ""
    if args.phase == "accepted":
        heading = "EXECUTION_RETRY_ACCEPTED" if status.get("is_retry") else "EXECUTION_ACCEPTED"
        identity = (
            f"- RETRY_ID: `{status.get('retry_id')}`\n"
            if status.get("is_retry")
            else f"- EXECUTION_ID: `{status.get('execution_id')}`\n"
        )
        text = (
            f"## {heading}\n\nGitHub Runner 已接收治理签名的 V6 专家任务。\n\n"
            f"- Task ID：`{status.get('task_id')}`\n"
            f"- TASK_FINGERPRINT: `{status.get('task_fingerprint')}`\n"
            + identity
            + f"- Governance roster SHA256：`{status.get('governance_roster_sha256')}`\n"
            + f"- Governance commit：`{status.get('governance_commit_sha')}`\n"
            + f"- 专家初始模型：`{status.get('maximum_initial_calls')}`个不同公司旗舰模型\n"
            + f"- 预批准恢复模型：`{status.get('maximum_recovery_calls')}`个不同公司旗舰模型\n"
            + f"- 付费调用总硬上限：`{status.get('calls')}`\n"
            + "- 模型选择：`治理中心按实际任务成本从低到高确定；专家中心禁止改选`\n"
            + "- 组织方式：`NetworkX DAG + topological_generations`\n"
            + "- 最终报告：`治理名单指定的最终综合工作模型完成；无额外裁判调用`\n"
            + "- Claude机制：`已取消；调用数0`\n"
            + "- GPT规划/综合调用：`0`\n"
            + "- 专家外部工具：`禁止`\n"
            + "- Provider：`ZDR精确单锁；禁止fallback`\n"
            + "- 失败策略：`失败关闭；只允许名单内预批准恢复模型`\n"
            + run_line
        )
    elif args.phase == "rejected":
        text = (
            "## EXECUTION_REJECTED\n\n"
            f"票据未进入模型调用阶段：{status.get('reason', 'unknown')}。\n\n"
            "模型调用：`0`。\n" + run_line
        )
    else:
        text = "## EXECUTION_FAILED\n\n" + run_line + "最终状态由V6独立审计和Artifact证据链发布。\n"
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
    render_parser.add_argument("--phase", choices=["accepted", "rejected", "success", "failure"], required=True)
    render_parser.add_argument("--output-dir", default="ticket-artifacts")
    render_parser.add_argument("--run-url", default="")
    render_parser.set_defaults(func=render)
    return root


if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.func(arguments))
