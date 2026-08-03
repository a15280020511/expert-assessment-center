#!/usr/bin/env python3
"""Serialized zero-call admission guard for the production workflow."""
from __future__ import annotations

import json
import os
import urllib.request

from v5_no_tools_policy import assert_allowed_control_plane_url
from typing import Any, Mapping


def _write_output(name: str, value: Any) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _api_json(url: str) -> Any:
    assert_allowed_control_plane_url(url)
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "v5-admission-lock"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _active_lower_run(repository: str, current_run_id: int) -> int:
    active: list[int] = []
    for status in ("in_progress", "queued"):
        url = (
            f"https://api.github.com/repos/{repository}/actions/workflows/execution-ticket.yml/runs"
            f"?status={status}&per_page=100"
        )
        payload = _api_json(url)
        rows = payload.get("workflow_runs") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            run_id = int(row.get("id") or 0)
            if 0 < run_id < current_run_id:
                active.append(run_id)
    return min(active, default=0)


def main() -> int:
    repository = os.getenv("GITHUB_REPOSITORY", "")
    current_run_id = int(os.getenv("GITHUB_RUN_ID") or 0)
    earlier = _active_lower_run(repository, current_run_id)
    allowed = earlier == 0
    reason = "" if allowed else (
        f"EXECUTION_BUSY: earlier production Run {earlier} is queued or in progress; "
        "this task is rejected instead of silently queued"
    )
    _write_output("allowed", str(allowed).lower())
    _write_output("reason", reason)
    print(json.dumps({"allowed": allowed, "reason": reason, "model_calls": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
