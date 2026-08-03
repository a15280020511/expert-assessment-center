#!/usr/bin/env python3
"""Generate the bounded PR #227 R11 paid-acceptance workflow."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GH_TOKEN"]
BRANCH = "acceptance/pr227-final-paid-20260803-r11"
TARGET_SHA = "0383d2ef80f1890306bc0bc5f7b37836cb2579ca"
OLD_TARGET_SHA = "140ea4f8ba15f4650ce5de30d3659de58c78c6b7"
TEMPLATE_REF = "acceptance/pr227-final-paid-20260803-r10"
TEMPLATE_PATH = ".github/workflows/pr227-production-paid-acceptance-r10.yml"
GENERATED_PATH = ".github/workflows/pr227-production-paid-acceptance-r11.yml"
RECEIPT_PATH = "acceptance-evidence/pr227-r11-workflow-generator.json"


def api(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> bytes:
    data = None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pr227-r11-workflow-generator",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def content(path: str, ref: str) -> dict[str, Any]:
    path_q = urllib.parse.quote(path, safe="/")
    ref_q = urllib.parse.quote(ref, safe="")
    value = json.loads(api(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{path_q}?ref={ref_q}"
    ).decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("unexpected GitHub contents response")
    return value


def transform(source: str) -> str:
    generated = source.replace("R10", "R11").replace("r10", "r11")
    replacements = {
        OLD_TARGET_SHA: TARGET_SHA,
        "pr227-production-paid-acceptance-10": "pr227-production-paid-acceptance-11",
        "final-pr227-authoritative-production-qualification-after-ticket-token-forwarding": (
            "final-pr227-authoritative-production-qualification-after-governance-token-envelope"
        ),
    }
    for old, new in replacements.items():
        count = generated.count(old)
        if count < 1:
            raise RuntimeError(f"required replacement missing: {old}")
        generated = generated.replace(old, new)
    forbidden = ["R10", "r10", OLD_TARGET_SHA, "pr227-production-paid-acceptance-10"]
    residue = [value for value in forbidden if value in generated]
    if residue:
        raise RuntimeError(f"R10 residue remains: {residue}")
    required = [
        BRANCH,
        ".github/v5-paid-production-request-r11.json",
        TARGET_SHA,
        "pr227-production-paid-acceptance-11",
        'MAX_COMPLETION_TOKENS: "8000"',
        '--max-completion-tokens "$MAX_COMPLETION_TOKENS"',
        ".max_completion_tokens == 8000",
        "pr227-production-paid-r11-receipt-1",
        "acceptance-evidence/pr227-production-paid-r11-result.json",
    ]
    missing = [value for value in required if value not in generated]
    if missing:
        raise RuntimeError(f"required R11 contract missing: {missing}")
    if generated.count("--independent-revalidation-file independent-revalidation.json") != 2:
        raise RuntimeError("R11 must retain two independent revalidation bindings")
    return generated


def create_blob(data: bytes) -> str:
    value = json.loads(api(
        f"https://api.github.com/repos/{REPOSITORY}/git/blobs",
        method="POST",
        payload={"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
    ).decode("utf-8"))
    sha = str(value.get("sha") or "")
    if not sha:
        raise RuntimeError("missing generated blob SHA")
    return sha


def persist(receipt: dict[str, Any]) -> None:
    path_q = urllib.parse.quote(RECEIPT_PATH, safe="/")
    url = f"https://api.github.com/repos/{REPOSITORY}/contents/{path_q}"
    existing_sha = None
    try:
        existing_sha = content(RECEIPT_PATH, BRANCH).get("sha")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    payload: dict[str, Any] = {
        "message": "test: persist PR227 R11 workflow generator receipt",
        "content": base64.b64encode(
            (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        ).decode("ascii"),
        "branch": BRANCH,
    }
    if existing_sha:
        payload["sha"] = existing_sha
    api(url, method="PUT", payload=payload)


def main() -> int:
    template = content(TEMPLATE_PATH, TEMPLATE_REF)
    generated = transform(base64.b64decode(template["content"]).decode("utf-8"))
    data = generated.encode("utf-8")
    receipt = {
        "schema_version": "pr227-r11-workflow-generator-1",
        "status": "PASS",
        "template_ref": TEMPLATE_REF,
        "template_blob_sha": template.get("sha"),
        "target_sha": TARGET_SHA,
        "generated_path": GENERATED_PATH,
        "generated_blob_sha": create_blob(data),
        "generated_sha256": hashlib.sha256(data).hexdigest(),
        "generated_size_bytes": len(data),
        "max_completion_tokens": 8000,
        "authorization_fetch_depth": 0,
        "independent_revalidation_bindings": 2,
        "maximum_total_calls": 4,
        "maximum_recovery_calls": 0,
        "maximum_cost_usd": 0.25,
        "chat_model_calls": 0,
        "paid_model_calls": 0,
        "generator_commit_sha": os.environ.get("GITHUB_SHA", ""),
    }
    Path("pr227-r11-workflow-generator-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    persist(receipt)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
