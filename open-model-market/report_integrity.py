#!/usr/bin/env python3
"""Fail-closed dynamic expert-report and artifact completeness gate."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


class ReportIntegrityError(ValueError):
    pass


def _expected_expert_count(report: dict[str, Any]) -> int:
    raw = report.get("expert_count")
    if raw is None:
        outputs = report.get("expert_outputs")
        raw = len(outputs) if isinstance(outputs, list) else 0
    try:
        count = int(raw)
    except (TypeError, ValueError) as exc:
        raise ReportIntegrityError("expert_count must be an integer") from exc
    if not 1 <= count <= 4:
        raise ReportIntegrityError("expert_count must be between 1 and 4")
    return count


def validate(report: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    required = ["task_id", "final_status", "judge_report", "call_ledger", "expert_outputs", "manifest"]
    missing = [key for key in required if not report.get(key)]
    if missing:
        raise ReportIntegrityError(f"missing report fields: {missing}")
    if report["final_status"] != "EXPERT_TEAM_COMPLETED":
        raise ReportIntegrityError("business completion status is not complete")
    expected = _expected_expert_count(report)
    outputs = report["expert_outputs"]
    if not isinstance(outputs, list) or len(outputs) != expected or any(not str(value).strip() for value in outputs):
        raise ReportIntegrityError(f"requires {expected}/{expected} non-empty expert outputs")
    if not str(report["judge_report"]).strip():
        raise ReportIntegrityError("judge report empty")
    minimum_calls = expected + 1
    if not isinstance(report["call_ledger"], list) or len(report["call_ledger"]) < minimum_calls:
        raise ReportIntegrityError(f"call ledger incomplete: requires at least {minimum_calls} calls")
    manifest = report["manifest"]
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        raise ReportIntegrityError("manifest files missing")
    verified = 0
    for row in files:
        path = artifact_dir / str(row["path"])
        if not path.is_file():
            raise ReportIntegrityError(f"artifact missing: {row['path']}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != row["sha256"]:
            raise ReportIntegrityError(f"artifact hash mismatch: {row['path']}")
        verified += 1
    return {
        "schema_version": "expert-report-integrity-v2-dynamic-team",
        "status": "PASS",
        "expert_count": expected,
        "minimum_call_count": minimum_calls,
        "call_count": len(report["call_ledger"]),
        "verified_artifact_files": verified,
    }
