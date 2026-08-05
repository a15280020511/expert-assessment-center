#!/usr/bin/env python3
"""Fail-closed immutable admission gate for V6 governed expert execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from v5_ticket_identity import task_fingerprint
from v6_governed_roster import GovernedRosterError, validate_governed_ticket


class TicketGateError(RuntimeError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise TicketGateError(f"missing immutable admission file: {path.name}")
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise TicketGateError(f"invalid immutable admission JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise TicketGateError(f"immutable admission JSON must be an object: {path.name}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def same_number(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    try:
        a, b = float(left), float(right)
    except (TypeError, ValueError):
        return False
    return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= 1e-12


def validate_gate(root: Path, calls: int, recovery: int, advisory: float | None) -> dict[str, Any]:
    ticket_path, status_path, task_path = (
        root / "ticket.json", root / "ticket-status.json", root / "task.txt"
    )
    ticket, status = load_object(ticket_path), load_object(status_path)
    if not task_path.is_file():
        raise TicketGateError("missing immutable admission file: task.txt")
    task_text = task_path.read_text("utf-8")
    errors: list[str] = []
    require = lambda condition, message: errors.append(message) if not condition else None
    require(status.get("accepted") is True, "ticket-status.accepted must be true")
    require(ticket.get("route") == "expert-team", "ticket.route must be expert-team")
    task = ticket.get("task") if isinstance(ticket.get("task"), Mapping) else {}
    task_id, question = str(ticket.get("task_id") or ""), str(task.get("question") or "").strip()
    require(bool(task_id), "ticket.task_id is required")
    require(str(status.get("task_id") or "") == task_id, "ticket/status task_id mismatch")
    require(bool(question) and question in task_text, "task.txt does not contain admitted question")
    fingerprint = task_fingerprint(ticket)
    require(str(status.get("task_fingerprint") or "") == fingerprint, "ticket/status fingerprint mismatch")
    try:
        validation = validate_governed_ticket(ticket)
    except GovernedRosterError as exc:
        validation = None
        errors.append(str(exc))
    budget = ticket.get("approved_budget") if isinstance(ticket.get("approved_budget"), Mapping) else {}
    require(2 <= calls <= 12, "expected calls must be between 2 and 12")
    require(0 <= recovery <= 4 and calls - recovery >= 2, "recovery must leave at least two primaries")
    require(int(budget.get("calls") or -1) == calls, "ticket calls differ from workflow")
    require(int(status.get("calls") or -1) == calls, "status calls differ from workflow")
    require(int(budget.get("maximum_recovery_calls") or -1) == recovery, "ticket recovery differs from workflow")
    require(int(status.get("maximum_recovery_calls") or -1) == recovery, "status recovery differs from workflow")
    require(int(status.get("maximum_initial_calls") or -1) == calls - recovery, "status initial calls mismatch")
    require(int(status.get("maximum_replacements") or -1) == recovery, "status replacements mismatch")
    accepted_cost = {"prompt_led_soft_governance", "unbounded_with_anomaly_guard"}
    require(str(budget.get("cost_policy") or "") in accepted_cost, "ticket cost policy invalid")
    require(str(status.get("cost_policy") or "") in accepted_cost, "status cost policy invalid")
    if advisory is None:
        require(budget.get("cost_anomaly_usd") is None, "ticket cost advisory must be absent")
        require(status.get("cost_anomaly_usd") is None, "status cost advisory must be absent")
    else:
        require(same_number(budget.get("cost_anomaly_usd"), advisory), "ticket cost advisory mismatch")
        require(same_number(status.get("cost_anomaly_usd"), advisory), "status cost advisory mismatch")
    expected_strings = {
        "analysis_owner": "governance-signed-v6-networkx-expert-team",
        "runtime_version": "v6-governed-roster-networkx-1",
        "authoritative_trigger": "issue_comment.created",
        "fallback_policy": "exact-provider-no-fallback-no-unlisted-model",
    }
    for field, expected in expected_strings.items():
        require(str(status.get(field) or "") == expected, f"status {field} mismatch")
    require(status.get("private_output") is False, "private output must be false")
    require(status.get("cross_task_history_used") is False, "cross-task history must be false")
    require(status.get("claude_mechanism_enabled") is False, "Claude mechanism must be false")
    for field in ("claude_calls", "gpt_planning_calls", "gpt_synthesis_calls"):
        require(int(status.get(field) or 0) == 0, f"status {field} must be zero")
    mode = str(status.get("trigger_mode") or "")
    require(mode in {"run", "retry"}, "trigger_mode must be run or retry")
    if mode == "run":
        require(status.get("is_retry") is not True, "run cannot be marked retry")
        require(str(status.get("execution_id") or "") == task_id, "execution_id must equal task_id")
    if mode == "retry":
        require(status.get("is_retry") is True, "retry must be marked retry")
        require(bool(str(status.get("retry_id") or "")), "retry_id is required")
    if validation is not None:
        roster = validation["governance_roster"]
        require(validation["approved_total_calls"] == calls, "roster total calls mismatch")
        require(validation["approved_recovery_calls"] == recovery, "roster recovery mismatch")
        require(str(status.get("governance_roster_sha256") or "") == roster["roster_sha256"], "roster digest mismatch")
        require(str(status.get("governance_commit_sha") or "") == roster["governance_commit_sha"], "governance commit mismatch")
        require(int(status.get("team_size") or -1) == len(validation["primary_members"]), "team size mismatch")
    if errors:
        raise TicketGateError("; ".join(dict.fromkeys(errors)))
    roster = validation["governance_roster"]
    return {
        "schema_version": "v6-ticket-gate-1",
        "status": "PASS",
        "runtime_version": "v6-governed-roster-networkx-1",
        "task_id": task_id,
        "task_fingerprint": fingerprint,
        "trigger_mode": mode,
        "governance_roster_sha256": roster["roster_sha256"],
        "governance_commit_sha": roster["governance_commit_sha"],
        "validated_budget": {
            "maximum_total_calls": calls,
            "governance_calls": 0,
            "maximum_initial_calls": calls - recovery,
            "maximum_recovery_calls": recovery,
            "cost_anomaly_usd": advisory
        },
        "immutable_admission_evidence": {
            path.name: {"sha256": sha256(path), "size_bytes": path.stat().st_size}
            for path in (ticket_path, status_path, task_path)
        },
        "model_calls_performed": 0,
        "claude_calls": 0,
        "mutation_performed": False
    }


def optional_float(value: str) -> float | None:
    if not str(value or "").strip():
        return None
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("cost advisory must be finite")
    return number


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--expected-calls", required=True, type=int)
    parser.add_argument("--expected-recovery-calls", required=True, type=int)
    parser.add_argument("--expected-cost-anomaly-usd", default="", type=optional_float)
    args = parser.parse_args()
    root, output = Path(args.output_dir), Path(args.output_dir) / "ticket-gate.json"
    try:
        result = validate_gate(root, args.expected_calls, args.expected_recovery_calls, args.expected_cost_anomaly_usd)
    except TicketGateError as exc:
        result = {"schema_version": "v6-ticket-gate-1", "status": "FAIL", "errors": [part.strip() for part in str(exc).split(";") if part.strip()], "model_calls_performed": 0, "claude_calls": 0, "mutation_performed": False}
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"V6_TICKET_GATE_FAIL: {exc}")
        return 1
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
