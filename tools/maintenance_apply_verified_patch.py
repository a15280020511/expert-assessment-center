#!/usr/bin/env python3
"""Reconstruct one owner-authorized maintenance patch from verified Issue parts."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

PART_RE = re.compile(r"^PATCH_PART (\d{3})/(\d{3})\n([A-Za-z0-9+/=]+)$")
SUBPART_RE = re.compile(r"^PATCH_SUBPART 002/004 (\d{3})\n([A-Za-z0-9+/=]+)$")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def reconstruct(ticket: dict[str, Any], comments: list[dict[str, Any]]) -> tuple[bytes, dict[str, Any]]:
    count = int(ticket["part_count"])
    lengths = {int(k): int(v) for k, v in ticket["part_lengths"].items()}
    hashes = {int(k): str(v) for k, v in ticket["part_sha256"].items()}
    expected = list(range(1, count + 1))
    if sorted(lengths) != expected or sorted(hashes) != expected:
        raise ValueError("part expectations are incomplete")

    parts: dict[int, str] = {}
    subparts: dict[int, str] = {}
    for row in comments:
        body = str(row.get("body") or "").strip()
        match = PART_RE.fullmatch(body)
        if match:
            index, total, payload = int(match.group(1)), int(match.group(2)), match.group(3)
            if total != count or index in parts:
                raise ValueError("part metadata is invalid or duplicated")
            parts[index] = payload
            continue
        submatch = SUBPART_RE.fullmatch(body)
        if submatch:
            index, payload = int(submatch.group(1)), submatch.group(2)
            if index in subparts:
                raise ValueError("subpart is duplicated")
            subparts[index] = payload

    if sorted(parts) != expected:
        raise ValueError("parts are missing")

    rebuilt = False
    if subparts:
        sub_lengths = {int(k): int(v) for k, v in ticket["part2_subpart_lengths"].items()}
        sub_hashes = {int(k): str(v) for k, v in ticket["part2_subpart_sha256"].items()}
        if sorted(subparts) != [1, 2, 3, 4] or sorted(sub_lengths) != [1, 2, 3, 4] or sorted(sub_hashes) != [1, 2, 3, 4]:
            raise ValueError("part 2 subparts or expectations are incomplete")
        for index in [1, 2, 3, 4]:
            payload = subparts[index]
            if len(payload) != sub_lengths[index] or _sha(payload) != sub_hashes[index]:
                raise ValueError(f"part 2 subpart {index} integrity mismatch")
        parts[2] = "".join(subparts[index] for index in [1, 2, 3, 4])
        rebuilt = True

    for index in expected:
        payload = parts[index]
        if len(payload) != lengths[index] or _sha(payload) != hashes[index]:
            raise ValueError(f"part {index} integrity mismatch")

    encoded = "".join(parts[index] for index in expected)
    patch = base64.b64decode(encoded, validate=True)
    digest = hashlib.sha256(patch).hexdigest()
    if len(patch) != int(ticket["patch_bytes"]):
        raise ValueError("patch byte count mismatch")
    if digest != str(ticket["patch_sha256"]):
        raise ValueError("patch digest mismatch")
    return patch, {
        "patch_bytes": len(patch),
        "patch_sha256": digest,
        "part_count": count,
        "part2_rebuilt_from_subparts": rebuilt,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--comments", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-branch", required=True)
    parser.add_argument("--expected-base", required=True)
    args = parser.parse_args()

    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    ticket = json.loads(event["issue"]["body"])
    if ticket.get("target_branch") != args.expected_branch:
        raise SystemExit("target branch mismatch")
    if ticket.get("base_commit") != args.expected_base:
        raise SystemExit("base commit mismatch")
    comments = json.loads(Path(args.comments).read_text(encoding="utf-8"))
    patch, evidence = reconstruct(ticket, comments)
    Path(args.output).write_bytes(patch)
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
