"""Write deterministic Phase-A V5 task-resource audit artifacts."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

_ARTIFACTS = {
    "task-interpretations.json": "task_semantics",
    "atomic-work-graph.json": "atomic_work_graphs",
    "task-resource-matrix.json": "resource_matrices",
}


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_task_resource_artifacts(bundle: Mapping[str, Any], output_dir: str | Path) -> dict[str, Any]:
    if int(bundle.get("version", 0)) != 5 or not bundle.get("phase_a_complete"):
        raise ValueError("Only a completed V5 Phase-A task-resource bundle can be written.")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, Any]] = []
    for filename, key in _ARTIFACTS.items():
        if key not in bundle:
            raise ValueError(f"Missing required Phase-A bundle key: {key}")
        payload = _canonical_bytes(bundle[key])
        path = root / filename
        path.write_bytes(payload)
        manifest_rows.append(
            {
                "name": filename,
                "sha256": sha256(payload).hexdigest(),
                "bytes": len(payload),
                "source_key": key,
            }
        )
    manifest = {
        "version": 5,
        "phase": "task-resource-compilation",
        "task_digest": bundle["task_semantics"].get("task_digest"),
        "model_market_accessed": bool(bundle.get("model_market_accessed")),
        "artifacts": manifest_rows,
    }
    manifest_payload = _canonical_bytes(manifest)
    (root / "task-resource-manifest.json").write_bytes(manifest_payload)
    return {
        **manifest,
        "manifest_sha256": sha256(manifest_payload).hexdigest(),
        "output_dir": str(root),
    }
