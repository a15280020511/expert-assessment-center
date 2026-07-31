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
LEGACY_REPOSITORY = "a15280020511/" + "test"
HISTORICAL = {
    "MIGRATION.md",
    "MIGRATION_MANIFEST.json",
    "MIGRATION_PROVENANCE.json",
    "RECOVERY.md",
    "governance-compatibility.json",
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


def audit_python(rel: str, text: str) -> tuple[list[Finding], set[str], dict[str, int]]:
    findings: list[Finding] = []
    imports: set[str] = set()
    metrics = {
        "functions": 0,
        "classes": 0,
        "max_complexity": 0,
        "max_function_lines": 0,
    }
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        return (
            [Finding("critical", "PY-SYNTAX", rel, int(exc.lineno or 1), str(exc))],
            imports,
            metrics,
        )
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            metrics["functions"] += 1
            score = complexity(node)
            length = (
                int(getattr(node, "end_lineno", node.lineno)) - int(node.lineno) + 1
            )
            metrics["max_complexity"] = max(metrics["max_complexity"], score)
            metrics["max_function_lines"] = max(metrics["max_function_lines"], length)
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
        elif isinstance(node, ast.ClassDef):
            metrics["classes"] += 1
        elif isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                findings.append(
                    Finding(
                        "high",
                        "PY-BARE-EXCEPT",
                        rel,
                        node.lineno,
                        "bare except hides termination and programming errors",
                    )
                )
            elif isinstance(node.type, ast.Name) and node.type.id == "BaseException":
                findings.append(
                    Finding(
                        "high",
                        "PY-BASE-EXCEPTION",
                        rel,
                        node.lineno,
                        "BaseException handler catches process termination",
                    )
                )
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"eval", "exec"}:
                findings.append(
                    Finding(
                        "critical",
                        "PY-DYNAMIC-CODE",
                        rel,
                        node.lineno,
                        f"use of {func.id}()",
                    )
                )
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                owner, name = func.value.id, func.attr
                if owner == "os" and name == "system":
                    findings.append(
                        Finding(
                            "critical",
                            "PY-OS-SYSTEM",
                            rel,
                            node.lineno,
                            "os.system executes a shell",
                        )
                    )
                if owner == "subprocess" and any(
                    k.arg == "shell"
                    and isinstance(k.value, ast.Constant)
                    and k.value.value is True
                    for k in node.keywords
                ):
                    findings.append(
                        Finding(
                            "critical",
                            "PY-SHELL-TRUE",
                            rel,
                            node.lineno,
                            "subprocess call uses shell=True",
                        )
                    )
                if owner in {"pickle", "dill"} and name in {"load", "loads"}:
                    findings.append(
                        Finding(
                            "high",
                            "PY-UNSAFE-DESERIALIZE",
                            rel,
                            node.lineno,
                            f"{owner}.{name} can execute untrusted input",
                        )
                    )
        elif (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and "tests" not in Path(rel).parts
            and rel != "tools/repository_audit.py"
        ):
            targets = (
                list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            )
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                names = [name for target in targets for name in target_names(target)]
                if (
                    names
                    and any(SECRET_NAME.search(name) for name in names)
                    and len(value.value) >= 12
                    and not PLACEHOLDER.search(value.value)
                ):
                    findings.append(
                        Finding(
                            "critical",
                            "PY-HARDCODED-CREDENTIAL",
                            rel,
                            int(getattr(node, "lineno", 1)),
                            f"sensitive variable {names[0]!r} contains a literal value",
                        )
                    )
    return findings, imports, metrics


