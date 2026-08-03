from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "tools" / "repository_audit.py"
text = AUDIT.read_text(encoding="utf-8")

old = '''HISTORICAL = {
    "MIGRATION.md",
    "MIGRATION_MANIFEST.json",
    "MIGRATION_PROVENANCE.json",
    "RECOVERY.md",
    "governance-compatibility.json",
}
'''
new = '''HISTORICAL = {
    "MIGRATION.md",
    "MIGRATION_MANIFEST.json",
    "MIGRATION_PROVENANCE.json",
    "RECOVERY.md",
    "governance-compatibility.json",
}
APPROVED_NETWORK_MODULES = {
    "open-model-market/openrouter_api.py",
    "open-model-market/v5_admission_lock.py",
    "open-model-market/v5_issue_ticket.py",
}
DISALLOWED_NETWORK_IMPORTS = {
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "websocket",
    "selenium",
    "playwright",
    "ftplib",
    "smtplib",
    "paramiko",
}
'''
if text.count(old) != 1:
    raise RuntimeError("repository audit constant anchor mismatch")
text = text.replace(old, new, 1)

anchor = '''def _inspect_structured_file(
    state: AuditState,
    rel: str,
    kind: str,
    text: str,
) -> None:
'''
insert = '''def _network_boundary_findings(
    rel: str,
    kind: str,
    text: str,
    imports: set[str],
) -> list[Finding]:
    if kind != "python" or "tests" in Path(rel).parts or rel == "tools/repository_audit.py":
        return []
    findings: list[Finding] = []
    disallowed = sorted(DISALLOWED_NETWORK_IMPORTS.intersection(imports))
    if disallowed:
        findings.append(
            Finding(
                "critical",
                "ARCH-UNAPPROVED-NETWORK-CLIENT",
                rel,
                1,
                f"unapproved network client imports: {disallowed}",
            )
        )
    if "urllib.request.urlopen" in text and rel not in APPROVED_NETWORK_MODULES:
        line = text[: text.index("urllib.request.urlopen")].count("\\n") + 1
        findings.append(
            Finding(
                "critical",
                "ARCH-UNAPPROVED-NETWORK-EGRESS",
                rel,
                line,
                "direct network egress exists outside the approved model/control-plane modules",
            )
        )
    if (
        rel != "open-model-market/v5_no_tools_policy.py"
        and re.search(r"^FORBIDDEN_(?:REQUEST_)?FIELDS\\s*=", text, re.MULTILINE)
    ):
        line = text[: re.search(
            r"^FORBIDDEN_(?:REQUEST_)?FIELDS\\s*=", text, re.MULTILINE
        ).start()].count("\\n") + 1
        findings.append(
            Finding(
                "high",
                "ARCH-DUPLICATE-NO-TOOLS-POLICY",
                rel,
                line,
                "tool-field prohibition is duplicated instead of importing the constitutional policy",
            )
        )
    if "MAX_TASK_CHARS" in text:
        line = text[: text.index("MAX_TASK_CHARS")].count("\\n") + 1
        findings.append(
            Finding(
                "high",
                "ARCH-LOCAL-TASK-CHARACTER-GATE",
                rel,
                line,
                "local task character gate conflicts with provider-native capacity matching",
            )
        )
    return findings


'''
if text.count(anchor) != 1:
    raise RuntimeError("repository audit function anchor mismatch")
text = text.replace(anchor, insert + anchor, 1)

old = '''    if kind == "python":
        py_findings, imports, py_metrics = audit_python(rel, text)
        state.findings.extend(py_findings)
        state.imports_by_file[rel] = imports
'''
new = '''    if kind == "python":
        py_findings, imports, py_metrics = audit_python(rel, text)
        state.findings.extend(py_findings)
        state.findings.extend(_network_boundary_findings(rel, kind, text, imports))
        state.imports_by_file[rel] = imports
'''
if text.count(old) != 1:
    raise RuntimeError("repository audit integration anchor mismatch")
text = text.replace(old, new, 1)
AUDIT.write_text(text, encoding="utf-8")

# The final branch must contain neither patch applicator.
Path(__file__).unlink()
