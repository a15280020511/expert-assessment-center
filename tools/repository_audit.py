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


def _call_findings(rel: str, node: ast.Call) -> list[Finding]:
    findings: list[Finding] = []
    func = node.func
    if isinstance(func, ast.Name) and func.id in {"eval", "exec"}:
        findings.append(
            Finding("critical", "PY-DYNAMIC-CODE", rel, node.lineno, f"use of {func.id}()")
        )
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return findings
    owner, name = func.value.id, func.attr
    if owner == "os" and name == "system":
        findings.append(
            Finding("critical", "PY-OS-SYSTEM", rel, node.lineno, "os.system executes a shell")
        )
    shell_true = owner == "subprocess" and any(
        keyword.arg == "shell"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in node.keywords
    )
    if shell_true:
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
    return findings


def _assignment_findings(
    rel: str,
    node: ast.Assign | ast.AnnAssign,
) -> list[Finding]:
    if "tests" in Path(rel).parts or rel == "tools/repository_audit.py":
        return []
    targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
    value = node.value
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return []
    names = [name for target in targets for name in target_names(target)]
    if not (
        names
        and any(SECRET_NAME.search(name) for name in names)
        and len(value.value) >= 12
        and not PLACEHOLDER.search(value.value)
    ):
        return []
    return [
        Finding(
            "critical",
            "PY-HARDCODED-CREDENTIAL",
            rel,
            int(getattr(node, "lineno", 1)),
            f"sensitive variable {names[0]!r} contains a literal value",
        )
    ]


