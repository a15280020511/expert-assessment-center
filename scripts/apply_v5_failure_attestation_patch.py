from pathlib import Path


path = Path("open-model-market/v5_evidence_bundle.py")
text = path.read_text(encoding="utf-8")
old = '''    report = root / "expert-team-report.md"
    manifest = root / "artifact-manifest.json"
    bundle = root / "evidence-bundle.json"
    if not report.is_file() or not manifest.is_file() or not bundle.is_file() or not final_status_file.is_file():
        raise RuntimeError("report, manifest, evidence bundle, and final status must exist")
    if not primary_artifact_id or not primary_artifact_digest:
        raise RuntimeError("primary artifact identity is required")
    diagnosis = _load(root / "execution-diagnosis.json", {})
    evidence = _load(bundle, {})
    return {
'''
new = '''    report = root / "expert-team-report.md"
    manifest = root / "artifact-manifest.json"
    bundle = root / "evidence-bundle.json"
    diagnosis_path = root / "execution-diagnosis.json"
    normalized_audit_status = str(audit_status or "FAIL").upper()
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
    diagnosis = _load(diagnosis_path, {})
    evidence = _load(bundle, {})
    report_present = report.is_file()
    return {
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one attestation precondition block, found {text.count(old)}")
text = text.replace(old, new, 1)
old_hash = '''        "audit_status": audit_status,
        "diagnosis_status": diagnosis.get("status") if isinstance(diagnosis, Mapping) else None,
        "evidence_input_sha256": evidence.get("input_sha256") if isinstance(evidence, Mapping) else None,
        "business_evidence_frozen_before_upload": bool(
            evidence.get("business_evidence_frozen") if isinstance(evidence, Mapping) else False
        ),
        "report_sha256": sha256_file(report),
        "manifest_sha256": sha256_file(manifest),
'''
new_hash = '''        "audit_status": normalized_audit_status,
        "diagnosis_status": diagnosis.get("status") if isinstance(diagnosis, Mapping) else None,
        "evidence_input_sha256": evidence.get("input_sha256") if isinstance(evidence, Mapping) else None,
        "business_evidence_frozen_before_upload": bool(
            evidence.get("business_evidence_frozen") if isinstance(evidence, Mapping) else False
        ),
        "report_required": report_required,
        "report_present": report_present,
        "report_sha256": sha256_file(report) if report_present else None,
        "manifest_sha256": sha256_file(manifest),
'''
if text.count(old_hash) != 1:
    raise SystemExit(f"expected one attestation hash block, found {text.count(old_hash)}")
path.write_text(text.replace(old_hash, new_hash, 1), encoding="utf-8")
