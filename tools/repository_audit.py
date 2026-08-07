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


def _python_findings(path: Path, rel: str, metrics: dict[str, int]) -> list[Finding]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [Finding("critical", "PY-SYNTAX", rel, exc.lineno or 1, str(exc))]
    findings: list[Finding] = []
    metrics["python_files"] += 1
    metrics["python_lines"] += len(text.splitlines())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            findings.extend(_function_findings(rel, node, metrics))
        elif isinstance(node, ast.ExceptHandler):
            findings.extend(_exception_findings(rel, node))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif node.module:
                names = [node.module.split(".", 1)[0]]
            if rel not in APPROVED_NETWORK_MODULES and any(
                name in DISALLOWED_NETWORK_IMPORTS for name in names
            ):
                findings.append(
                    Finding(
                        "critical",
                        "ARCH-UNAPPROVED-NETWORK-EGRESS",
                        rel,
                        node.lineno,
                        "network-capable import exists outside an approved control-plane module",
                    )
                )
    return findings


def _yaml_findings(path: Path, rel: str) -> list[Finding]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    findings: list[Finding] = []
    for index, line in enumerate(lines, 1):
        stripped = line.strip()
        if "uses:" not in stripped:
            continue
        value = stripped.split("uses:", 1)[1].strip().strip("'\"")
        if value.startswith("./"):
            continue
        if not PINNED_ACTION.match(value):
            findings.append(
                Finding(
                    "high",
                    "GH-ACTION-NOT-PINNED",
                    rel,
                    index,
                    f"GitHub Action is not pinned to a 40-character SHA: {value}",
                )
            )
    return findings


def _requirements_findings(path: Path, rel: str) -> list[Finding]:
    findings: list[Finding] = []
    for index, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not REQ.match(stripped):
            findings.append(
                Finding(
                    "high",
                    "DEP-UNPINNED",
                    rel,
                    index,
                    f"dependency is not version constrained: {stripped}",
                )
            )
    return findings


def _text_findings(path: Path, rel: str) -> list[Finding]:
    if rel in HISTORICAL:
        return []
    findings: list[Finding] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for index, line in enumerate(lines, 1):
        if LEGACY_REPOSITORY in line:
            findings.append(
                Finding(
                    "high",
                    "ARCH-LEGACY-REPOSITORY",
                    rel,
                    index,
                    "legacy repository reference remains outside migration history",
                )
            )
        if SECRET_NAME.search(line) and "=" in line:
            _, value = line.split("=", 1)
            value = value.strip().strip("'\"")
            if value and not PLACEHOLDER.search(value) and "${{" not in value:
                findings.append(
                    Finding(
                        "critical",
                        "SEC-HARDCODED-SECRET-CANDIDATE",
                        rel,
                        index,
                        "possible hard-coded credential value",
                    )
                )
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
    if not market.is_dir():
        return result
    for path in sorted(market.glob("*.py")):
        if path.name.startswith("__"):
            continue
        module = path.stem
        if module not in imported and module not in entrypoints:
            result.append(path.relative_to(root).as_posix())
    return result


def audit(root: Path) -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    metrics: dict[str, int] = defaultdict(int)
    hashes: dict[str, list[str]] = defaultdict(list)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in IGNORED for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/"):
            continue
        metrics["files"] += 1
        data = path.read_bytes()
        hashes[hashlib.sha256(data).hexdigest()].append(rel)
        kind = file_kind(path)
        if kind == "python":
            findings.extend(_python_findings(path, rel, metrics))
        elif kind == "yaml":
            findings.extend(_yaml_findings(path, rel))
        elif kind == "requirements":
            findings.extend(_requirements_findings(path, rel))
        else:
            findings.extend(_text_findings(path, rel))

    duplicate_groups = [paths for paths in hashes.values() if len(paths) > 1]
    for paths in duplicate_groups:
        if all(path.startswith("tests/") for path in paths):
            severity = "info"
        else:
            severity = "medium"
        findings.append(
            Finding(
                severity,
                "REPO-EXACT-DUPLICATE-FILES",
                paths[0],
                1,
                "exact duplicate file contents: " + ", ".join(paths),
            )
        )

    for rel in _orphan_candidates(root):
        findings.append(
            Finding(
                "low",
                "ARCH-ORPHAN-CANDIDATE",
                rel,
                1,
                "module is not imported by Python code and is not a workflow entrypoint",
            )
        )

    findings.sort(
        key=lambda item: (
            SEVERITY.get(item.severity, 99),
            item.path,
            item.line,
            item.rule,
        )
    )
    counts = Counter(item.severity for item in findings)
    summary = {
        "schema_version": "repository-line-audit-v1",
        "status": "PASS" if not (counts["critical"] or counts["high"]) else "FAIL",
        "metrics": dict(metrics),
        "finding_counts": dict(sorted(counts.items())),
        "exact_duplicate_groups": duplicate_groups,
        "orphan_candidates": _orphan_candidates(root),
    }
    return findings, summary


def _write_markdown(path: Path, summary: dict[str, Any], findings: list[Finding]) -> None:
    rows = [
        "# Repository line audit",
        "",
        f"Status: **{summary['status']}**",
        "",
        "## Finding counts",
        "",
    ]
    for severity in ("critical", "high", "medium", "low", "info"):
        rows.append(f"- {severity}: {summary['finding_counts'].get(severity, 0)}")
    rows.extend(["", "## Findings", ""])
    if not findings:
        rows.append("No findings.")
    else:
        for finding in findings:
            rows.append(
                f"- **{finding.severity.upper()}** `{finding.rule}` "
                f"`{finding.path}:{finding.line}` — {finding.message}"
            )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--json-output", default="audit-artifacts/repository-audit.json")
    parser.add_argument("--markdown-output", default="audit-artifacts/repository-audit.md")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    findings, summary = audit(root)
    output_json = Path(args.json_output)
    output_markdown = Path(args.markdown_output)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(
            {
                **summary,
                "findings": [asdict(item) for item in findings],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_markdown(output_markdown, summary, findings)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