def audit(root: Path) -> dict[str, Any]:
    findings: list[Finding] = []
    files: list[dict[str, Any]] = []
    hashes: dict[str, list[str]] = defaultdict(list)
    imports_by_file: dict[str, set[str]] = {}
    metrics: dict[str, dict[str, int]] = {}
    requirements: dict[str, str] = {}
    workflow_entrypoints: set[str] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root)
        if any(part in IGNORED for part in rel_path.parts):
            continue
        rel = rel_path.as_posix()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        hashes[digest].append(rel)
        try:
            text = data.decode("utf-8") if b"\x00" not in data else None
        except UnicodeDecodeError:
            text = None
        kind = file_kind(path)
        files.append(
            {
                "path": rel,
                "size_bytes": len(data),
                "sha256": digest,
                "line_count": None if text is None else len(text.splitlines()),
                "kind": kind,
            }
        )
        if len(data) > 2_000_000:
            findings.append(
                Finding(
                    "medium",
                    "FILE-LARGE",
                    rel,
                    1,
                    f"repository file size={len(data)} bytes",
                )
            )
        if text is None:
            continue
        if kind == "yaml":
            workflow_entrypoints.update(PYTHON_WORKFLOW_ENTRYPOINT.findall(text))
        for index, line in enumerate(text.splitlines(), 1):
            if line.rstrip(" \t") != line:
                findings.append(
                    Finding(
                        "low",
                        "TXT-TRAILING-WHITESPACE",
                        rel,
                        index,
                        "trailing whitespace",
                    )
                )
            if "\t" in line and kind in {"python", "yaml", "json"}:
                findings.append(
                    Finding(
                        "low",
                        "TXT-TAB",
                        rel,
                        index,
                        "tab character in structured source",
                    )
                )
            if rel != "tools/repository_audit.py" and re.search(
                r"\b(TODO|FIXME|HACK|XXX)\b", line, re.I
            ):
                findings.append(
                    Finding("medium", "TXT-DEBT-MARKER", rel, index, line.strip()[:220])
                )
            if (
                rel != "tools/repository_audit.py"
                and LEGACY_REPOSITORY in line
                and Path(rel).name not in HISTORICAL
            ):
                findings.append(
                    Finding(
                        "medium",
                        "ARCH-LEGACY-REPOSITORY",
                        rel,
                        index,
                        "legacy repository reference remains outside migration provenance",
                    )
                )
            if kind == "yaml" and re.match(r"\s*repository_dispatch\s*:", line):
                findings.append(
                    Finding(
                        "critical",
                        "ARCH-CROSS-REPO-DISPATCH",
                        rel,
                        index,
                        "repository_dispatch violates center isolation",
                    )
                )
            if kind == "yaml" and re.match(r"\s*pull_request_target\s*:", line):
                findings.append(
                    Finding(
                        "high",
                        "GHA-PR-TARGET",
                        rel,
                        index,
                        "pull_request_target expands workflow trust",
                    )
                )
            if kind == "yaml" and re.search(r"\bpermissions:\s*write-all\b", line):
                findings.append(
                    Finding(
                        "critical",
                        "GHA-WRITE-ALL",
                        rel,
                        index,
                        "workflow grants write-all",
                    )
                )
            if kind == "yaml" and re.match(r"\s*uses:\s*", line):
                action = line.split("uses:", 1)[1].strip()
                if not action.startswith(
                    ("./", "docker://")
                ) and not PINNED_ACTION.match(action):
                    findings.append(
                        Finding(
                            "high",
                            "GHA-UNPINNED-ACTION",
                            rel,
                            index,
                            f"action is not pinned to a full commit SHA: {action}",
                        )
                    )
        if kind == "python":
            py_findings, imports, py_metrics = audit_python(rel, text)
            findings.extend(py_findings)
            imports_by_file[rel] = imports
            metrics[rel] = py_metrics
        elif kind == "json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                findings.append(
                    Finding("critical", "JSON-PARSE", rel, exc.lineno, exc.msg)
                )
        elif kind == "requirements":
            requirements[rel] = text
    for digest, paths in hashes.items():
        useful = [p for p in paths if not p.endswith(("__init__.py", ".gitkeep"))]
        if len(useful) > 1:
            findings.append(
                Finding(
                    "low",
                    "FILE-DUPLICATE",
                    useful[0],
                    1,
                    f"identical content shared by {useful}; sha256={digest}",
                )
            )
    package_specs: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for rel, text in requirements.items():
        for index, raw in enumerate(text.splitlines(), 1):
            value = raw.strip()
            if not value or value.startswith("#") or value.startswith("-r "):
                continue
            match = REQ.match(value)
            if not match:
                findings.append(
                    Finding(
                        "medium",
                        "REQ-UNPINNED",
                        rel,
                        index,
                        f"dependency is not constrained: {value}",
                    )
                )
                continue
            package_specs[match.group(1).lower().replace("_", "-")].append(
                (rel, index, value)
            )
    for package, specs in package_specs.items():
        values = {spec for _, _, spec in specs}
        if len(values) > 1:
            findings.append(
                Finding(
                    "medium",
                    "REQ-CONFLICTING-SPECS",
                    specs[0][0],
                    specs[0][1],
                    f"{package} has multiple constraints: {sorted(values)}",
                )
            )
    imported = set().union(*imports_by_file.values()) if imports_by_file else set()
    reachable_modules = imported | workflow_entrypoints
    orphan_candidates = []
    for rel in sorted(metrics):
        path = Path(rel)
        if (
            "tests" in path.parts
            or path.name in {"__init__.py", "repository_audit.py"}
            or path.parts[0] == "tools"
        ):
            continue
        if path.stem not in reachable_modules and not path.name.endswith(
            ("_task.py", "_runner.py")
        ):
            orphan_candidates.append(rel)
            findings.append(
                Finding(
                    "info",
                    "PY-ORPHAN-CANDIDATE",
                    rel,
                    1,
                    "module is not imported or referenced by a workflow; verify other CLI use before removal",
                )
            )
    findings.sort(
        key=lambda item: (SEVERITY[item.severity], item.path, item.line, item.rule)
    )
    counts = Counter(item.severity for item in findings)
    return {
        "schema_version": "repository-audit-v4",
        "file_count": len(files),
        "total_lines": sum(item["line_count"] or 0 for item in files),
        "finding_counts": {name: counts.get(name, 0) for name in SEVERITY},
        "findings": [asdict(item) for item in findings],
        "files": files,
        "python_metrics": metrics,
        "workflow_entrypoints": sorted(workflow_entrypoints),
        "orphan_candidates": orphan_candidates,
    }


