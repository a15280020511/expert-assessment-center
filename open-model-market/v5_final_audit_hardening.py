"""Ensure the final production audit retains Runtime Knob Coverage.

Run #387 proved that the execution engine computed this audit but a later
pipeline writer replaced ``v5-request-audit.json``. This wrapper is installed
before the pipeline reaches its final request-audit phase and recomputes the
coverage after the last ordinary request-audit writer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

import v5_price_ranked_pipeline_legacy as pipeline_legacy
import v5_quality_status_integrity as quality_integrity
from v5_json_io import load_json_or_default


def _integrity_status(root: Path) -> str:
    for filename in ("v5-result.json", "v5-execution-summary.json"):
        value = load_json_or_default(root / filename, {})
        if not isinstance(value, Mapping):
            continue
        integrity = value.get("quality_integrity")
        if isinstance(integrity, Mapping) and str(integrity.get("status") or ""):
            return str(integrity.get("status"))
    return "UNKNOWN"


def install_final_request_audit_hardening() -> None:
    current = pipeline_legacy._request_audit  # noqa: SLF001
    if getattr(current, "_runtime_knob_final_writer", False):
        return

    def hardened_request_audit(
        output: Path,
        *,
        approved_total_calls: int,
    ) -> None:
        current(output, approved_total_calls=approved_total_calls)
        root = Path(output)
        quality_integrity._rewrite_request_audit(  # noqa: SLF001
            root,
            _integrity_status(root),
        )
        audit = load_json_or_default(root / "v5-request-audit.json", {})
        if not isinstance(audit, Mapping):
            raise RuntimeError("final request audit is missing")
        if audit.get("runtime_knob_coverage_status") != "PASS":
            raise RuntimeError(
                "runtime knob coverage failed after final request-audit write"
            )

    setattr(hardened_request_audit, "_runtime_knob_final_writer", True)
    pipeline_legacy._request_audit = hardened_request_audit  # noqa: SLF001


__all__ = ["install_final_request_audit_hardening"]
