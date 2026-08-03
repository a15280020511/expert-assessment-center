#!/usr/bin/env python3
"""One-shot PR #227 R6 final-status and attestation complexity refactor."""
from __future__ import annotations

from pathlib import Path


PATH = Path("open-model-market/v5_evidence_bundle.py")


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{PATH}: expected one replacement, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "def build_final_status_record(\n",
        '''def _final_status_context(
    inputs: FinalStatusInputs,
    *,
    audit_outcome: str,
    manifest_outcome: str,
    ticket_upload_outcome: str,
    independent_revalidation: Mapping[str, Any] | None,
) -> tuple[
    Mapping[str, Any],
    list[Any],
    list[Any],
    str,
    dict[str, Any],
    str,
]:
    audit = inputs.diagnosis
    failures = list(audit.get("failures") or [])
    degradations = list(audit.get("degradations") or [])
    status = str(audit.get("status") or "FAIL")
    independent = (
        dict(independent_revalidation)
        if isinstance(independent_revalidation, Mapping)
        else {}
    )
    independent_status = str(independent.get("status") or "MISSING").upper()
    if audit_outcome != "success":
        status = "FAIL"
        failures.append(f"V5 audit step outcome is {audit_outcome}")
    if manifest_outcome != "success":
        status = "FAIL"
        failures.append(
            f"primary artifact manifest step outcome is {manifest_outcome}"
        )
    if ticket_upload_outcome != "success":
        status = "FAIL"
        failures.append(
            "primary ticket artifact upload outcome is "
            f"{ticket_upload_outcome}"
        )
    if status in {"PASS", "DEGRADED"} and independent_status != "PASS":
        status = "FAIL"
        failures.append(
            "independent artifact revalidation is not PASS: "
            f"{independent_status}"
        )
    return (
        audit,
        failures,
        degradations,
        status,
        independent,
        independent_status,
    )


def build_final_status_record(
''',
    )
    old = '''    audit = inputs.diagnosis
    failures = list(audit.get("failures") or [])
    degradations = list(audit.get("degradations") or [])
    status = str(audit.get("status") or "FAIL")
    independent = (
        dict(independent_revalidation)
        if isinstance(independent_revalidation, Mapping)
        else {}
    )
    independent_status = str(independent.get("status") or "MISSING").upper()
    if audit_outcome != "success":
        status = "FAIL"
        failures.append(f"V5 audit step outcome is {audit_outcome}")
    if manifest_outcome != "success":
        status = "FAIL"
        failures.append(f"primary artifact manifest step outcome is {manifest_outcome}")
    if ticket_upload_outcome != "success":
        status = "FAIL"
        failures.append(f"primary ticket artifact upload outcome is {ticket_upload_outcome}")
    if status in {"PASS", "DEGRADED"} and independent_status != "PASS":
        status = "FAIL"
        failures.append(
            "independent artifact revalidation is not PASS: "
            f"{independent_status}"
        )
    summary = inputs.ledger.get("summary")
'''
    new = '''    (
        audit,
        failures,
        degradations,
        status,
        independent,
        independent_status,
    ) = _final_status_context(
        inputs,
        audit_outcome=audit_outcome,
        manifest_outcome=manifest_outcome,
        ticket_upload_outcome=ticket_upload_outcome,
        independent_revalidation=independent_revalidation,
    )
    summary = inputs.ledger.get("summary")
'''
    text = replace_once(text, old, new)

    text = replace_once(
        text,
        "def build_final_attestation_record(\n",
        '''def _load_independent_attestation(
    path: Path | None,
) -> tuple[dict[str, Any], bool]:
    raw = load_json_or_default(path, {}) if path is not None else {}
    independent = dict(raw) if isinstance(raw, Mapping) else {}
    valid = (
        independent.get("schema_version")
        == "v5-independent-artifact-revalidation-3"
        and independent.get("status") == "PASS"
        and independent.get("recomputed_from_primitive_evidence") is True
        and independent.get("paid_acceptance_verdict_used_as_source") is False
    )
    return independent, valid


def _require_attestation_inputs(
    *,
    manifest: Path,
    bundle: Path,
    final_status_file: Path,
    report: Path,
    diagnosis_path: Path,
    report_required: bool,
    normalized_audit_status: str,
    primary_artifact_id: str,
    primary_artifact_digest: str,
) -> None:
    required_paths = (manifest, bundle, final_status_file)
    if not all(item.is_file() for item in required_paths):
        raise RuntimeError("manifest, evidence bundle, and final status must exist")
    if report_required and not report.is_file():
        raise RuntimeError("successful or degraded execution requires a report")
    if normalized_audit_status == "FAIL" and not diagnosis_path.is_file():
        raise RuntimeError("failed execution requires deterministic diagnosis evidence")
    if not primary_artifact_id or not primary_artifact_digest:
        raise RuntimeError("primary artifact identity is required")


def _attestation_status(
    *,
    normalized_audit_status: str,
    diagnosis_status: str,
    report_present: bool,
    evidence_frozen: bool,
    independent_valid: bool,
) -> str:
    valid = (
        normalized_audit_status in {"PASS", "DEGRADED"}
        and diagnosis_status == normalized_audit_status
        and report_present
        and evidence_frozen
        and independent_valid
    )
    return normalized_audit_status if valid else "FAIL"


def build_final_attestation_record(
''',
    )
    old = '''    normalized_audit_status = str(audit_status or "FAIL").upper()
    independent_path = independent_revalidation_file
    independent = (
        load_json_or_default(independent_path, {})
        if independent_path is not None
        else {}
    )
    independent = independent if isinstance(independent, Mapping) else {}
    independent_valid = (
        independent.get("schema_version")
        == "v5-independent-artifact-revalidation-3"
        and independent.get("status") == "PASS"
        and independent.get("recomputed_from_primitive_evidence") is True
        and independent.get("paid_acceptance_verdict_used_as_source") is False
    )
    report_required = normalized_audit_status in {"PASS", "DEGRADED"}
    required_paths = (manifest, bundle, final_status_file)
    if not all(item.is_file() for item in required_paths):
        raise RuntimeError("manifest, evidence bundle, and final status must exist")
    if report_required and not report.is_file():
        raise RuntimeError("successful or degraded execution requires a report")
    if normalized_audit_status == "FAIL" and not diagnosis_path.is_file():
        raise RuntimeError("failed execution requires deterministic diagnosis evidence")
    if not primary_artifact_id or not primary_artifact_digest:
        raise RuntimeError("primary artifact identity is required")
    diagnosis = load_json_or_default(diagnosis_path, {})
'''
    new = '''    normalized_audit_status = str(audit_status or "FAIL").upper()
    independent_path = independent_revalidation_file
    independent, independent_valid = _load_independent_attestation(independent_path)
    report_required = normalized_audit_status in {"PASS", "DEGRADED"}
    _require_attestation_inputs(
        manifest=manifest,
        bundle=bundle,
        final_status_file=final_status_file,
        report=report,
        diagnosis_path=diagnosis_path,
        report_required=report_required,
        normalized_audit_status=normalized_audit_status,
        primary_artifact_id=primary_artifact_id,
        primary_artifact_digest=primary_artifact_digest,
    )
    diagnosis = load_json_or_default(diagnosis_path, {})
'''
    text = replace_once(text, old, new)
    old = '''    attestation_status = (
        normalized_audit_status
        if normalized_audit_status in {"PASS", "DEGRADED"}
        and diagnosis_status == normalized_audit_status
        and report_present
        and evidence_frozen
        and independent_valid
        else "FAIL"
    )
'''
    new = '''    attestation_status = _attestation_status(
        normalized_audit_status=normalized_audit_status,
        diagnosis_status=diagnosis_status,
        report_present=report_present,
        evidence_frozen=evidence_frozen,
        independent_valid=independent_valid,
    )
'''
    text = replace_once(text, old, new)
    PATH.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
