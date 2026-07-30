"""Correct audit semantics for zero-call R8 preflight rejection."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import v5_r8_executor as runtime
from execution_graph import ExecutionGraph, GraphLimits

_INSTALLED = False
_ORIGINAL_WRITE_REJECTION = runtime._write_rejection


def write_blocked_rejection(
    root: Path | None,
    graph: ExecutionGraph,
    limits: GraphLimits,
    preflight: Mapping[str, Any],
) -> None:
    _ORIGINAL_WRITE_REJECTION(root, graph, limits, preflight)
    if root is None:
        return
    path = Path(root) / "v5-request-audit.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = "BLOCKED"
    data["audit_result"] = "preflight-rejected-before-model-calls"
    data["request_count"] = 0
    data["requests"] = []
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    runtime._write_rejection = write_blocked_rejection
    _INSTALLED = True
