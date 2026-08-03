#!/usr/bin/env python3
"""Generate the bounded PR #227 R7 paid-acceptance workflow from R6."""
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
BRANCH = "acceptance/pr227-final-paid-20260803-r7"
TARGET_SHA = "2a0d1b5d2d8bc77b52a7af5a4d257843675c662e"
OLD_TARGET_SHA = "9360c9f05892e02c42c84d28f1cf10dc2461ebcb"
TEMPLATE_REF = "acceptance/pr227-final-paid-20260803-r6"
TEMPLATE_PATH = ".github/workflows/pr227-production-paid-acceptance-r6.yml"
GENERATED_PATH = ".github/workflows/pr227-production-paid-acceptance-r7.yml"
RECEIPT_PATH = "acceptance-evidence/pr227-r7-workflow-generator.json"


def api(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> bytes:
    data = None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pr227-r7-workflow-generator-v2",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def repository_content(path: str, ref: str) -> dict[str, Any]:
    encoded_path = urllib.parse.quote(path, safe="/")
    encoded_ref = urllib.parse.quote(ref, safe="")
    raw = api(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{encoded_path}"
        f"?ref={encoded_ref}"
    )
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected contents response for {path}")
    return value


def transform(source: str) -> str:
    generated = source.replace("R6", "R7").replace("r6", "r7")
    replacements = {
        OLD_TARGET_SHA: TARGET_SHA,
        "pr227-production-paid-acceptance-6": "pr227-production-paid-acceptance-7",
        "final-pr227-authoritative-production-qualification-after-route-envelope": (
            "final-pr227-authoritative-production-qualification-after-evidence-contract-remediation"
        ),
    }
    for old, new in replacements.items():
        count = generated.count(old)
        if count < 1:
            raise RuntimeError(f"required R7 replacement source missing: {old}")
        generated = generated.replace(old, new)

    final_status_marker = (
        '            --artifact-digest "$PRIMARY_ARTIFACT_DIGEST" \\\n'
        '            | tee final-status.md'
    )
    final_status_replacement = (
        '            --artifact-digest "$PRIMARY_ARTIFACT_DIGEST" \\\n'
        '            --independent-revalidation-file independent-revalidation.json \\\n'
        '            | tee final-status.md'
    )
    if generated.count(final_status_marker) != 1:
        raise RuntimeError("final-status independent-revalidation insertion point changed")
    generated = generated.replace(
        final_status_marker,
        final_status_replacement,
        1,
    )

    attestation_marker = (
        '            --final-status-file final-status.md \\\n'
        '            --output final-attestation.json'
    )
    attestation_replacement = (
        '            --final-status-file final-status.md \\\n'
        '            --independent-revalidation-file independent-revalidation.json \\\n'
        '            --output final-attestation.json'
    )
    if generated.count(attestation_marker) != 1:
        raise RuntimeError("attestation independent-revalidation insertion point changed")
    generated = generated.replace(
        attestation_marker,
        attestation_replacement,
        1,
    )

    forbidden = [
        "R6",
        "r6",
        OLD_TARGET_SHA,
        "pr227-production-paid-acceptance-6",
        "after-route-envelope",
    ]
    remaining = [value for value in forbidden if value in generated]
    if remaining:
        raise RuntimeError(f"R6 residue remains: {remaining}")
    required = [
        BRANCH,
        ".github/v5-paid-production-request-r7.json",
        TARGET_SHA,
        "pr227-production-paid-acceptance-7",
        "pr227-production-paid-r7-receipt-1",
        "acceptance-evidence/pr227-production-paid-r7-result.json",
    ]
    missing = [value for value in required if value not in generated]
    if missing:
        raise RuntimeError(f"required R7 contract missing: {missing}")
    binding = "--independent-revalidation-file independent-revalidation.json"
    if generated.count(binding) != 2:
        raise RuntimeError("R7 must bind independent revalidation exactly twice")
    return generated


def create_blob(content: bytes) -> str:
    raw = api(
        f"https://api.github.com/repos/{REPOSITORY}/git/blobs",
        method="POST",
        payload={
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        },
    )
    value = json.loads(raw.decode("utf-8"))
    sha = str(value.get("sha") or "")
    if not sha:
        raise RuntimeError("GitHub did not return generated blob SHA")
    return sha


def persist_receipt(receipt: dict[str, Any]) -> None:
    encoded_path = urllib.parse.quote(RECEIPT_PATH, safe="/")
    url = f"https://api.github.com/repos/{REPOSITORY}/contents/{encoded_path}"
    existing_sha = None
    try:
        existing = repository_content(RECEIPT_PATH, BRANCH)
        existing_sha = existing.get("sha")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    payload: dict[str, Any] = {
        "message": "test: persist PR227 R7 workflow generator receipt",
        "content": base64.b64encode(
            (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        ).decode("ascii"),
        "branch": BRANCH,
    }
    if existing_sha:
        payload["sha"] = existing_sha
    api(url, method="PUT", payload=payload)


def main() -> int:
    template = repository_content(TEMPLATE_PATH, TEMPLATE_REF)
    source = base64.b64decode(template["content"]).decode("utf-8")
    generated = transform(source)
    content = generated.encode("utf-8")
    blob_sha = create_blob(content)
    receipt = {
        "schema_version": "pr227-r7-workflow-generator-2",
        "status": "PASS",
        "template_ref": TEMPLATE_REF,
        "template_path": TEMPLATE_PATH,
        "template_blob_sha": template.get("sha"),
        "target_sha": TARGET_SHA,
        "generated_path": GENERATED_PATH,
        "generated_sha256": hashlib.sha256(content).hexdigest(),
        "generated_size_bytes": len(content),
        "generated_blob_sha": blob_sha,
        "independent_revalidation_bindings": 2,
        "maximum_total_calls": 4,
        "maximum_recovery_calls": 0,
        "maximum_cost_usd": 0.25,
        "chat_model_calls": 0,
        "paid_model_calls": 0,
        "generator_commit_sha": os.environ.get("GITHUB_SHA", ""),
    }
    Path("pr227-r7-workflow-generator-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    persist_receipt(receipt)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
