#!/usr/bin/env python3
"""Fail-closed free-first evidence guard for explicit paid acceptance.

This module runs in the GitHub control plane before any paid model request. It
finds the successful task-adaptive zero-call qualification and zero-cost free
model Canary artifacts for the exact candidate SHA, independently revalidates
their receipts, and then evaluates the canonical free-first preflight policy.
"""
from __future__ import annotations

import io
import json
import os
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Mapping

from v5_free_first_preflight import evaluate_free_first_preflight

ZERO_CALL_WORKFLOW = "v5-free-model-qualification.yml"
FREE_CANARY_WORKFLOW = "v5-zero-cost-free-canary.yml"
ZERO_CALL_ARTIFACT_PREFIX = "v5-top50-ortools-zero-call-"
FREE_CANARY_ARTIFACT_PREFIX = "v5-zero-cost-free-canary-"
ZERO_CALL_SCHEMA_VERSION = "v5-top50-task-adaptive-ortools-zero-call-qualification-2"
SELECTION_PRINCIPLES = [
    "concrete-problem-concrete-analysis",
    "dynamic-adaptation",
    "small-effort-large-return",
]


class PaidAcceptanceFreeFirstError(RuntimeError):
    """Raised before model execution when free-first evidence is incomplete."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PaidAcceptanceFreeFirstError(f"{field} must be an object")
    return value


def _api_request(url: str, token: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "expert-center-free-first-guard",
        },
    )


def _api_json(url: str, token: str) -> Mapping[str, Any]:
    try:
        with urllib.request.urlopen(_api_request(url, token), timeout=60) as response:
            value = json.loads(response.read())
    except Exception as exc:  # noqa: BLE001 - fail-closed control-plane boundary
        raise PaidAcceptanceFreeFirstError(f"GitHub API JSON request failed: {exc}") from exc
    return _mapping(value, "GitHub API response")


def _api_bytes(url: str, token: str) -> bytes:
    try:
        with urllib.request.urlopen(_api_request(url, token), timeout=60) as response:
            return response.read()
    except Exception as exc:  # noqa: BLE001 - fail-closed control-plane boundary
        raise PaidAcceptanceFreeFirstError(f"GitHub artifact download failed: {exc}") from exc


def _read_json_from_zip(data: bytes, candidates: tuple[str, ...]) -> Mapping[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            selected = next((name for name in candidates if name in names), None)
            if selected is None:
                suffixes = tuple("/" + name for name in candidates)
                selected = next(
                    (name for name in names if name.endswith(suffixes)),
                    None,
                )
            if selected is None:
                raise PaidAcceptanceFreeFirstError(
                    f"artifact does not contain any expected receipt: {candidates}"
                )
            value = json.loads(archive.read(selected))
    except PaidAcceptanceFreeFirstError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PaidAcceptanceFreeFirstError(f"cannot read artifact receipt: {exc}") from exc
    return _mapping(value, "artifact receipt")


def _workflow_runs_url(repo: str, workflow: str) -> str:
    encoded = urllib.parse.quote(workflow, safe="")
    return (
        f"https://api.github.com/repos/{repo}/actions/workflows/{encoded}/runs"
        "?status=success&per_page=100"
    )


def _run_url(repo: str, run_id: int) -> str:
    return f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"


def _artifacts_url(repo: str, run_id: int) -> str:
    return f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"


def _artifact_zip_url(repo: str, artifact_id: int) -> str:
    return f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip"


def _artifact_for_run(
    repo: str,
    run_id: int,
    prefix: str,
    token: str,
) -> tuple[int, Mapping[str, Any]]:
    payload = _api_json(_artifacts_url(repo, run_id), token)
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise PaidAcceptanceFreeFirstError("GitHub artifacts response is invalid")
    for row in artifacts:
        if not isinstance(row, Mapping):
            continue
        if row.get("expired") is True:
            continue
        name = str(row.get("name") or "")
        artifact_id = row.get("id")
        if name.startswith(prefix) and isinstance(artifact_id, int) and artifact_id > 0:
            return artifact_id, row
    raise PaidAcceptanceFreeFirstError(
        f"required artifact is missing for run {run_id}: {prefix}"
    )


def _validate_zero_call_receipt(
    receipt: Mapping[str, Any],
    expected_sha: str,
) -> None:
    expected = {
        "schema_version": ZERO_CALL_SCHEMA_VERSION,
        "target_sha": expected_sha,
        "status": "PASS",
        "model_calls": 0,
        "candidate_pool_authority": "decision-system-governance",
        "model_assignment_authority": "expert-assessment-center-ortools",
        "candidate_pool_size": 50,
        "popularity_period": "week",
        "optimizer": "ortools-cp-sat",
        "optimizer_required_status": "OPTIMAL",
        "selection_principles": SELECTION_PRINCIPLES,
        "task_adaptive_value_scoring_required": True,
        "semantic_keyword_routing_used": False,
        "cross_task_history_used": False,
        "primary_expert_count": 4,
        "warm_recovery_count": 4,
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "model_substitution_allowed": False,
        "production_ref_moved": False,
    }
    for key, required in expected.items():
        if receipt.get(key) != required:
            raise PaidAcceptanceFreeFirstError(f"zero-call receipt mismatch: {key}")


def _validate_free_canary_receipt(
    receipt: Mapping[str, Any],
    expected_sha: str,
) -> int:
    if receipt.get("schema_version") != "v5-zero-cost-free-model-canary-1":
        raise PaidAcceptanceFreeFirstError("free Canary schema mismatch")
    if receipt.get("status") != "PASS" or receipt.get("target_sha") != expected_sha:
        raise PaidAcceptanceFreeFirstError("free Canary target/status mismatch")
    if receipt.get("model_requests") != 1 or receipt.get("successful_model_calls") != 1:
        raise PaidAcceptanceFreeFirstError("free Canary call count mismatch")
    if receipt.get("paid_model_calls") != 0 or float(receipt.get("actual_cost_usd", -1)) != 0.0:
        raise PaidAcceptanceFreeFirstError("free Canary was not zero-cost")
    requested = str(receipt.get("requested_model") or "")
    if requested != "openrouter/free" and not requested.endswith(":free"):
        raise PaidAcceptanceFreeFirstError("free Canary did not request a free model")
    if receipt.get("synthetic_prompt_only") is not True:
        raise PaidAcceptanceFreeFirstError("free Canary used non-synthetic input")
    if receipt.get("formal_model_identity_qualified") is not False:
        raise PaidAcceptanceFreeFirstError("free Canary claimed formal model qualification")
    if receipt.get("production_ref_moved") is not False:
        raise PaidAcceptanceFreeFirstError("free Canary moved production")
    run_id_raw = str(receipt.get("zero_call_qualification_run_id") or "").strip()
    if not run_id_raw.isdigit() or int(run_id_raw) <= 0:
        raise PaidAcceptanceFreeFirstError("free Canary is not chained to zero-call qualification")
    return int(run_id_raw)


def _find_free_canary(
    repo: str,
    expected_sha: str,
    token: str,
) -> tuple[int, int, Mapping[str, Any]]:
    payload = _api_json(_workflow_runs_url(repo, FREE_CANARY_WORKFLOW), token)
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise PaidAcceptanceFreeFirstError("free Canary workflow-runs response is invalid")
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        run_id = run.get("id")
        if not isinstance(run_id, int) or run_id <= 0:
            continue
        try:
            artifact_id, _ = _artifact_for_run(
                repo,
                run_id,
                FREE_CANARY_ARTIFACT_PREFIX,
                token,
            )
            receipt = _read_json_from_zip(
                _api_bytes(_artifact_zip_url(repo, artifact_id), token),
                ("free-canary.json", "free-canary/free-canary.json"),
            )
            if receipt.get("target_sha") != expected_sha:
                continue
            _validate_free_canary_receipt(receipt, expected_sha)
            return run_id, artifact_id, receipt
        except PaidAcceptanceFreeFirstError:
            continue
    raise PaidAcceptanceFreeFirstError(
        f"no successful zero-cost free Canary evidence found for {expected_sha}"
    )


def enforce_free_first(
    *,
    output_dir: Path,
    expected_sha: str,
    repository: str | None = None,
    token: str | None = None,
) -> Mapping[str, Any]:
    sha = str(expected_sha or "").strip()
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise PaidAcceptanceFreeFirstError("authoritative candidate SHA is invalid")
    repo = str(repository or os.environ.get("GITHUB_REPOSITORY") or "").strip()
    auth = str(token or os.environ.get("GITHUB_TOKEN") or "").strip()
    if "/" not in repo or not auth:
        raise PaidAcceptanceFreeFirstError("GitHub control-plane identity/token is missing")

    canary_run_id, canary_artifact_id, canary = _find_free_canary(repo, sha, auth)
    zero_call_run_id = _validate_free_canary_receipt(canary, sha)
    zero_run = _api_json(_run_url(repo, zero_call_run_id), auth)
    if zero_run.get("conclusion") != "success":
        raise PaidAcceptanceFreeFirstError("zero-call qualification run did not succeed")
    if str(zero_run.get("head_sha") or "") != sha:
        raise PaidAcceptanceFreeFirstError("zero-call qualification run SHA mismatch")
    zero_artifact_id, _ = _artifact_for_run(
        repo,
        zero_call_run_id,
        ZERO_CALL_ARTIFACT_PREFIX,
        auth,
    )
    zero_receipt = _read_json_from_zip(
        _api_bytes(_artifact_zip_url(repo, zero_artifact_id), auth),
        ("free-qualification.json", "free-qualification/free-qualification.json"),
    )
    _validate_zero_call_receipt(zero_receipt, sha)

    combined = {
        "schema_version": "v5-free-first-preflight-1",
        "target_sha": sha,
        "simulation": {
            "status": "PASS",
            "model_calls": 0,
            "paid_model_calls": 0,
            "workflow_run_id": zero_call_run_id,
            "artifact_id": zero_artifact_id,
        },
        "free_canary": dict(canary),
        "paid_acceptance_triggered": False,
        "production_ref_moved": False,
        "evidence": {
            "zero_call_run_id": zero_call_run_id,
            "zero_call_artifact_id": zero_artifact_id,
            "free_canary_run_id": canary_run_id,
            "free_canary_artifact_id": canary_artifact_id,
            "task_adaptive_zero_call_schema_version": ZERO_CALL_SCHEMA_VERSION,
            "selection_principles": SELECTION_PRINCIPLES,
        },
    }
    verdict = evaluate_free_first_preflight(combined, expected_sha=sha)
    if verdict.get("status") != "PASS" or verdict.get("paid_acceptance_allowed") is not True:
        raise PaidAcceptanceFreeFirstError(
            "free-first preflight rejected paid acceptance: "
            + ",".join(str(value) for value in verdict.get("reasons", []))
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "free-first-preflight-receipt.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "free-first-preflight-verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return verdict


__all__ = [
    "PaidAcceptanceFreeFirstError",
    "enforce_free_first",
]
