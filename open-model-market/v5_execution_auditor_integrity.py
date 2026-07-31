#!/usr/bin/env python3
"""Augment the deterministic V5 audit with node-level quality integrity."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import v5_execution_auditor as base
from v5_quality_status_integrity import (
    DEGRADED_SUCCESS_STATUSES,
    STRICT_SUCCESS_STATUSES,
)


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _node_quality(root: Path) -> dict[str, Any]:
    rows = _load(root / "v5-node-results.json", [])
    rows = rows if isinstance(rows, list) else []
    strict: list[str] = []
    degraded: list[dict[str, Any]] = []
    failed: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        node_id = str(row.get("node_id") or "")
        status = str(row.get("status") or "")
        if status in STRICT_SUCCESS_STATUSES:
            strict.append(node_id)
        elif status in DEGRADED_SUCCESS_STATUSES or status.startswith("success"):
            attempts = row.get("attempts", [])
            gate_failures = []
            if isinstance(attempts, list):
                for attempt in attempts:
                    if not isinstance(attempt, Mapping):
                        continue
                    reasons = attempt.get("gate_reasons", [])
                    reasons = [str(value) for value in reasons] if isinstance(reasons, list) else []
                    if str(attempt.get("status") or "") == "quality_gate_failed" or reasons:
                        gate_failures.append(
                            {
                                "attempt_index": int(attempt.get("attempt_index") or 0),
                                "status": str(attempt.get("status") or ""),
                                "gate_reasons": reasons,
                                "quality_score": float(attempt.get("quality_score") or 0.0),
                            }
                        )
            degraded.append(
                {
                    "node_id": node_id,
                    "status": status,
                    "quality_score": float(row.get("quality_score") or 0.0),
                    "gate_failures": gate_failures,
                }
            )
        else:
            failed.append(node_id)
    return {
        "node_result_count": len(rows),
        "strict_node_ids": strict,
        "degraded_nodes": degraded,
        "failed_node_ids": failed,
        "all_nodes_strict": bool(rows) and len(strict) == len(rows),
    }


def audit(root: Path, *, execute_outcome: str, publish_outcome: str) -> dict[str, Any]:
    result = base.audit(
        root,
        execute_outcome=execute_outcome,
        publish_outcome=publish_outcome,
    )
    summary = _load(root / "v5-execution-summary.json", {})
    summary = summary if isinstance(summary, Mapping) else {}
    evidence = _node_quality(root)
    failures = list(result.get("failures") or [])
    degradations = list(result.get("degradations") or [])
    completion_mode = str(summary.get("completion_mode") or "")
    quality_status = str(summary.get("quality_status") or "")
    integrity = summary.get("quality_integrity", {})
    integrity = integrity if isinstance(integrity, Mapping) else {}

    if evidence["failed_node_ids"]:
        failures.append(
            "node-level execution failures are present: "
            + ", ".join(evidence["failed_node_ids"])
        )

    if evidence["degraded_nodes"]:
        if completion_mode != "degraded" or quality_status != "degraded_success":
            failures.append(
                "degraded node output was incorrectly represented as full success"
            )
        else:
            degradations.append(
                "one or more nodes delivered usable output after failing a quality gate"
            )
        if integrity.get("status") != "DEGRADED":
            failures.append("run-level quality integrity evidence is missing or inconsistent")
    elif evidence["all_nodes_strict"]:
        if completion_mode == "full" and quality_status != "full_success":
            failures.append("strict full completion is missing full_success quality status")
        if completion_mode == "full" and integrity.get("status") not in {"PASS", None}:
            failures.append("strict node success conflicts with run-level quality integrity")

    if quality_status == "full_success" and evidence["degraded_nodes"]:
        failures.append("full_success is forbidden when a node is success_degraded")

    checks = dict(result.get("checks") or {})
    checks.update(
        {
            "quality_status": quality_status,
            "quality_integrity_status": integrity.get("status"),
            "strict_node_count": len(evidence["strict_node_ids"]),
            "degraded_node_count": len(evidence["degraded_nodes"]),
            "failed_node_count": len(evidence["failed_node_ids"]),
            "node_quality_evidence": evidence,
        }
    )
    result["checks"] = checks
    result["failures"] = list(dict.fromkeys(failures))
    result["degradations"] = list(dict.fromkeys(degradations))
    result["status"] = (
        "FAIL"
        if result["failures"]
        else "DEGRADED"
        if result["degradations"]
        else "PASS"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--execute-outcome", default="unknown")
    parser.add_argument("--publish-outcome", default="unknown")
    args = parser.parse_args()
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    result = audit(
        root,
        execute_outcome=args.execute_outcome,
        publish_outcome=args.publish_outcome,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    (root / "execution-audit.json").write_text(serialized, encoding="utf-8")
    (root / "execution-diagnosis.json").write_text(serialized, encoding="utf-8")
    base._write_output("status", result["status"])
    base._write_output(
        "reason",
        "; ".join(result["failures"] or result["degradations"]),
    )
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
