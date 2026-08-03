#!/usr/bin/env python3
"""Generate PR #227 R9 acceptance workflow with an explicit 8k output envelope."""
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
BRANCH = "acceptance/pr227-final-paid-20260803-r9"
TARGET_SHA = "2a0d1b5d2d8bc77b52a7af5a4d257843675c662e"
TEMPLATE_REF = "acceptance/pr227-final-paid-20260803-r8"
TEMPLATE_PATH = ".github/workflows/pr227-production-paid-acceptance-r8.yml"
GENERATED_PATH = ".github/workflows/pr227-production-paid-acceptance-r9.yml"
RECEIPT_PATH = "acceptance-evidence/pr227-r9-workflow-generator.json"
MAX_COMPLETION_TOKENS = 8000


def api(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> bytes:
    data = None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pr227-r9-workflow-generator",
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


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def transform(source: str) -> str:
    generated = source.replace("R8", "R9").replace("r8", "r9")
    for old, new in {
        "pr227-production-paid-acceptance-8": "pr227-production-paid-acceptance-9",
        "final-pr227-authoritative-production-qualification-after-authorization-ancestry-fix": (
            "final-pr227-authoritative-production-qualification-with-explicit-token-envelope"
        ),
    }.items():
        if old not in generated:
            raise RuntimeError(f"required replacement missing: {old}")
        generated = generated.replace(old, new)

    generated = replace_once(
        generated,
        '              "maximum_recovery_calls",\n              "maximum_total_calls",',
        '              "max_completion_tokens",\n              "maximum_recovery_calls",\n              "maximum_total_calls",',
        "request key authorization",
    )
    generated = replace_once(
        generated,
        '            .cost_cap_usd == 0.25 and\n            (.task | type == "object")',
        '            .cost_cap_usd == 0.25 and\n            .max_completion_tokens == 8000 and\n            (.task | type == "object")',
        "request token validation",
    )
    generated = replace_once(
        generated,
        '      MAXIMUM_COST_USD: "0.25"',
        '      MAXIMUM_COST_USD: "0.25"\n      MAX_COMPLETION_TOKENS: "8000"',
        "runtime token environment",
    )
    generated = replace_once(
        generated,
        '            --cost-anomaly-usd "$MAXIMUM_COST_USD" \\\n            --require-live-catalog',
        '            --cost-anomaly-usd "$MAXIMUM_COST_USD" \\\n            --max-completion-tokens "$MAX_COMPLETION_TOKENS" \\\n            --require-live-catalog',
        "runtime token argument",
    )
    generated = replace_once(
        generated,
        '              "actual_cost_usd": independent.get("actual_cost_usd"),',
        '              "actual_cost_usd": independent.get("actual_cost_usd"),\n              "max_completion_tokens": 8000,',
        "receipt token evidence",
    )

    forbidden = ["R8", "r8", "pr227-production-paid-acceptance-8"]
    residue = [value for value in forbidden if value in generated]
    if residue:
        raise RuntimeError(f"R8 residue remains: {residue}")
    required = [
        BRANCH,
        ".github/v5-paid-production-request-r9.json",
        TARGET_SHA,
        "pr227-production-paid-acceptance-9",
        'MAX_COMPLETION_TOKENS: "8000"',
        '--max-completion-tokens "$MAX_COMPLETION_TOKENS"',
        ".max_completion_tokens == 8000",
        "pr227-production-paid-r9-receipt-1",
        "acceptance-evidence/pr227-production-paid-r9-result.json",
    ]
    missing = [value for value in required if value not in generated]
    if missing:
        raise RuntimeError(f"required R9 contract missing: {missing}")
    if generated.count("--independent-revalidation-file independent-revalidation.json") != 2:
        raise RuntimeError("R9 must retain two independent revalidation bindings")
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
        "message": "test: persist PR227 R9 workflow generator receipt",
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
        "schema_version": "pr227-r9-workflow-generator-1",
        "status": "PASS",
        "template_ref": TEMPLATE_REF,
        "template_blob_sha": template.get("sha"),
        "target_sha": TARGET_SHA,
        "generated_path": GENERATED_PATH,
        "generated_blob_sha": create_blob(data),
        "generated_sha256": hashlib.sha256(data).hexdigest(),
        "generated_size_bytes": len(data),
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
        "authorization_fetch_depth": 0,
        "independent_revalidation_bindings": 2,
        "maximum_total_calls": 4,
        "maximum_recovery_calls": 0,
        "maximum_cost_usd": 0.25,
        "chat_model_calls": 0,
        "paid_model_calls": 0,
        "generator_commit_sha": os.environ.get("GITHUB_SHA", ""),
    }
    Path("pr227-r9-workflow-generator-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    persist(receipt)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