def render(report: dict[str, Any]) -> str:
    rows = [
        "# Full Repository Audit",
        "",
        f"- Files: `{report['file_count']}`",
        f"- Lines inspected: `{report['total_lines']}`",
        *(f"- {name.title()}: `{report['finding_counts'][name]}`" for name in SEVERITY),
        "",
        "| Severity | Rule | File | Line | Finding |",
        "|---|---|---|---:|---|",
    ]
    for item in report["findings"]:
        message = str(item["message"]).replace("|", "\\|").replace("\n", " ")
        rows.append(
            f"| {item['severity']} | `{item['rule']}` | `{item['path']}` | {item['line']} | {message} |"
        )
    return "\n".join(rows) + "\n"


def should_fail(report: dict[str, Any], fail_on: str) -> bool:
    if fail_on == "none":
        return False
    threshold = SEVERITY[fail_on]
    return any(
        SEVERITY[item["severity"]] <= threshold for item in report.get("findings", [])
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="audit-artifacts")
    parser.add_argument(
        "--fail-on", choices=("none", "critical", "high"), default="high"
    )
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report = audit(Path(args.root).resolve())
    (output / "repository-audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "repository-audit.md").write_text(render(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "finding_counts": report["finding_counts"],
                "file_count": report["file_count"],
                "total_lines": report["total_lines"],
            },
            ensure_ascii=False,
        )
    )
    return 1 if should_fail(report, args.fail_on) else 0


if __name__ == "__main__":
    raise SystemExit(main())
