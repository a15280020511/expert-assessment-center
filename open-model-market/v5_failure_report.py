"""Deterministic failure-report artifacts for fail-closed V5 runs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _money(value: Any) -> str:
    try:
        return f"{float(value):.8f}"
    except (TypeError, ValueError):
        return "unavailable"


def ensure_failure_report(root: Path, error: BaseException) -> Path:
    """Create one explicit failure report without overwriting valid output."""
    root.mkdir(parents=True, exist_ok=True)
    final_report = root / "v5-final-report.md"
    normalized_report = root / "expert-team-report.md"

    if final_report.is_file() and final_report.stat().st_size > 0:
        if not normalized_report.is_file() or normalized_report.stat().st_size == 0:
            normalized_report.write_text(
                final_report.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        return final_report

    summary = _load(root / "v5-execution-summary.json", {})
    summary = summary if isinstance(summary, Mapping) else {}
    preflight = summary.get("cost_preflight")
    preflight = preflight if isinstance(preflight, Mapping) else {}
    blockers = preflight.get("blockers")
    blockers = blockers if isinstance(blockers, list) else []
    budget = summary.get("execution_budget")
    budget = budget if isinstance(budget, Mapping) else {}

    lines = [
        "# V5 execution failed",
        "",
        "This is a deterministic fail-closed report. It is not a successful task answer.",
        "",
        "## status",
        "failed",
        "",
        "## primary_failure",
        str(error).strip() or error.__class__.__name__,
        "",
        "## stop_reason",
        str(summary.get("stop_reason") or "v5-production-runtime-failure"),
        "",
        "## model_calls",
        str(int(budget.get("calls_reserved") or 0)),
    ]
    if preflight:
        lines.extend([
            "",
            "## cost_preflight",
            f"- Status: `{preflight.get('status') or 'unknown'}`",
            f"- Estimated initial cost USD: `{_money(preflight.get('estimated_initial_cost_usd'))}`",
            f"- Risk-adjusted upper cost USD: `{_money(preflight.get('risk_adjusted_cost_upper_usd'))}`",
            f"- Anomaly stop USD: `{_money(preflight.get('cost_anomaly_usd'))}`",
            f"- Policy: `{preflight.get('policy') or 'unknown'}`",
        ])
    if blockers:
        lines.extend([
            "",
            "## blockers",
            *[f"- `{str(blocker)}`" for blocker in blockers],
        ])
    lines.extend([
        "",
        "## evidence_boundary",
        "No alternate runtime was invoked. No successful business conclusion is claimed.",
        "",
    ])
    report = "\n".join(lines)
    final_report.write_text(report, encoding="utf-8")
    normalized_report.write_text(report, encoding="utf-8")
    return final_report
