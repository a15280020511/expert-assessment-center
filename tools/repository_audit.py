#!/usr/bin/env python3
"""Read-only full repository audit with deterministic JSON and Markdown evidence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

IGNORED = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "audit-artifacts",
    "artifacts",
    "validation-artifacts",
    "ticket-artifacts",
    "runtime-state",
    "performance-state",
    "tmp-test-artifacts",
}
SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}(?:\s*#.*)?$")
SECRET_NAME = re.compile(r"(?i)(api[_-]?key|secret|token|password|credential)")
PLACEHOLDER = re.compile(
    r"(?i)(example|placeholder|dummy|redacted|replace[-_ ]?me|not[-_ ]?set|your[-_ ])"
)
REQ = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*(?:==|~=|>=|<=|>|<|!=).*$")
PYTHON_WORKFLOW_ENTRYPOINT = re.compile(
    r"(?:python(?:3)?\s+)(?:\./)?open-model-market/([A-Za-z0-9_]+)\.py\b"
)
PYTHON_WORKFLOW_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+([A-Za-z0-9_]+)\b",
    re.MULTILINE,
)
LEGACY_REPOSITORY = "a15280020511/" + "test"
HISTORICAL = {
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
    "open-model-market/v5_paid_acceptance_free_first_guard.py",
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


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    path: str
    line: int
    message: str


def file_kind(path: Path) -> str:
    if path.suffix == ".py":
        return "python"
    if path.suffix == ".json":
        return "json"
    if path.suffix in {".yml", ".yaml"}:
        return "yaml"
    if "requirements" in path.name.lower() and path.suffix == ".txt":
        return "requirements"
    return "text"


def complexity(node: ast.AST) -> int:
    score = 1
    for child in ast.walk(node):
        if isinstance(
            child,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.Try,
                ast.With,
                ast.AsyncWith,
                ast.IfExp,
                ast.Match,
                ast.comprehension,
            ),
        ):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += max(1, len(child.values) - 1)
    return score


def target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for item in target.elts for name in target_names(item)]
    return []


def _function_findings(
    rel: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    metrics: dict[str, int],
) -> list[Finding]:
    metrics["functions"] += 1
    score = complexity(node)
    length = int(getattr(node, "end_lineno", node.lineno)) - int(node.lineno) + 1
    metrics["max_complexity"] = max(metrics["max_complexity"], score)
    metrics["max_function_lines"] = max(metrics["max_function_lines"], length)
    findings: list[Finding] = []
    if score > 20:
        findings.append(
            Finding(
                "medium",
                "PY-COMPLEXITY",
                rel,
                node.lineno,
                f"function {node.name!r} complexity={score}",
            )
        )
    if length > 180:
        findings.append(
            Finding(
                "medium",
                "PY-FUNCTION-SIZE",
                rel,
                node.lineno,
                f"function {node.name!r} spans {length} lines",
            )
        )
    return findings


def _exception_findings(rel: str, node: ast.ExceptHandler) -> list[Finding]:
    if node.type is None:
        return [
            Finding(
                "high",
                "PY-BARE-EXCEPT",
                rel,
                node.lineno,
                "bare except hides termination and programming errors",
            )
        ]
    if isinstance(node.type, ast.Name) and node.type.id == "BaseException":
        return [
            Finding(
                "high",
                "PY-BASE-EXCEPTION",
                rel,
                node.lineno,
                "BaseException handler catches process termination",
            )
        ]
    return []


def _duplicate_findings(
    functions: dict[str, list[tuple[str, int, str]]],
) -> list[Finding]:
    findings: list[Finding] = []
    for digest, rows in functions.items():
        if len(rows) < 2:
            continue
        paths = {row[0] for row in rows}
        if len(paths) < 2:
            continue
        display = [f"{path}:{line}:{name}" for path, line, name in rows]
        findings.append(
            Finding(
                "medium",
                "PY-DUPLICATE-FUNCTION-BODY",
                rows[0][0],
                rows[0][1],
                f"identical function bodies found at {display}; sha256={digest}",
            )
        )
    return findings


def _python_findings(path: Path, rel: str, text: str) -> tuple[list[Finding], dict[str, int], list[tuple[str, int, str, str]]]:
    findings: list[Finding] = []
    metrics = {"functions": 0, "classes": 0, "max_complexity": 0, "max_function_lines": 0}
    functions: list[tuple[str, int, str, str]] = []
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        findings.append(
            Finding("critical", "PY-SYNTAX", rel, int(exc.lineno or 1), str(exc))
        )
        return findings, metrics, functions
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            findings.extend(_function_findings(rel, node, metrics))
            try:
                body = ast.Module(body=node.body, type_ignores=[])
                normalized = ast.dump(body, annotate_fields=True, include_attributes=False)
                digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                functions.append((rel, node.lineno, node.name, digest))
            except Exception:
                pass
        elif isinstance(node, ast.ClassDef):
            metrics["classes"] += 1
        elif isinstance(node, ast.ExceptHandler):
            findings.extend(_exception_findings(rel, node))
    if rel not in APPROVED_NETWORK_MODULES:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".", 1)[0] for alias in node.names}
                if names & DISALLOWED_NETWORK_IMPORTS:
                    findings.append(Finding("critical", "ARCH-UNAPPROVED-NETWORK-EGRESS", rel, node.lineno, "direct network egress exists outside the approved model/control-plane modules"))
                if any(alias.name == "urllib.request" for alias in node.names):
                    findings.append(Finding("critical", "ARCH-UNAPPROVED-NETWORK-EGRESS", rel, node.lineno, "direct network egress exists outside the approved model/control-plane modules"))
            elif isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                root = module.split(".", 1)[0]
                if root in DISALLOWED_NETWORK_IMPORTS or module == "urllib.request":
                    findings.append(Finding("critical", "ARCH-UNAPPROVED-NETWORK-EGRESS", rel, node.lineno, "direct network egress exists outside the approved model/control-plane modules"))
    return findings, metrics, functions


def _text_findings(rel: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    if LEGACY_REPOSITORY in text and rel not in HISTORICAL:
        findings.append(Finding("high", "ARCH-LEGACY-REPOSITORY-REFERENCE", rel, 1, "active file references the retired legacy repository"))
    return findings


def _yaml_findings(rel: str, text: str) -> list[Finding]:
    findings = _text_findings(rel, text)
    for index, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("uses:"):
            value = stripped.split(":", 1)[1].strip()
            if not PINNED_ACTION.fullmatch(value):
                findings.append(Finding("critical", "WF-UNPINNED-ACTION", rel, index, f"workflow action is not pinned to an immutable SHA: {value}"))
    return findings


def _json_findings(rel: str, text: str) -> list[Finding]:
    findings = _text_findings(rel, text)
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        findings.append(Finding("critical", "JSON-SYNTAX", rel, exc.lineno, str(exc)))
    return findings


def _requirements_findings(rel: str, text: str) -> list[Finding]:
    findings = _text_findings(rel, text)
    for index, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not REQ.fullmatch(stripped):
            findings.append(Finding("high", "REQ-UNPINNED", rel, index, f"dependency is not version constrained: {stripped}"))
    return findings


def _workflow_entrypoints(root: Path) -> set[str]:
    modules: set[str] = set()
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        return modules
    for path in sorted(workflows.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        modules.update(PYTHON_WORKFLOW_ENTRYPOINT.findall(text))
        modules.update(PYTHON_WORKFLOW_IMPORT.findall(text))
    return modules


def _module_imports(root: Path) -> set[str]:
    modules: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if any(part in IGNORED for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        modules.update(PYTHON_WORKFLOW_IMPORT.findall(text))
    return modules


def _orphan_candidates(root: Path) -> list[str]:
    imported = _module_imports(root)
    entrypoints = _workflow_entrypoints(root)
    result: list[str] = []
    market = root / "open-model-market"
    for path in sorted(market.glob("*.py")):
        module = path.stem
        if module.startswith("__"):
            continue
        if module not in imported and module not in entrypoints:
            result.append(path.relative_to(root).as_posix())
    return result


def run_audit(root: Path) -> dict[str, Any]:
    findings: list[Finding] = []
    file_metrics: dict[str, Any] = {}
    duplicate_index: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    files = 0
    total_lines = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in IGNORED for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        files += 1
        total_lines += len(text.splitlines())
        kind = file_kind(path)
        if kind == "python":
            local, metrics, functions = _python_findings(path, rel, text)
            findings.extend(local)
            file_metrics[rel] = metrics
            for f_rel, line, name, digest in functions:
                duplicate_index[digest].append((f_rel, line, name))
        elif kind == "yaml":
            findings.extend(_yaml_findings(rel, text))
        elif kind == "json":
            findings.extend(_json_findings(rel, text))
        elif kind == "requirements":
            findings.extend(_requirements_findings(rel, text))
        else:
            findings.extend(_text_findings(rel, text))
    findings.extend(_duplicate_findings(duplicate_index))
    orphans = _orphan_candidates(root)
    for rel in orphans:
        findings.append(Finding("info", "PY-ORPHAN-CANDIDATE", rel, 1, "module is not imported or referenced by a workflow; verify other CLI use before removal"))
    ordered = sorted(findings, key=lambda item: (SEVERITY[item.severity], item.path, item.line, item.rule, item.message))
    counts = Counter(item.severity for item in ordered)
    return {
        "schema_version": "repository-audit-v4",
        "file_count": files,
        "total_lines": total_lines,
        "finding_counts": {level: counts.get(level, 0) for level in SEVERITY},
        "findings": [asdict(item) for item in ordered],
        "python_metrics": file_metrics,
        "workflow_entrypoints": sorted(_workflow_entrypoints(root)),
        "orphan_candidates": orphans,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Full Repository Audit",
        "",
        f"- Files: `{report['file_count']}`",
        f"- Lines inspected: `{report['total_lines']}`",
        f"- Critical: `{report['finding_counts']['critical']}`",
        f"- High: `{report['finding_counts']['high']}`",
        f"- Medium: `{report['finding_counts']['medium']}`",
        f"- Low: `{report['finding_counts']['low']}`",
        f"- Info: `{report['finding_counts']['info']}`",
        "",
        "| Severity | Rule | File | Line | Finding |",
        "|---|---|---|---:|---|",
    ]
    for finding in report["findings"]:
        message = str(finding["message"]).replace("|", "\\|")
        lines.append(
            f"| {finding['severity']} | `{finding['rule']}` | `{finding['path']}` | "
            f"{finding['line']} | {message} |"
        )
    if not report["findings"]:
        lines.append("| info | `NONE` | `-` | 0 | no findings |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--markdown-output", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    report = run_audit(root)
    json_path = Path(args.json_output)
    markdown_path = Path(args.markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report) + "\n", encoding="utf-8")
    critical = report["finding_counts"]["critical"]
    high = report["finding_counts"]["high"]
    print(json.dumps({"critical": critical, "high": high, "medium": report["finding_counts"]["medium"], "files": report["file_count"], "lines": report["total_lines"]}))
    return 1 if critical or high else 0


if __name__ == "__main__":
    raise SystemExit(main())
