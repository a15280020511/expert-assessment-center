#!/usr/bin/env python3
"""Single deterministic V5 execution-ticket entrypoint.

This module owns schema validation, authorization, duplicate/retry protection,
command parsing, task projection, admission outputs, and admission receipts.
It does not monkey-patch imported modules and has no compatibility entrypoint.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from v5_ticket_identity import task_fingerprint

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "execution-ticket.schema.json"
V5_MINIMUM_MODEL_CALLS = 4
V5_MAXIMUM_MODEL_CALLS = 16
V5_MAXIMUM_RECOVERY_CALLS = 4
MAXIMUM_RETRIES_PER_ISSUE = 2
MAX_BODY_CHARS = 100_000
RUN_COMMAND_RE = re.compile(
    r"^/run-expert-team\s+([A-Za-z0-9][A-Za-z0-9._:-]{7,127})$"
)
RETRY_COMMAND_RE = re.compile(
    r"^/retry-expert-team\s+([A-Za-z0-9][A-Za-z0-9._:-]{7,63})$"
)
TRUSTED_STATE_ACTORS = {"github-actions[bot]"}
TRUSTED_STATE_PREFIXES = (
    "## EXECUTION_ACCEPTED",
    "## EXECUTION_RETRY_ACCEPTED",
    "## EXECUTION_COMPLETED",
    "## EXECUTION_DEGRADED",
    "## EXECUTION_FAILED",
    "## EXECUTION_REJECTED",
)
DELEGATION_NOTICE = (
    "委托边界：原始任务由GPT latest直接拆解并提出专家图；Claude Opus latest只执行一次红队审查并给出修改意见；"
    "GPT latest只综合一次；确定性宪法校验器是唯一硬门；通过后才由GitHub专家图执行。"
    "外部网页GPT只负责忠实提交、监控、取回和转述，不得在专家结果产生前替代专家分析。"
)


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is forbidden: {value}")


def _load_ticket_schema() -> dict[str, Any]:
    schema = json.loads(
        SCHEMA_PATH.read_text(encoding="utf-8"),
        parse_constant=_reject_constant,
    )
    Draft202012Validator.check_schema(schema)
    return schema


TICKET_SCHEMA = _load_ticket_schema()
TICKET_VALIDATOR = Draft202012Validator(TICKET_SCHEMA)


def _write_output(name: str, value: Any) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    text = str(value).replace("\n", " ").replace("\r", " ")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={text}\n")


def _read_event(path: str) -> tuple[str, str, int, str, str, str]:
    event = json.loads(
        Path(path).read_text(encoding="utf-8"),
        parse_constant=_reject_constant,
    )
    issue = event.get("issue") if isinstance(event.get("issue"), Mapping) else {}
    comment = (
        event.get("comment") if isinstance(event.get("comment"), Mapping) else {}
    )
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
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "expert-team-duplicate-guard",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _issue_comments(repo: str, issue_number: int) -> Iterable[str]:
    """Yield only bot-authored formal state comments."""
    for page in range(1, 6):
        url = (
            f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
            f"?per_page=100&page={page}"
        )
        rows = _api_json(url)
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
    bodies = list(comments)
    completed = any(body.startswith("## EXECUTION_COMPLETED") for body in bodies)
    degraded = any(body.startswith("## EXECUTION_DEGRADED") for body in bodies)
    failed = any(body.startswith("## EXECUTION_FAILED") for body in bodies)
    if degraded and not completed:
        failed = True
    return {
        "accepted": any(
            body.startswith(("## EXECUTION_ACCEPTED", "## EXECUTION_RETRY_ACCEPTED"))
            for body in bodies
        ),
        "completed": completed,
        "degraded": degraded,
        "failed": failed,
        "rejected": any(body.startswith("## EXECUTION_REJECTED") for body in bodies),
        "retry_count": sum(body.startswith("## EXECUTION_RETRY_ACCEPTED") for body in bodies),
        "retry_ids": {
            match.group(1)
            for body in bodies
            for match in [re.search(r"RETRY_ID:\s*`?([A-Za-z0-9._:-]+)`?", body)]
            if match
        },
    }


def _is_rejected_only(state: Mapping[str, Any]) -> bool:
    return bool(
        state.get("rejected")
        and not state.get("accepted")
        and not state.get("completed")
        and not state.get("failed")
    )


def _current_issue_submission_reason(
    state: Mapping[str, Any],
    *,
    is_retry: bool,
    retry_id: str,
) -> str:
    if not is_retry:
        if any(state.get(key) for key in ("accepted", "completed", "failed", "rejected")):
            return (
                "this Issue was already submitted; update the original Issue "
                "and use a controlled retry"
            )
        return ""
    if state["completed"]:
        return "this Issue already completed; successful tasks cannot be retried"
    if not (state["failed"] or state["rejected"]):
        return (
            "controlled retry is allowed only after an EXECUTION_FAILED, "
            "EXECUTION_DEGRADED, or EXECUTION_REJECTED result"
        )
    if state["retry_count"] >= MAXIMUM_RETRIES_PER_ISSUE:
        return (
            f"this Issue already used the maximum "
            f"{MAXIMUM_RETRIES_PER_ISSUE} controlled retries"
        )
    if retry_id in state["retry_ids"]:
        return f"retry_id {retry_id} was already used"
    return ""


def _execution_issue_rows(repo: str) -> Iterable[Mapping[str, Any]]:
    for page in range(1, 11):
        url = (
            f"https://api.github.com/repos/{repo}/issues"
            f"?state=all&per_page=100&page={page}"
        )
        rows = _api_json(url)
        if not isinstance(rows, list):
            return
        for row in rows:
            if isinstance(row, Mapping):
                yield row
        if len(rows) < 100:
            return


def _issue_ticket_packet(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    try:
        packet = json.loads(
            str(row.get("body") or ""),
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError):
        return None
    return packet if isinstance(packet, Mapping) else None


def _duplicate_row_reason(
    repo: str,
    row: Mapping[str, Any],
    *,
    current_issue: int,
    task_id: str,
    fingerprint: str,
) -> str:
    prior_issue = int(row.get("number") or 0)
    if (
        row.get("pull_request")
        or prior_issue == current_issue
        or not str(row.get("title") or "").startswith("[execution]")
    ):
        return ""
    packet = _issue_ticket_packet(row)
    if packet is None:
        return ""
    same_id = str(packet.get("task_id") or "") == task_id
    same_fingerprint = task_fingerprint(packet) == fingerprint
    if not (same_id or same_fingerprint):
        return ""
    prior_state = _execution_state(_issue_comments(repo, prior_issue))
    if _is_rejected_only(prior_state):
        return ""
    reason = "task_id" if same_id else "task fingerprint"
    return (
        f"duplicate {reason}; previously submitted in Issue #{prior_issue}; "
        "do not create a new Issue"
    )


def duplicate_reason(
    repo: str,
    current_issue: int,
    task_id: str,
    fingerprint: str,
    *,
    is_retry: bool,
    retry_id: str,
) -> str:
    if not repo or not os.getenv("GITHUB_TOKEN"):
        return ""
    current = _execution_state(_issue_comments(repo, current_issue))
    current_reason = _current_issue_submission_reason(
        current,
        is_retry=is_retry,
        retry_id=retry_id,
    )
    if current_reason:
        return current_reason
    for row in _execution_issue_rows(repo):
        reason = _duplicate_row_reason(
            repo,
            row,
            current_issue=current_issue,
            task_id=task_id,
            fingerprint=fingerprint,
        )
        if reason:
            return reason
    return ""


def _path_text(path: Iterable[Any]) -> str:
    result = ""
    for item in path:
        result += f"[{item}]" if isinstance(item, int) else (("." if result else "") + str(item))
    return result


def _unexpected_fields(error: ValidationError) -> list[str]:
    if not isinstance(error.instance, Mapping):
        return []
    properties = error.schema.get("properties") if isinstance(error.schema, Mapping) else None
    allowed = set(properties) if isinstance(properties, Mapping) else set()
    return sorted(set(error.instance) - allowed)


def _additional_properties_message(error: ValidationError, path: str) -> str:
    fields = _unexpected_fields(error)
    if not path:
        return f"Unknown ticket fields: {fields}"
    if path == "task":
        return f"Unknown task fields: {fields}"
    if path == "approved_budget":
        return (
            "approved_budget may contain only calls, maximum_recovery_calls, "
            "cost_policy, and optional cost_anomaly_usd."
        )
    if path == "evidence" or path.startswith("evidence["):
        return f"Unknown {path} fields: {fields}"
    return ""


def _required_fields_message(error: ValidationError, path: str) -> str:
    if not isinstance(error.instance, Mapping):
        return ""
    missing = [name for name in error.validator_value if name not in error.instance]
    return "; ".join(
        f"{path + '.' if path else ''}{name} is required." for name in missing
    )


def _fixed_schema_message(path: str, validator: str) -> str:
    messages = {
        ("route", "const"): "route must be expert-team.",
        ("task_id", "type"): "task_id must be 8-128 safe characters and start with a letter or number.",
        ("task_id", "minLength"): "task_id must be 8-128 safe characters and start with a letter or number.",
        ("task_id", "maxLength"): "task_id must be 8-128 safe characters and start with a letter or number.",
        ("task_id", "pattern"): "task_id must be 8-128 safe characters and start with a letter or number.",
        ("task.question", "minLength"): "task.question is required.",
        ("task", "type"): "task must be an object.",
        ("task.requirements", "type"): "task.requirements must be an array with at most 20 entries.",
        ("task.requirements", "maxItems"): "task.requirements must be an array with at most 20 entries.",
        ("evidence", "oneOf"): "evidence must be an object or an array.",
        ("evidence", "type"): "evidence must be an object or an array.",
        ("approved_budget", "type"): "approved_budget must be a V5 budget object.",
        ("approved_budget.cost_policy", "const"): "approved_budget.cost_policy must be unbounded_with_anomaly_guard.",
        ("approved_budget.cost_anomaly_usd", "type"): "approved_budget.cost_anomaly_usd must be a finite positive number at most 100.",
        ("approved_budget.cost_anomaly_usd", "exclusiveMinimum"): "approved_budget.cost_anomaly_usd must be a finite positive number at most 100.",
        ("approved_budget.cost_anomaly_usd", "maximum"): "approved_budget.cost_anomaly_usd must be a finite positive number at most 100.",
    }
    return messages.get((path, validator), "")


def _path_schema_message(error: ValidationError, path: str, validator: str) -> str:
    fixed = _fixed_schema_message(path, validator)
    if fixed:
        return fixed
    if path == "task.question":
        return "task.question must be a string with at most 20000 characters."
    if path.startswith("task.requirements["):
        return f"{path} must be a string with at most 2000 characters."
    if path == "approved_budget.calls":
        if validator == "type":
            return "approved_budget.calls must be an integer."
        return "approved_budget.calls must be between 4 and 16."
    if path == "approved_budget.maximum_recovery_calls":
        if validator == "type":
            return "approved_budget.maximum_recovery_calls must be an integer."
        return "approved_budget.maximum_recovery_calls must be between 0 and 4."
    if path == "private_output":
        if validator == "const" and isinstance(error.instance, bool):
            return (
                "private_output=true is unsupported: this repository and its Issue comments "
                "are public, and no private delivery channel is implemented"
            )
        return "private_output must be boolean."
    return ""


def _format_schema_error(error: ValidationError) -> str:
    path = _path_text(error.absolute_path)
    validator = str(error.validator)
    if validator == "additionalProperties":
        message = _additional_properties_message(error, path)
        if message:
            return message
    if validator == "required":
        message = _required_fields_message(error, path)
        if message:
            return message
    message = _path_schema_message(error, path, validator)
    return message or f"{path or 'ticket'}: {error.message}"


def _schema_errors(packet: Mapping[str, Any]) -> list[str]:
    errors = sorted(
        TICKET_VALIDATOR.iter_errors(packet),
        key=lambda error: (_path_text(error.absolute_path), str(error.validator), error.message),
    )
    result: list[str] = []
    for error in errors:
        message = _format_schema_error(error)
        if message not in result:
            result.append(message)
    return result


def _clean_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _structured_evidence_text(evidence: Any) -> str:
    if not isinstance(evidence, list):
        return ""
    rows: list[str] = []
    for index, item in enumerate(evidence, 1):
        if not isinstance(item, Mapping):
            continue
        fields: list[str] = []
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
        ):
            value = _clean_string(item.get(key))
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


def _substantive_task_text(packet: Mapping[str, Any]) -> str:
    task = packet.get("task") if isinstance(packet.get("task"), Mapping) else {}
    text = DELEGATION_NOTICE
    question = _clean_string(task.get("question"))
    if question:
        text += "\n\n" + question
    requirements = task.get("requirements") if isinstance(task.get("requirements"), list) else []
    cleaned = [_clean_string(item) for item in requirements if isinstance(item, str)]
    cleaned = [item for item in cleaned if item]
    if cleaned:
        text += "\n\n执行要求：\n" + "\n".join(f"- {item}" for item in cleaned)
    evidence = packet.get("evidence")
    if isinstance(evidence, Mapping):
        note = _clean_string(evidence.get("note"))
        if note:
            text += "\n\n用户提供的执行说明：\n" + note
    return text + _structured_evidence_text(evidence)


def _validate_ticket(packet: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors = _schema_errors(packet)
    task = packet.get("task") if isinstance(packet.get("task"), Mapping) else {}
    budget = packet.get("approved_budget") if isinstance(packet.get("approved_budget"), Mapping) else {}
    requirements = task.get("requirements") if isinstance(task.get("requirements"), list) else []
    raw_calls = budget.get("calls")
    calls = raw_calls if isinstance(raw_calls, int) and not isinstance(raw_calls, bool) else 0
    raw_recovery = budget.get("maximum_recovery_calls")
    recovery = raw_recovery if isinstance(raw_recovery, int) and not isinstance(raw_recovery, bool) else -1
    raw_anomaly = budget.get("cost_anomaly_usd")
    anomaly = float(raw_anomaly) if isinstance(raw_anomaly, (int, float)) and not isinstance(raw_anomaly, bool) else None
    return (
        {
            "task_id": _clean_string(packet.get("task_id")),
            "question": _clean_string(task.get("question")),
            "requirements": [_clean_string(item) for item in requirements if isinstance(item, str)],
            "language": _clean_string(task.get("language")),
            "calls": calls,
            "maximum_recovery_calls": recovery,
            "cost_policy": _clean_string(budget.get("cost_policy")),
            "cost_anomaly_usd": anomaly,
            "max_cost_usd": anomaly or 0.0,
        },
        errors,
    )


def _command(comment_body: str) -> tuple[str, str]:
    body = str(comment_body or "").strip()
    run_match = RUN_COMMAND_RE.fullmatch(body)
    if run_match:
        return "run", run_match.group(1)
    retry_match = RETRY_COMMAND_RE.fullmatch(body)
    if retry_match:
        return "retry", retry_match.group(1)
    if body.startswith("/run-expert-team"):
        raise ValueError("run command must be: /run-expert-team <ticket_task_id>")
    if body.startswith("/retry-expert-team"):
        raise ValueError("retry command must be: /retry-expert-team <unique_retry_id>")
    raise ValueError(
        "execution requires one explicit comment command: "
        "/run-expert-team <ticket_task_id> or /retry-expert-team <unique_retry_id>"
    )


def _active_lower_run_reason(repo: str, current_run_id: int) -> str:
    if not repo or current_run_id <= 0 or not os.getenv("GITHUB_TOKEN"):
        return ""
    active: list[int] = []
    for status in ("in_progress", "queued"):
        url = (
            f"https://api.github.com/repos/{repo}/actions/workflows/execution-ticket.yml/runs"
            f"?status={status}&per_page=100"
        )
        payload = _api_json(url)
        rows = payload.get("workflow_runs") if isinstance(payload, Mapping) else None
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
        f"EXECUTION_BUSY: earlier production Run {lower[0]} is queued or in progress; "
        "new tasks are rejected instead of silently queued"
        if lower
        else ""
    )


def _reject(status: dict[str, Any], reason: str) -> None:
    errors = list(status.get("errors") or [])
    if reason not in errors:
        errors.append(reason)
    status["errors"] = errors
    status["reason"] = "; ".join(errors)
    status["accepted"] = False


def _rewrite_outputs(status: Mapping[str, Any]) -> None:
    for key in (
        "accepted",
        "reason",
        "calls",
        "max_cost_usd",
        "cost_policy",
        "maximum_replacements",
        "maximum_recovery_calls",
        "maximum_initial_calls",
        "cost_anomaly_usd",
        "task_id",
        "task_fingerprint",
        "private_output",
        "is_retry",
        "retry_id",
        "trigger_mode",
        "execution_id",
        "analysis_owner",
    ):
        value = status.get(key, "")
        _write_output(key, str(value).lower() if isinstance(value, bool) else value)


def _prepare_context(args: argparse.Namespace) -> tuple[str, str, int, str, str, str]:
    if args.event_path:
        return _read_event(args.event_path)
    return (
        args.issue_title,
        args.issue_body,
        args.issue_number,
        args.actor or "",
        args.author_association or "",
        args.comment_body or "",
    )


def _base_ticket_status(
    title: str,
    issue_number: int,
    actor: str,
    association: str,
) -> dict[str, Any]:
    return {
        "accepted": False,
        "title": title,
        "issue_number": issue_number,
        "actor": actor,
        "author_association": association,
        "required_model_calls": V5_MINIMUM_MODEL_CALLS,
        "analysis_owner": "github-v5-gpt-claude-expert-graph",
        "runtime_version": "v5-native-runtime-1",
        "authoritative_trigger": "issue_comment.created",
        "legacy_runtime_present": False,
        "cross_task_history_used": False,
        "claude_red_team_calls": 1,
        "claude_is_advisory_only": True,
        "claude_gatekeeping_allowed": False,
    }


def _validate_trigger(
    *,
    title: str,
    body: str,
    actor: str,
    association: str,
) -> None:
    repository_owner = os.getenv("GITHUB_REPOSITORY_OWNER", "")
    if repository_owner and actor != repository_owner:
        raise ValueError("only the repository owner may trigger paid expert execution")
    if association and association != "OWNER":
        raise ValueError("trigger author_association must be OWNER")
    if not title.startswith("[execution]"):
        raise ValueError("Issue title must start with [execution].")
    if len(body) > MAX_BODY_CHARS:
        raise ValueError(f"Issue body exceeds {MAX_BODY_CHARS} characters.")


def _load_packet(body: str) -> Mapping[str, Any]:
    packet = json.loads(body, parse_constant=_reject_constant)
    if not isinstance(packet, Mapping):
        raise ValueError("Issue body must be one JSON object.")
    return packet


def _identity_reasons(
    packet: Mapping[str, Any],
    validated: Mapping[str, Any],
    *,
    command_mode: str,
    command_id: str,
    issue_number: int,
) -> tuple[list[str], str, bool, str]:
    reasons: list[str] = []
    task_id = str(validated["task_id"])
    is_retry = command_mode == "retry"
    retry_id = command_id if is_retry else ""
    if command_mode == "run" and command_id != task_id:
        reasons.append("run execution_id must exactly equal ticket task_id")
    fingerprint = (
        task_fingerprint(packet) if task_id and validated["question"] else ""
    )
    if fingerprint:
        duplicate = duplicate_reason(
            os.getenv("GITHUB_REPOSITORY", ""),
            issue_number,
            task_id,
            fingerprint,
            is_retry=is_retry,
            retry_id=retry_id,
        )
        if duplicate:
            reasons.append(duplicate)
    return reasons, fingerprint, is_retry, retry_id


def _budget_reasons(
    packet: Mapping[str, Any],
    validated: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    total_calls = int(validated["calls"])
    recovery_calls = int(validated["maximum_recovery_calls"])
    if not 4 <= total_calls <= V5_MAXIMUM_MODEL_CALLS:
        reasons.append("approved_budget.calls must be between 4 and 16")
    if not 0 <= recovery_calls <= V5_MAXIMUM_RECOVERY_CALLS:
        reasons.append("approved_budget.maximum_recovery_calls must be between 0 and 4")
    elif recovery_calls >= total_calls - 3:
        reasons.append(
            "approved recovery calls must leave at least one initial expert call "
            "after three governance calls"
        )
    if validated["cost_policy"] != "unbounded_with_anomaly_guard":
        reasons.append(
            "approved_budget.cost_policy must be unbounded_with_anomaly_guard"
        )
    if packet.get("private_output") is True:
        reasons.append(
            "private_output=true is unsupported: this repository and its Issue comments "
            "are public, and no private delivery channel is implemented"
        )
    return reasons


def _busy_reason() -> str:
    return _active_lower_run_reason(
        os.getenv("GITHUB_REPOSITORY", ""),
        int(os.getenv("GITHUB_RUN_ID") or 0),
    )


def _accepted_status_fields(
    packet: Mapping[str, Any],
    validated: Mapping[str, Any],
    *,
    reasons: list[str],
    fingerprint: str,
    is_retry: bool,
    retry_id: str,
    command_mode: str,
    command_id: str,
) -> dict[str, Any]:
    total_calls = int(validated["calls"])
    recovery_calls = int(validated["maximum_recovery_calls"])
    recovery_reserve = max(0, recovery_calls)
    return {
        "task_id": validated["task_id"],
        "task_fingerprint": fingerprint,
        "calls": total_calls,
        "maximum_recovery_calls": recovery_reserve,
        "maximum_replacements": recovery_reserve,
        "maximum_initial_calls": max(0, total_calls - 3 - recovery_reserve),
        "max_cost_usd": validated["cost_anomaly_usd"],
        "cost_anomaly_usd": validated["cost_anomaly_usd"],
        "cost_policy": validated["cost_policy"] or "invalid",
        "private_output": bool(packet.get("private_output", False)),
        "is_retry": is_retry,
        "retry_id": retry_id,
        "trigger_mode": command_mode,
        "execution_id": command_id if command_mode == "run" else "",
        "call_policy": (
            "approved-total-includes-gpt-claude-governance-experts-and-recovery"
        ),
        "fallback_policy": "disabled-fail-closed",
        "accepted": not reasons,
        "errors": reasons,
        "reason": (
            "; ".join(reasons)
            if reasons
            else "explicit command, ticket, authorization, uniqueness, governance reserve, "
            "expert reserve, recovery reserve, anomaly guard, and fail-closed policy accepted"
        ),
    }


def _write_ticket_artifacts(
    output: Path,
    packet: Mapping[str, Any],
    status: Mapping[str, Any],
) -> None:
    (output / "ticket.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if status["accepted"]:
        (output / "task.txt").write_text(
            _substantive_task_text(packet),
            encoding="utf-8",
        )


def _record_prepare_error(
    status: dict[str, Any],
    exc: Exception,
    *,
    command_mode: str,
    command_id: str,
) -> None:
    status["errors"] = [str(exc)]
    status["reason"] = str(exc)
    status["trigger_mode"] = command_mode
    status["execution_id"] = command_id if command_mode == "run" else ""
    status["retry_id"] = command_id if command_mode == "retry" else ""


def prepare(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    title, body, issue_number, actor, association, comment_body = _prepare_context(args)
    status = _base_ticket_status(title, issue_number, actor, association)
    command_mode = "invalid"
    command_id = ""
    try:
        command_mode, command_id = _command(comment_body)
        _validate_trigger(
            title=title,
            body=body,
            actor=actor,
            association=association,
        )
        packet = _load_packet(body)
        validated, reasons = _validate_ticket(packet)
        identity, fingerprint, is_retry, retry_id = _identity_reasons(
            packet,
            validated,
            command_mode=command_mode,
            command_id=command_id,
            issue_number=issue_number,
        )
        reasons.extend(identity)
        reasons.extend(_budget_reasons(packet, validated))
        busy = _busy_reason()
        if busy:
            reasons.append(busy)
        reasons = list(dict.fromkeys(reasons))
        status.update(
            _accepted_status_fields(
                packet,
                validated,
                reasons=reasons,
                fingerprint=fingerprint,
                is_retry=is_retry,
                retry_id=retry_id,
                command_mode=command_mode,
                command_id=command_id,
            )
        )
        _write_ticket_artifacts(output, packet, status)
    except (ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
        _record_prepare_error(
            status,
            exc,
            command_mode=command_mode,
            command_id=command_id,
        )
    (output / "ticket-status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _rewrite_outputs(status)
    return 0


def render(args: argparse.Namespace) -> int:
    root = Path(args.output_dir)
    status = json.loads((root / "ticket-status.json").read_text(encoding="utf-8"))
    run_line = f"- Run: `{args.run_url}`\n" if args.run_url else ""
    if args.phase == "accepted":
        heading = "EXECUTION_RETRY_ACCEPTED" if status.get("is_retry") else "EXECUTION_ACCEPTED"
        identity = (
            f"- RETRY_ID: `{status.get('retry_id')}`\n"
            if status.get("is_retry")
            else f"- EXECUTION_ID: `{status.get('execution_id')}`\n"
        )
        anomaly = status.get("cost_anomaly_usd")
        anomaly_text = f"`${anomaly}`" if anomaly is not None else "`账户级与估算偏差守卫；无固定美元目标`"
        text = (
            f"## {heading}\n\n"
            "GitHub Issue Runner 已接收唯一 V5 专家任务。\n\n"
            f"- Task ID：`{status.get('task_id')}`\n"
            f"- TASK_FINGERPRINT: `{status.get('task_fingerprint')}`\n"
            + identity
            + "- 治理链：`GPT latest一次提案 → Claude Opus latest一次统一红队建议 → GPT latest一次综合`\n"
            + "- Claude权限：`只给修改建议；不是批准者、否决者或门禁；禁止第二次复审`\n"
            + "- 唯一硬门：`确定性宪法校验器`\n"
            + "- 专家编组：`GPT直接依据原始任务、硬约束和精确model+provider目录生成；本地不拆任务、不评分、不选模`\n"
            + f"- 付费调用总硬上限：`{status.get('calls')}`（治理、专家和恢复合计）\n"
            + f"- 专家初始调用上限：`{status.get('maximum_initial_calls')}`\n"
            + f"- 恢复调用保留：`{status.get('maximum_recovery_calls')}`\n"
            + f"- 费用异常停止阈值：{anomaly_text}\n"
            + "- 专家外部工具：`禁止`\n"
            + "- Provider：`精确单锁；禁止fallback`\n"
            + "- 跨任务历史：`不读取、不保存、不参与编组`\n"
            + "- 失败策略：`失败关闭；不调用旧运行时`\n"
            + run_line
        )
    elif args.phase == "rejected":
        text = (
            "## EXECUTION_REJECTED\n\n"
            f"票据未进入模型调用阶段：{status.get('reason', 'unknown')}。\n\n"
            "模型调用：`0`。首次执行请评论：`/run-expert-team <ticket_task_id>`；"
            "受控重试请评论：`/retry-expert-team <唯一retry_id>`。\n"
            + run_line
        )
    else:
        text = (
            "## EXECUTION_FAILED\n\n"
            + run_line
            + "最终状态由V5独立审计、主Artifact和最终attestation工作流发布。\n"
        )
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
