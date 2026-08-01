#!/usr/bin/env python3
"""Validate execution Issues, block duplicates, and allow controlled retries."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Tuple

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "execution-ticket.schema.json"
MINIMUM_MODEL_CALLS = 4
MAXIMUM_MODEL_CALLS = 6
MAXIMUM_REPLACEMENTS = 2
MAXIMUM_RETRIES_PER_ISSUE = 2
MAX_BODY_CHARS = 100_000
RETRY_COMMAND_RE = re.compile(r"^/retry-expert-team\s+([A-Za-z0-9][A-Za-z0-9._:-]{7,63})$")

DELEGATION_NOTICE = (
    "委托边界：本任务的分析、推断与最终结论必须由 GitHub 专家团及裁判生成。"
    "外部网页 GPT 只负责忠实提交用户问题和证据、报告运行状态、取回并转述 GitHub 产物；"
    "不得在专家结果产生前自行补充、替代或冒充专家分析。"
)


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is forbidden: {value}")


def _load_ticket_schema() -> Dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"), parse_constant=_reject_constant)
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


def _read_event(path: str) -> Tuple[str, str, int, str, str, str]:
    event = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_constant)
    issue = event.get("issue") if isinstance(event.get("issue"), dict) else {}
    comment = event.get("comment") if isinstance(event.get("comment"), dict) else {}
    actor = str((comment.get("user") or issue.get("user") or {}).get("login") or "")
    association = str(comment.get("author_association") or issue.get("author_association") or "")
    return (
        str(issue.get("title") or ""),
        str(issue.get("body") or ""),
        int(issue.get("number") or 0),
        actor,
        association,
        str(comment.get("body") or "").strip(),
    )


def _normalize_semantic_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[\s\u3000]+", " ", text)
    text = re.sub(r"[，。！？；：、,.!?;:]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _canonical_task(packet: Dict[str, Any]) -> Dict[str, Any]:
    task = packet.get("task") if isinstance(packet.get("task"), dict) else {}
    return {
        "objective": _normalize_semantic_text(packet.get("objective")),
        "question": _normalize_semantic_text(task.get("question")),
    }


def task_fingerprint(packet: Dict[str, Any]) -> str:
    raw = json.dumps(_canonical_task(packet), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _api_json(url: str) -> Any:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "expert-team-duplicate-guard"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _issue_comments(repo: str, issue_number: int) -> Iterable[str]:
    page = 1
    while page <= 5:
        url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments?per_page=100&page={page}"
        rows = _api_json(url)
        if not isinstance(rows, list):
            return
        for row in rows:
            if isinstance(row, dict):
                yield str(row.get("body") or "")
        if len(rows) < 100:
            return
        page += 1


def _execution_state(comments: Iterable[str]) -> Dict[str, Any]:
    bodies = list(comments)
    return {
        "accepted": any("EXECUTION_ACCEPTED" in body or "EXECUTION_RETRY_ACCEPTED" in body for body in bodies),
        "completed": any("EXECUTION_COMPLETED" in body for body in bodies),
        "failed": any("EXECUTION_FAILED" in body for body in bodies),
        "rejected": any("EXECUTION_REJECTED" in body for body in bodies),
        "retry_count": sum("EXECUTION_RETRY_ACCEPTED" in body for body in bodies),
        "retry_ids": {
            match.group(1)
            for body in bodies
            for match in [re.search(r"RETRY_ID:\s*`?([A-Za-z0-9._:-]+)`?", body)]
            if match
        },
    }


def _parse_retry(comment_body: str) -> Tuple[bool, str]:
    if not comment_body:
        return False, ""
    match = RETRY_COMMAND_RE.fullmatch(comment_body)
    if match:
        return True, match.group(1)
    if comment_body.startswith("/retry-expert-team"):
        raise ValueError("retry command must be: /retry-expert-team <unique_retry_id>")
    return False, ""


def _is_rejected_only(state: Dict[str, Any]) -> bool:
    return bool(
        state.get("rejected")
        and not state.get("accepted")
        and not state.get("completed")
        and not state.get("failed")
    )


def duplicate_reason(
    repo: str,
    current_issue: int,
    task_id: str,
    fingerprint: str,
    *,
    is_retry: bool,
    retry_id: str,
    comments_reader: Callable[[str, int], Iterable[str]] = _issue_comments,
    state_evaluator: Callable[[Iterable[str]], Dict[str, Any]] = _execution_state,
    fingerprint_fn: Callable[[Dict[str, Any]], str] = task_fingerprint,
) -> str:
    if not repo or not os.getenv("GITHUB_TOKEN"):
        return ""

    current = state_evaluator(comments_reader(repo, current_issue))
    if is_retry:
        if current["completed"]:
            return "this Issue already completed; successful tasks cannot be retried"
        if not (current["failed"] or current["rejected"]):
            return "controlled retry is allowed only after an EXECUTION_FAILED or EXECUTION_REJECTED result"
        if current["retry_count"] >= MAXIMUM_RETRIES_PER_ISSUE:
            return f"this Issue already used the maximum {MAXIMUM_RETRIES_PER_ISSUE} controlled retries"
        if retry_id in current["retry_ids"]:
            return f"retry_id {retry_id} was already used"
    elif current["accepted"] or current["completed"] or current["failed"] or current["rejected"]:
        return "this Issue was already submitted; update the original Issue and use a controlled retry"

    page = 1
    while page <= 10:
        url = f"https://api.github.com/repos/{repo}/issues?state=all&per_page=100&page={page}"
        rows = _api_json(url)
        if not isinstance(rows, list):
            break
        for row in rows:
            if not isinstance(row, dict) or row.get("pull_request") or int(row.get("number") or 0) == current_issue:
                continue
            if not str(row.get("title") or "").startswith("[execution]"):
                continue
            body = str(row.get("body") or "")
            try:
                packet = json.loads(body, parse_constant=_reject_constant)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(packet, dict):
                continue
            same_id = str(packet.get("task_id") or "") == task_id
            same_fingerprint = fingerprint_fn(packet) == fingerprint
            if not (same_id or same_fingerprint):
                continue

            prior_issue = int(row.get("number") or 0)
            prior_state = state_evaluator(comments_reader(repo, prior_issue))
            if _is_rejected_only(prior_state):
                continue

            reason = "task_id" if same_id else "task fingerprint"
            return f"duplicate {reason}; previously submitted in Issue #{prior_issue}; do not create a new Issue"
        if len(rows) < 100:
            break
        page += 1
    return ""


def _path_text(path: Iterable[Any]) -> str:
    result = ""
    for item in path:
        if isinstance(item, int):
            result += f"[{item}]"
        else:
            result += ("." if result else "") + str(item)
    return result


def _unexpected_fields(error: ValidationError) -> List[str]:
    if not isinstance(error.instance, Mapping):
        return []
    properties = error.schema.get("properties") if isinstance(error.schema, Mapping) else None
    allowed = set(properties) if isinstance(properties, Mapping) else set()
    return sorted(set(error.instance) - allowed)


def _format_schema_error(error: ValidationError) -> str:
    path = _path_text(error.absolute_path)
    validator = error.validator

    if validator == "additionalProperties":
        fields = _unexpected_fields(error)
        if path == "":
            return f"Unknown ticket fields: {fields}"
        if path == "task":
            return f"Unknown task fields: {fields}"
        if path == "approved_budget":
            return "approved_budget must contain only calls and max_cost_usd."
        if path == "evidence":
            return f"Unknown evidence fields: {fields}"
        if path.startswith("evidence["):
            return f"Unknown {path} fields: {fields}"

    if validator == "required":
        missing = [name for name in error.validator_value if name not in error.instance]
        target = missing[0] if missing else "required field"
        return f"{path + '.' if path else ''}{target} is required."

    if path == "route" and validator == "const":
        return "route must be expert-team."
    if path == "task_id" and validator in {"minLength", "maxLength", "pattern", "type"}:
        return "task_id must be 8-128 safe characters and start with a letter or number."
    if path == "task.question" and validator in {"minLength", "maxLength", "type"}:
        if validator == "minLength":
            return "task.question is required."
        return "task.question must be a string with at most 20000 characters."
    if path == "task" and validator == "type":
        return "task must be an object."
    if path == "task.requirements" and validator in {"type", "maxItems"}:
        return "task.requirements must be an array with at most 20 entries."
    if path.startswith("task.requirements["):
        return f"{path} must be a string with at most 2000 characters."
    if path == "task.language":
        return "task.language must be a string with at most 64 characters."
    if path == "objective":
        return "objective must be a string with at most 5000 characters."
    if path == "evidence" and validator == "oneOf":
        return "evidence must be an object or an array."
    if path == "approved_budget" and validator == "type":
        return "approved_budget must be an object containing only calls and max_cost_usd."
    if path == "approved_budget.calls":
        if validator == "type":
            return "approved_budget.calls must be an integer."
        return f"approved_budget.calls must be between {MINIMUM_MODEL_CALLS} and {MAXIMUM_MODEL_CALLS}"
    if path == "approved_budget.max_cost_usd":
        if validator == "type":
            return "approved_budget.max_cost_usd must be a finite number."
        return "approved_budget.max_cost_usd must be greater than 0 and at most 100.0"
    if path == "quality_tier":
        return "quality_tier must be budget, value, or quality."
    if path == "private_output":
        return "private_output must be boolean."

    label = path or "ticket"
    return f"{label}: {error.message}"


def _schema_errors(
    packet: Dict[str, Any],
    formatter: Callable[[ValidationError], str] = _format_schema_error,
) -> List[str]:
    errors = sorted(
        TICKET_VALIDATOR.iter_errors(packet),
        key=lambda error: (_path_text(error.absolute_path), str(error.validator), error.message),
    )
    result: List[str] = []
    for error in errors:
        message = formatter(error)
        if message not in result:
            result.append(message)
    return result


def _clean_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _append_evidence_metadata(task_text: str, evidence: Any) -> str:
    if evidence is None or evidence == [] or evidence == {}:
        return task_text
    if isinstance(evidence, dict):
        note = _clean_string(evidence.get("note"))
        return task_text + ("\n\n用户提供的执行说明：\n" + note if note else "")

    lines = ["\n\n用户提供的证据目录（专家禁止联网；未附正文时不得声称已读取或核验 URL 内容）："]
    for index, item in enumerate(evidence, 1):
        source_level = _clean_string(item.get("source_level"))
        source = _clean_string(item.get("source")) or "未命名来源"
        url = _clean_string(item.get("url"))
        note = _clean_string(item.get("note"))
        line = f"- [{index}] 来源={source}"
        if source_level:
            line += f"；级别={source_level}"
        if url:
            line += f"；URL={url}"
        if note:
            line += f"；说明={note}"
        lines.append(line)
    lines.append("- 证据边界：若没有提供来源正文，只能把上述条目视为待核验线索，不能当作已证事实。")
    return task_text + "\n".join(lines)


def _validate_ticket(
    packet: Dict[str, Any],
    *,
    schema_error_formatter: Callable[[ValidationError], str] = _format_schema_error,
) -> Tuple[Dict[str, Any], List[str]]:
    errors = _schema_errors(packet, schema_error_formatter)
    task = packet.get("task") if isinstance(packet.get("task"), dict) else {}
    budget = packet.get("approved_budget") if isinstance(packet.get("approved_budget"), dict) else {}
    requirements = task.get("requirements") if isinstance(task.get("requirements"), list) else []
    raw_calls = budget.get("calls")
    raw_cost = budget.get("max_cost_usd")

    calls = raw_calls if isinstance(raw_calls, int) and not isinstance(raw_calls, bool) else 0
    max_cost = float(raw_cost) if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool) else 0.0
    quality_tier = packet.get("quality_tier", "value")
    if not isinstance(quality_tier, str):
        quality_tier = "value"

    return {
        "task_id": _clean_string(packet.get("task_id")),
        "question": _clean_string(task.get("question")),
        "requirements": [_clean_string(item) for item in requirements if isinstance(item, str)],
        "language": _clean_string(task.get("language")),
        "objective": _clean_string(packet.get("objective")),
        "calls": calls,
        "max_cost_usd": max_cost,
        "quality_tier": quality_tier,
    }, errors


def prepare(
    args: argparse.Namespace,
    *,
    comments_reader: Callable[[str, int], Iterable[str]] = _issue_comments,
    state_evaluator: Callable[[Iterable[str]], Dict[str, Any]] = _execution_state,
    fingerprint_fn: Callable[[Dict[str, Any]], str] = task_fingerprint,
    schema_error_formatter: Callable[[ValidationError], str] = _format_schema_error,
) -> int:
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if args.event_path:
        title, body, issue_number, actor, association, comment_body = _read_event(args.event_path)
    else:
        title, body, issue_number = args.issue_title, args.issue_body, args.issue_number
        actor, association, comment_body = args.actor or "", args.author_association or "", args.comment_body or ""

    status: Dict[str, Any] = {
        "accepted": False,
        "title": title,
        "issue_number": issue_number,
        "actor": actor,
        "author_association": association,
        "required_model_calls": MINIMUM_MODEL_CALLS,
        "analysis_owner": "github-expert-team",
    }
    try:
        repository_owner = os.getenv("GITHUB_REPOSITORY_OWNER", "")
        if repository_owner and actor != repository_owner:
            raise ValueError("only the repository owner may trigger paid expert execution")
        if association and association != "OWNER":
            raise ValueError("trigger author_association must be OWNER")
        if not title.startswith("[execution]"):
            raise ValueError("Issue title must start with [execution].")
        if len(body) > MAX_BODY_CHARS:
            raise ValueError(f"Issue body exceeds {MAX_BODY_CHARS} characters.")

        is_retry, retry_id = _parse_retry(comment_body)
        packet = json.loads(body, parse_constant=_reject_constant)
        if not isinstance(packet, dict):
            raise ValueError("Issue body must be one JSON object.")

        validated, reasons = _validate_ticket(
            packet,
            schema_error_formatter=schema_error_formatter,
        )
        task_id = validated["task_id"]
        question = validated["question"]
        objective = validated["objective"]
        calls = validated["calls"]
        max_cost = validated["max_cost_usd"]
        quality_tier = validated["quality_tier"]

        fingerprint = fingerprint_fn(packet) if task_id and question else ""
        if fingerprint:
            duplicate = duplicate_reason(
                os.getenv("GITHUB_REPOSITORY", ""),
                issue_number,
                task_id,
                fingerprint,
                is_retry=is_retry,
                retry_id=retry_id,
                comments_reader=comments_reader,
                state_evaluator=state_evaluator,
                fingerprint_fn=fingerprint_fn,
            )
            if duplicate:
                reasons.append(duplicate)

        task_text = DELEGATION_NOTICE
        if question:
            task_text += "\n\n" + question
        if objective:
            task_text = f"任务目标：{objective}\n\n{task_text}"
        if validated["requirements"]:
            task_text += "\n\n执行要求：\n" + "\n".join(f"- {item}" for item in validated["requirements"] if item)
        if not any(error.startswith("evidence") or error.startswith("Unknown evidence") for error in reasons):
            task_text = _append_evidence_metadata(task_text, packet.get("evidence"))

        replacements = min(MAXIMUM_REPLACEMENTS, max(0, calls - MINIMUM_MODEL_CALLS))
        status.update(
            {
                "task_id": task_id,
                "task_fingerprint": fingerprint,
                "objective": objective,
                "calls": calls,
                "max_cost_usd": max_cost,
                "quality_tier": quality_tier,
                "maximum_replacements": replacements,
                "private_output": bool(packet.get("private_output", True)),
                "is_retry": is_retry,
                "retry_id": retry_id,
                "accepted": not reasons,
                "errors": reasons,
                "reason": "; ".join(reasons)
                if reasons
                else ("controlled retry accepted" if is_retry else "ticket, authorization, uniqueness, and budget accepted"),
            }
        )
        (output / "task.txt").write_text(task_text, encoding="utf-8")
        (output / "ticket.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    except (ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
        status["errors"] = [str(exc)]
        status["reason"] = str(exc)

    (output / "ticket-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    for key in (
        "accepted",
        "reason",
        "calls",
        "max_cost_usd",
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
        _write_output(key, str(value).lower() if isinstance(value, bool) else value)
    return 0


def render(args: argparse.Namespace) -> int:
    root = Path(args.output_dir)
    status = json.loads((root / "ticket-status.json").read_text(encoding="utf-8"))
    run_line = f"- Run: `{args.run_url}`\n" if args.run_url else ""
    if args.phase == "accepted":
        heading = "EXECUTION_RETRY_ACCEPTED" if status.get("is_retry") else "EXECUTION_ACCEPTED"
        retry_line = f"- RETRY_ID: `{status.get('retry_id')}`\n" if status.get("is_retry") else ""
        text = (
            f"## {heading}\n\n"
            "GitHub Issue Runner 已接收唯一任务。\n\n"
            f"- Task ID：`{status.get('task_id')}`\n"
            f"- TASK_FINGERPRINT: `{status.get('task_fingerprint')}`\n"
            + retry_line
            + "- 分析责任：`GitHub 专家团 + 裁判`\n"
            + "- 网页 GPT 职责：`仅提交、监控、取回和忠实转述；不得自行替代分析`\n"
            + "- 固定组合：`3名专家 + 1名裁判`\n"
            + f"- 批准调用数：`{status.get('calls')}`\n"
            + f"- 费用上限：`${status.get('max_cost_usd')}`\n"
            + f"- 模型故障替换上限：`{status.get('maximum_replacements')}`\n"
            + "- 专家外部工具：`禁止`\n"
            + "- 选模方式：`硬过滤 + 每席最多3个候选 + 固定优先级 + 厂商独立 + 预算降档`\n"
            + "- 推理参数：`仅使用可明确限制token的推理预算，优先保证最终正文`\n"
            + run_line
        )
    elif args.phase == "rejected":
        text = (
            "## EXECUTION_REJECTED\n\n"
            f"票据未进入模型调用阶段：{status.get('reason', 'unknown')}。\n\n"
            "未消耗专家模型调用额度。\n\n"
            "请直接修正本 Issue 正文，然后评论："
            "`/retry-expert-team <唯一retry_id>`；不要为同一任务继续新建 Issue。\n"
            + run_line
        )
    elif args.phase == "success":
        result = json.loads((root / "expert-team-result.json").read_text(encoding="utf-8"))
        expert_results = result.get("expert_results", [])
        usable = sum(item.get("status") in {"success_complete", "success_partial"} for item in expert_results)
        complete = sum(item.get("status") == "success_complete" for item in expert_results)
        text = (
            "## EXECUTION_COMPLETED\n\n"
            + run_line
            + f"- Task ID：`{status.get('task_id')}`\n"
            + f"- TASK_FINGERPRINT: `{status.get('task_fingerprint')}`\n"
            + (f"- RETRY_ID: `{status.get('retry_id')}`\n" if status.get("is_retry") else "")
            + f"- 三名专家可用：`{usable}/3`\n"
            + f"- 其中完整结束：`{complete}/3`\n"
            + f"- 裁判模型：`{result.get('judge', {}).get('model_id')}`\n"
            + f"- 实际费用：`${result.get('actual_cost_usd', 0)}`\n"
            + "- 完整正文、模型选择规则、调用证据和哈希清单：请查看本Run的Artifact。\n"
        )
    else:
        error_path = root / "expert-team-error.json"
        error = error_path.read_text(encoding="utf-8") if error_path.exists() else "No error artifact was generated."
        available = [
            name
            for name in (
                "expert-responses.json",
                "judge-response-diagnostics.json",
                "judge-response-raw.json",
                "artifact-manifest.json",
            )
            if (root / name).exists()
        ]
        evidence_line = f"\n可核验失败证据：`{', '.join(available)}`\n" if available else ""
        retry_hint = "\n修复后请在原Issue评论：`/retry-expert-team <唯一retry_id>`；不要新建重复任务。\n"
        text = "## EXECUTION_FAILED\n\n" + run_line + evidence_line + retry_hint + "\n```json\n" + error[:50000] + "\n```\n"
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
