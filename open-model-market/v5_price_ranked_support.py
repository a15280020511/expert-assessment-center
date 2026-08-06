"""Shared deterministic helpers for the price-ranked production path."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from v5_json_io import load_json_or_default


def load_mapping_path(path: Path) -> dict[str, Any]:
    """Load a JSON object or return an empty object."""
    value = load_json_or_default(path, {})
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def load_mapping(root: Path, name: str) -> dict[str, Any]:
    """Load a JSON object from ``root/name`` or return an empty object."""
    return load_mapping_path(root / name)


def canonical_ticket_task(root: Path, fallback: str) -> tuple[str, str]:
    """Project the admitted ticket task without importing the legacy runtime."""
    packet = load_mapping(root, "ticket.json")
    raw_task = packet.get("task")
    if not isinstance(raw_task, Mapping):
        return str(fallback).strip(), "fallback-cli-task"
    question = str(raw_task.get("question") or "").strip()
    if not question:
        return str(fallback).strip(), "fallback-cli-task"
    sections: list[str] = [question]
    raw_requirements = raw_task.get("requirements")
    if isinstance(raw_requirements, list):
        requirements = tuple(
            text for value in raw_requirements if (text := str(value).strip())
        )
        if requirements:
            bullet_text = "\n".join(f"- {value}" for value in requirements)
            sections.append(f"执行要求：\n{bullet_text}")
    language = str(raw_task.get("language") or "").strip()
    if language:
        sections.append(f"输出语言：{language}")
    return "\n\n".join(sections), "ticket.task"


def mapping_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Return only mapping rows from a possible JSON array."""
    if isinstance(value, list):
        return tuple(filter(lambda row: isinstance(row, Mapping), value))
    return ()


def canonical_json_sha(value: Any) -> str:
    """Hash the canonical JSON representation used by evidence receipts."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def report_text(root: Path, name: str = "v5-final-report.md") -> str:
    """Read one UTF-8 report when present."""
    path = root / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def providers_from_requests(
    requests: tuple[Mapping[str, Any], ...],
) -> tuple[str, ...]:
    """Extract exact provider locks from audited requests."""
    providers: set[str] = set()
    for request in requests:
        provider = request.get("provider")
        if not isinstance(provider, Mapping):
            continue
        only = provider.get("only")
        if isinstance(only, list) and only:
            value = str(only[0]).strip()
            if value:
                providers.add(value)
    return tuple(sorted(providers))


def models_from_graph(graph: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract unique selected model IDs in materialized graph order."""
    models: list[str] = []
    seen: set[str] = set()
    for row in mapping_rows(graph.get("nodes")):
        model = str(row.get("model") or "").strip()
        if model and model not in seen:
            seen.add(model)
            models.append(model)
    return tuple(models)
