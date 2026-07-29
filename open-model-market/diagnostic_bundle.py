#!/usr/bin/env python3
"""Generate one redacted, structured diagnosis bundle from partial or complete runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

JSON_FILES = (
    "ticket-status.json",
    "history-restore.json",
    "task-routing.json",
    "model-selection.json",
    "expert-responses.json",
    "judge-attempts.json",
    "judge-response-diagnostics.json",
    "expert-team-result.json",
    "expert-team-error.json",
    "unhandled-exception.json",
    "postprocess-errors.json",
    "request-audit.json",
    "call-ledger.json",
    "execution-audit.json",
    "execution-diagnosis.json",
    "artifact-manifest.json",
    "model-performance.json",
)
EXPECTED_EVIDENCE = (
    "ticket-status.json",
    "task-routing.json",
    "model-selection.json",
    "request-audit.json",
    "call-ledger.json",
    "execution-console.log",
)
SAFE_ENV_KEYS = (
    "GITHUB_REPOSITORY",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_SHA",
    "GITHUB_WORKFLOW",
    "GITHUB_JOB",
    "ISSUE_NUMBER",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path, parse_errors: list[dict[str, str]]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        parse_errors.append(
            {
                "file": path.name,
                "type": type(exc).__name__,
                "message": str(exc),
            }
        )
        return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _remediation(code: str, stage: str) -> list[str]:
    hints: list[str] = []
    if "TIMEOUT" in code:
        hints.append("Check Provider availability and retain the bounded replacement call.")
    if "EMPTY" in code or "TRUNCATED" in code or "TOO_SHORT" in code:
        hints.append(
            "Inspect captured requests, raw responses, finish reasons, token counts, and replacement history."
        )
    if "QUORUM" in code:
        hints.append(
            "Inspect all expert attempts; do not invoke the judge without 3/3 usable experts."
        )
    if "REQUEST" in code:
        hints.append(
            "Compare request-audit.json with call-ledger.json and captured request files."
        )
    if stage in {"catalog_and_routing", "selection"}:
        hints.append(
            "Inspect live catalog, benchmark fallback, top-50 gates, price data, and Provider diversity."
        )
    if stage in {"publish", "delivery"}:
        hints.append(
            "Inspect report comment markers, report SHA, upload outcomes, and artifact metadata."
        )
    if not hints:
        hints.append(
            "Use the stage matrix, execution-console.log, traceback evidence, and artifact hashes to locate the first missing invariant."
        )
    return hints


def _inventory(root: Path) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "diagnostic-summary.json":
            continue
        stat = path.stat()
        inventory.append(
            {
                "path": str(path.relative_to(root)),
                "size_bytes": stat.st_size,
                "sha256": _sha256(path),
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat(),
            }
        )
    return inventory


def _failure_chain(
    *,
    primary_code: str,
    primary_stage: str,
    primary_message: str,
    parse_errors: list[dict[str, str]],
    missing: list[str],
    workflow_outcomes: Mapping[str, str],
) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    if primary_code != "NONE" or primary_message:
        chain.append(
            {
                "kind": "primary",
                "code": primary_code,
                "stage": primary_stage,
                "message": primary_message,
            }
        )
    for item in parse_errors:
        chain.append(
            {
                "kind": "parse_error",
                "code": "DIAGNOSTIC_JSON_PARSE_FAILED",
                "stage": "diagnostics",
                "message": f"{item['file']}: {item['message']}",
            }
        )
    for path in missing:
        chain.append(
            {
                "kind": "missing_evidence",
                "code": "DIAGNOSTIC_EVIDENCE_MISSING",
                "stage": "evidence",
                "message": path,
            }
        )
    for stage, outcome in workflow_outcomes.items():
        if outcome not in {"success", "skipped"}:
            chain.append(
                {
                    "kind": "workflow_outcome",
                    "code": "WORKFLOW_STEP_NOT_SUCCESSFUL",
                    "stage": stage,
                    "message": outcome,
                }
            )
    return chain


def build(
    root: Path,
    *,
    execute_outcome: str,
    publish_outcome: str,
    state_outcome: str,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    parse_errors: list[dict[str, str]] = []
    loaded = {name: _load(root / name, parse_errors) for name in JSON_FILES}

    ticket = _mapping(loaded["ticket-status.json"])
    routing = _mapping(loaded["task-routing.json"])
    result = _mapping(loaded["expert-team-result.json"])
    error = _mapping(loaded["expert-team-error.json"])
    unhandled = _mapping(loaded["unhandled-exception.json"])
    ledger = _mapping(loaded["call-ledger.json"])
    request_audit = _mapping(loaded["request-audit.json"])
    history = _mapping(loaded["model-performance.json"])
    judge_attempts = loaded["judge-attempts.json"]
    experts = loaded["expert-responses.json"]

    primary_message = str(
        error.get("message")
        or unhandled.get("message")
        or unhandled.get("traceback")
        or ""
    )
    primary_code = str(
        error.get("error_code")
        or ("UNHANDLED_EXCEPTION" if unhandled else "NONE")
    )
    primary_stage = str(
        error.get("stage") or unhandled.get("stage") or "none"
    )

    usable = (
        sum(
            isinstance(item, Mapping)
            and item.get("status") in {"success_complete", "success_partial"}
            for item in experts
        )
        if isinstance(experts, list)
        else None
    )
    stage_status = {
        "ticket": "PASS" if ticket.get("accepted") else "FAIL",
        "history_restore": str(
            _mapping(loaded["history-restore.json"]).get("status") or "missing"
        ),
        "routing": str(routing.get("status") or "missing"),
        "selection": "PASS" if loaded["model-selection.json"] else "MISSING",
        "experts": f"{usable}/3 usable" if usable is not None else "MISSING",
        "judge": str(
            result.get("judge_status")
            or error.get("error_code")
            or "missing"
        ),
        "requests": str(request_audit.get("status") or "missing"),
        "ledger": "PASS" if ledger else "MISSING",
        "report": "PASS" if (root / "expert-team-report.md").exists() else "MISSING",
        "publish": publish_outcome,
        "state_upload_staging": state_outcome,
    }

    summary = _mapping(ledger.get("summary"))
    history_models = (
        history.get("models")
        if isinstance(history.get("models"), Mapping)
        else {}
    )
    missing = [
        name for name in EXPECTED_EVIDENCE if not (root / name).is_file()
    ]
    workflow_outcomes = {
        "execute": execute_outcome,
        "publish": publish_outcome,
        "state_staging": state_outcome,
    }
    failure_chain = _failure_chain(
        primary_code=primary_code,
        primary_stage=primary_stage,
        primary_message=primary_message,
        parse_errors=parse_errors,
        missing=missing,
        workflow_outcomes=workflow_outcomes,
    )
    request_complete = (
        request_audit.get("status") == "PASS"
        and request_audit.get("captured_request_count")
        == request_audit.get("expected_request_count")
    )
    diagnostic_confidence = (
        "high"
        if primary_code != "NONE" and request_complete and bool(ledger)
        else ("medium" if primary_code != "NONE" or failure_chain else "low")
    )

    diagnosis = {
        "schema_version": "expert-diagnostics-v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_identity": {
            key.lower(): os.getenv(key) for key in SAFE_ENV_KEYS
        },
        "workflow_outcomes": workflow_outcomes,
        "primary_failure": {
            "code": primary_code,
            "stage": primary_stage,
            "message": primary_message,
            "provider": error.get("provider") or unhandled.get("provider"),
            "model": error.get("model") or unhandled.get("model"),
            "finish_reason": error.get("finish_reason"),
            "retryable": bool(error.get("retryable")),
        },
        "stage_status": stage_status,
        "failure_chain": failure_chain,
        "diagnostic_confidence": diagnostic_confidence,
        "call_summary": dict(summary),
        "request_capture": {
            "status": request_audit.get("status"),
            "captured": request_audit.get("captured_request_count"),
            "expected": request_audit.get("expected_request_count"),
            "complete": request_complete,
        },
        "history_summary": {
            "model_count": len(history_models),
            "parseable": loaded["model-performance.json"] is not None,
        },
        "judge_attempt_count": (
            len(judge_attempts) if isinstance(judge_attempts, list) else 0
        ),
        "expert_usable_count": usable,
        "missing_evidence": missing,
        "parse_errors": parse_errors,
        "remediation_hints": _remediation(primary_code, primary_stage),
        "artifact_inventory": _inventory(root),
        "security": {
            "secret_values_included": False,
            "environment_allowlist": [
                key.lower() for key in SAFE_ENV_KEYS
            ],
            "raw_environment_embedded": False,
        },
    }
    (root / "diagnostic-summary.json").write_text(
        json.dumps(diagnosis, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return diagnosis


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--execute-outcome", default="unknown")
    parser.add_argument("--publish-outcome", default="unknown")
    parser.add_argument("--state-outcome", default="unknown")
    args = parser.parse_args()
    result = build(
        Path(args.output_dir),
        execute_outcome=args.execute_outcome,
        publish_outcome=args.publish_outcome,
        state_outcome=args.state_outcome,
    )
    print(
        json.dumps(
            {
                "primary_failure": result["primary_failure"],
                "stage_status": result["stage_status"],
                "diagnostic_confidence": result["diagnostic_confidence"],
                "failure_chain_count": len(result["failure_chain"]),
                "missing_evidence_count": len(result["missing_evidence"]),
                "parse_error_count": len(result["parse_errors"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