def _register_import(node: ast.AST, imports: set[str]) -> None:
    if isinstance(node, ast.Import):
        imports.update(alias.name.split(".")[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        imports.add(node.module.split(".")[0])


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
        _register_import(node, imports)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            findings.extend(_function_findings(rel, node, metrics))
        elif isinstance(node, ast.ClassDef):
            metrics["classes"] += 1
        elif isinstance(node, ast.ExceptHandler):
            findings.extend(_exception_findings(rel, node))
        elif isinstance(node, ast.Call):
            findings.extend(_call_findings(rel, node))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            findings.extend(_assignment_findings(rel, node))
    return findings, imports, metrics


@dataclass
class AuditState:
    findings: list[Finding]
    files: list[dict[str, Any]]
    hashes: dict[str, list[str]]
    imports_by_file: dict[str, set[str]]
    function_hashes: dict[str, list[tuple[str, str, int]]]
    metrics: dict[str, dict[str, int]]
    requirements: dict[str, str]
    workflow_entrypoints: set[str]

    @classmethod
    def empty(cls) -> "AuditState":
        return cls(
            findings=[],
            files=[],
            hashes=defaultdict(list),
            imports_by_file={},
            function_hashes=defaultdict(list),
            metrics={},
            requirements={},
            workflow_entrypoints=set(),
        )


def _repository_files(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not any(part in IGNORED for part in path.relative_to(root).parts)
    ]


def _decode_utf8(data: bytes) -> str | None:
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _line_findings(rel: str, kind: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for index, line in enumerate(text.splitlines(), 1):
        if line.rstrip(" \t") != line:
            findings.append(
                Finding("low", "TXT-TRAILING-WHITESPACE", rel, index, "trailing whitespace")
            )
        if "\t" in line and kind in {"python", "yaml", "json"}:
            findings.append(
                Finding("low", "TXT-TAB", rel, index, "tab character in structured source")
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
        findings.extend(_yaml_line_findings(rel, kind, index, line))
    return findings


def _yaml_line_findings(rel: str, kind: str, index: int, line: str) -> list[Finding]:
    if kind != "yaml":
        return []
    if re.match(r"\s*repository_dispatch\s*:", line):
        return [
            Finding(
                "critical",
                "ARCH-CROSS-REPO-DISPATCH",
                rel,
                index,
                "repository_dispatch violates center isolation",
            )
        ]
    if re.match(r"\s*pull_request_target\s*:", line):
        return [
            Finding(
                "high",
                "GHA-PR-TARGET",
                rel,
                index,
                "pull_request_target expands workflow trust",
            )
        ]
    if re.search(r"\bpermissions:\s*write-all\b", line):
        return [
            Finding(
                "critical",
                "GHA-WRITE-ALL",
                rel,
                index,
                "workflow grants write-all",
            )
        ]
    if not re.match(r"\s*uses:\s*", line):
        return []
    action = line.split("uses:", 1)[1].strip()
    if action.startswith(("./", "docker://")) or PINNED_ACTION.match(action):
        return []
    return [
        Finding(
            "high",
            "GHA-UNPINNED-ACTION",
            rel,
            index,
            f"action is not pinned to a full commit SHA: {action}",
        )
    ]




def _record_function_hashes(
    state: AuditState,
    rel: str,
    text: str,
) -> None:
    if "tests" in Path(rel).parts:
        return
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = list(node.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        rendered = ast.dump(
            ast.Module(body=body, type_ignores=[]),
            include_attributes=False,
        )
        if len(rendered) < 300:
            continue
        digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        state.function_hashes[digest].append((rel, node.name, node.lineno))


def _duplicate_function_findings(
    function_hashes: dict[str, list[tuple[str, str, int]]],
) -> list[Finding]:
    findings: list[Finding] = []
    for digest, rows in function_hashes.items():
        paths = {path for path, _, _ in rows}
        if len(paths) <= 1:
            continue
        locations = [f"{path}:{line}:{name}" for path, name, line in rows]
        first_path, _, first_line = rows[0]
        findings.append(
            Finding(
                "medium",
                "PY-DUPLICATE-FUNCTION-BODY",
                first_path,
                first_line,
                f"identical function bodies found at {locations}; sha256={digest}",
            )
        )
    return findings


def _network_boundary_findings(
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
        line = text[: text.index("urllib.request.urlopen")].count("\n") + 1
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
        and re.search(r"^FORBIDDEN_(?:REQUEST_)?FIELDS\s*=", text, re.MULTILINE)
    ):
        line = text[: re.search(
            r"^FORBIDDEN_(?:REQUEST_)?FIELDS\s*=", text, re.MULTILINE
        ).start()].count("\n") + 1
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
        line = text[: text.index("MAX_TASK_CHARS")].count("\n") + 1
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


def _inspect_structured_file(
    state: AuditState,
    rel: str,
    kind: str,
    text: str,
) -> None:
    if kind == "python":
        py_findings, imports, py_metrics = audit_python(rel, text)
        state.findings.extend(py_findings)
        state.findings.extend(_network_boundary_findings(rel, kind, text, imports))
        state.imports_by_file[rel] = imports
        state.metrics[rel] = py_metrics
        _record_function_hashes(state, rel, text)
    elif kind == "json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            state.findings.append(
                Finding("critical", "JSON-PARSE", rel, exc.lineno, exc.msg)
            )
    elif kind == "requirements":
        state.requirements[rel] = text


def _inspect_file(root: Path, path: Path, state: AuditState) -> None:
    rel = path.relative_to(root).as_posix()
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    state.hashes[digest].append(rel)
    text = _decode_utf8(data)
    kind = file_kind(path)
    state.files.append({
        "path": rel,
        "size_bytes": len(data),
        "sha256": digest,
        "line_count": None if text is None else len(text.splitlines()),
        "kind": kind,
    })
    if len(data) > 2_000_000:
        state.findings.append(
            Finding(
                "medium",
                "FILE-LARGE",
                rel,
                1,
                f"repository file size={len(data)} bytes",
            )
        )
    if text is None:
        return
    if kind == "yaml":
        state.workflow_entrypoints.update(PYTHON_WORKFLOW_ENTRYPOINT.findall(text))
        state.workflow_entrypoints.update(PYTHON_WORKFLOW_IMPORT.findall(text))
    state.findings.extend(_line_findings(rel, kind, text))
    _inspect_structured_file(state, rel, kind, text)


def _duplicate_findings(hashes: dict[str, list[str]]) -> list[Finding]:
    findings: list[Finding] = []
    for digest, paths in hashes.items():
        useful = [path for path in paths if not path.endswith(("__init__.py", ".gitkeep"))]
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
    return findings


def _requirement_findings(requirements: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
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
    return findings


def _orphan_findings(
    metrics: dict[str, dict[str, int]],
    imports_by_file: dict[str, set[str]],
    workflow_entrypoints: set[str],
) -> tuple[list[str], list[Finding]]:
    production_imports = [
        imports
        for rel, imports in imports_by_file.items()
        if "tests" not in Path(rel).parts
    ]
    imported = set().union(*production_imports) if production_imports else set()
    reachable_modules = imported | workflow_entrypoints
    candidates: list[str] = []
    findings: list[Finding] = []
    for rel in sorted(metrics):
        path = Path(rel)
        if (
            "tests" in path.parts
            or path.name in {"__init__.py", "repository_audit.py"}
            or path.parts[0] == "tools"
        ):
            continue
        if path.stem in reachable_modules or path.name.endswith(("_task.py", "_runner.py")):
            continue
        candidates.append(rel)
        findings.append(
            Finding(
                "info",
                "PY-ORPHAN-CANDIDATE",
                rel,
                1,
                "module is not imported or referenced by a workflow; verify other CLI use before removal",
            )
        )
    return candidates, findings


def audit(root: Path) -> dict[str, Any]:
    state = AuditState.empty()
    for path in _repository_files(root):
        _inspect_file(root, path, state)
    state.findings.extend(_duplicate_findings(state.hashes))
    state.findings.extend(_duplicate_function_findings(state.function_hashes))
    state.findings.extend(_requirement_findings(state.requirements))
    orphan_candidates, orphan_findings = _orphan_findings(
        state.metrics,
        state.imports_by_file,
        state.workflow_entrypoints,
    )
    state.findings.extend(orphan_findings)
    state.findings.sort(
        key=lambda item: (SEVERITY[item.severity], item.path, item.line, item.rule)
    )
    counts = Counter(item.severity for item in state.findings)
    return {
        "schema_version": "repository-audit-v4",
        "file_count": len(state.files),
        "total_lines": sum(item["line_count"] or 0 for item in state.files),
        "finding_counts": {name: counts.get(name, 0) for name in SEVERITY},
        "findings": [asdict(item) for item in state.findings],
        "files": state.files,
        "python_metrics": state.metrics,
        "workflow_entrypoints": sorted(state.workflow_entrypoints),
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
        "--fail-on", choices=("none", *SEVERITY), default="high"
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
