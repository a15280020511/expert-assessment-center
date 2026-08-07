#!/usr/bin/env python3
"""Non-blocking free-first telemetry for paid expert execution.

Free/zero-call qualification remains useful evidence, but it is no longer an
execution prerequisite. Missing, stale, failed, or unavailable Canary evidence
is recorded as advisory telemetry and never prevents the expert runtime from
starting.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


class PaidAcceptanceFreeFirstError(RuntimeError):
    """Backward-compatible exception type; no longer raised as an admission gate."""


def enforce_free_first(
    *,
    output_dir: Path,
    expected_sha: str,
    repository: str | None = None,
    token: str | None = None,
) -> Mapping[str, Any]:
    """Return an always-permissive advisory verdict.

    This function intentionally performs no GitHub/API lookup. Qualification and
    Canary workflows can still run independently and publish evidence, but paid
    execution is decoupled from those workflows.
    """
    sha = str(expected_sha or "").strip()
    valid_sha = len(sha) == 40 and all(char in "0123456789abcdef" for char in sha)
    verdict = {
        "schema_version": "v5-free-first-advisory-1",
        "status": "PASS",
        "mode": "advisory-only",
        "target_sha": sha,
        "target_sha_well_formed": valid_sha,
        "paid_acceptance_allowed": True,
        "execution_blocked": False,
        "qualification_required_before_execution": False,
        "free_canary_required_before_execution": False,
        "repository": str(repository or ""),
        "evidence_lookup_performed": False,
        "reasons": (
            []
            if valid_sha
            else ["authoritative SHA missing or malformed; execution remains allowed"]
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "free-first-preflight-verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return verdict


__all__ = ["PaidAcceptanceFreeFirstError", "enforce_free_first"]
