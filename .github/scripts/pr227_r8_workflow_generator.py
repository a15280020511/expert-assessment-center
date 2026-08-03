#!/usr/bin/env python3
"""Generate PR #227 R8 acceptance workflow with full ancestry checkout."""
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
BRANCH = "acceptance/pr227-final-paid-20260803-r8"
TARGET_SHA = "2a0d1b5d2d8bc77b52a7af5a4d257843675c662e"
TEMPLATE_REF = "acceptance/pr227-final-paid-20260803-r7"
TEMPLATE_PATH = ".github/workflows/pr227-production-paid-acceptance-r7.yml"
GENERATED_PATH = ".github/workflows/pr227-production-paid-acceptance-r8.yml"
RECEIPT_PATH = "acceptance-evidence/pr227-r8-workflow-generator.json"


def api(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> bytes:
    data = None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pr227-r8-workflow-generator",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def content(path: str, ref: str) -> dict[str, Any]:
    encoded_path = urllib.parse.quote(path, safe="/")
    encoded_ref = urllib.parse.quote(ref, safe="")
    raw = api(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{encoded_path}?ref={encoded_ref}"
    )
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("unexpected GitHub contents response")
    return value


def transform(source: str) -> str:
    generated = source.replace("R7", "R8").replace("r7", "r8")
    replacements = {
        "pr227-production-paid-acceptance-7": "pr227-production-paid-acceptance-8",
        "final-pr227-authoritative-production-qualification-after-evidence-contract-remediation": (
            "final-pr227-authoritative-production-qualification-after-authorization-ancestry-fix"
        ),
    }
    for old, new in replacements.items():
        if generated.count(old) < 1:
            raise RuntimeError(f"required replacement missing: {old}")
        generated = generated.replace(old, new)
    if generated.count("          fetch-depth: 3") != 1:
        raise RuntimeError("authorization checkout fetch-depth contract changed")
    generated = generated.replace("          fetch-depth: 3", "          fetch-depth: 0", 1)
    forbidden = ["R7", "r7", "pr227-production-paid-acceptance-7", "fetch-depth: 3"]
    residue = [value for value in forbidden if value in generated]
    if residue:
        raise RuntimeError(f"R7 residue remains: {residue}")
    required = [
        BRANCH,
        ".github/v5-paid-production-request-r8.json",
        TARGET_SHA,
        "pr227-production-paid-acceptance-8",
        "fetch-depth: 0",
        "pr227-production-paid-r8-receipt-1",
        "acceptance-evidence/pr227-production-paid-r8-result.json",
    ]
    missing = [value for value in required if value not in generated]
    if missing:
        raise RuntimeError(f"required R8 contract missing: {missing}")
    binding = "--independent-revalidation-file independent-revalidation.json"
    if generated.count(binding) != 2:
        raise RuntimeError("R8 must retain exactly two independent revalidation bindings")
    return generated


def create_blob(data: bytes) -> str:
    raw = api(
        f"https://api.github.com/repos/{REPOSITORY}/git/blobs",
        method="POST",
        payload={"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
    )
    value = json.loads(raw.decode("utf-8"))
    sha = str(value.get("sha") or "")
    if not sha:
        raise RuntimeError("missing generated blob SHA")
    return sha


def persist(receipt: dict[str, Any]) -> None:
    encoded = urllib.parse.quote(RECEIPT_PATH, safe="/")
    url = f"https://api.github.com/repos/{REPOSITORY}/contents/{encoded}"
    existing_sha = None
    try:
        existing_sha = content(RECEIPT_PATH, BRANCH).get("sha")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    payload: dict[str, Any] = {
        "message": "test: persist PR227 R8 workflow generator receipt",
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
    source = base64.b64decode(template["content"]).decode("utf-8")
    generated = transform(source)
    data = generated.encode("utf-8")
    blob_sha = create_blob(data)
    receipt = {
        "schema_version": "pr227-r8-workflow-generator-1",
        "status": "PASS",
        "template_ref": TEMPLATE_REF,
        "template_blob_sha": template.get("sha"),
        "target_sha": TARGET_SHA,
        "generated_path": GENERATED_PATH,
        "generated_blob_sha": blob_sha,
        "generated_sha256": hashlib.sha256(data).hexdigest(),
        "generated_size_bytes": len(data),
        "authorization_fetch_depth": 0,
        "independent_revalidation_bindings": 2,
        "maximum_total_calls": 4,
        "maximum_recovery_calls": 0,
        "maximum_cost_usd": 0.25,
        "chat_model_calls": 0,
        "paid_model_calls": 0,
        "generator_commit_sha": os.environ.get("GITHUB_SHA", ""),
    }
    Path("pr227-r8-workflow-generator-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    persist(receipt)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
