#!/usr/bin/env python3
"""Render authoritative V5 final status from the frozen evidence bundle."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from v5_evidence_bundle import (
    FinalStatusInputs,
    build_final_status_record,
    render_final_status_markdown,
)


def _write_output(name: str, value: Any) -> None:
    target = os.getenv("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--ticket-upload-outcome", default="unknown")
    parser.add_argument("--audit-outcome", default="unknown")
    parser.add_argument("--manifest-outcome", default="unknown")
    parser.add_argument("--artifact-id", default="")
    parser.add_argument("--artifact-url", default="")
    parser.add_argument("--artifact-digest", default="")
    parser.add_argument("--independent-revalidation-file", default="")
    parser.add_argument("--json-output", default="")
    args = parser.parse_args()

    root = Path(args.output_dir)
    inputs = FinalStatusInputs.from_directory(root)
    independent: dict[str, Any] = {}
    if args.independent_revalidation_file:
        try:
            raw_independent = json.loads(
                Path(args.independent_revalidation_file).read_text(
                    encoding="utf-8"
                )
            )
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            raw_independent = {}
        if isinstance(raw_independent, dict):
            independent = raw_independent
    record = build_final_status_record(
        inputs,
        run_url=args.run_url,
        ticket_upload_outcome=args.ticket_upload_outcome,
        audit_outcome=args.audit_outcome,
        manifest_outcome=args.manifest_outcome,
        artifact_id=args.artifact_id,
        artifact_url=args.artifact_url,
        artifact_digest=args.artifact_digest,
        independent_revalidation=independent,
    )
    json_path = Path(args.json_output) if args.json_output else root / "final-status.json"
    json_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(render_final_status_markdown(record), end="")
    _write_output("status", record["status"])
    _write_output("final_status_json", str(json_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
