#!/usr/bin/env python3
"""Patch and run the one-time constitutional cleanup transformer.

The wrapper exists only on the bootstrap branch. It is fetched by GitHub Actions
and never committed to the qualified remediation branch.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSFORMER = ROOT / "tools" / ".one_time_constitutional_cleanup.py"


def _load_transformer():
    spec = importlib.util.spec_from_file_location(
        "one_time_constitutional_cleanup_runtime",
        TRANSFORMER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load cleanup transformer: {TRANSFORMER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _replace_top_level_function(
    path: Path,
    name: str,
    replacement: str,
) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one top-level {name} in {path}, got {len(matches)}"
        )
    node = matches[0]
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1 : node.end_lineno] = [replacement.rstrip() + "\n\n"]
    path.write_text("".join(lines), encoding="utf-8")


def _remove_test_methods_with_install_calls(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for parent in tree.body:
        candidates: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        if isinstance(parent, ast.ClassDef):
            candidates = [
                value
                for value in parent.body
                if isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
        elif isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            candidates = [parent]
        for node in candidates:
            if not node.name.startswith("test_"):
                continue
            if any(
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "install"
                for value in ast.walk(node)
            ):
                nodes.append(node)
    if not nodes:
        return []
    lines = source.splitlines(keepends=True)
    for node in sorted(nodes, key=lambda value: value.lineno, reverse=True):
        del lines[node.lineno - 1 : node.end_lineno]
    path.write_text("".join(lines), encoding="utf-8")
    return sorted(node.name for node in nodes)


def _rewrite_native_p0_runner(module) -> None:
    path = module.ROOT / "tools" / "run_v5_p0_regressions.py"
    source = path.read_text(encoding="utf-8")
    start = source.index("SPECS = (")
    end_marker = "\n\n\ndef _load_module"
    end = source.index(end_marker, start)
    suite = '''SPECS = (
    (TESTS / "test_v5_general_task_planning.py", "V5GeneralTaskPlanningTests", 6),
    (TESTS / "test_v5_planning_scenario_matrix.py", "V5PlanningScenarioMatrixTests", 4),
    (TESTS / "test_v5_general_task_full_planning.py", "V5GeneralTaskFullPlanningTests", 4),
    (TESTS / "test_v5_constitutional_runtime.py", "V5ConstitutionalRuntimeTests", 6),
    (TESTS / "test_v5_task_constraints.py", "TaskConstraintPolarityTests", 3),
    (TESTS / "test_v5_task_constraints.py", "ClosedWorldEvidenceTests", 5),
    (TESTS / "test_v5_task_constraints.py", "DynamicObjectiveTests", 1),
    (TESTS / "test_v5_task_constraints.py", "ActualCompanyAuditTests", 2),
    (TESTS / "test_v5_independent_artifact_revalidation.py", "IndependentArtifactRevalidationTests", 3),
)'''
    path.write_text(source[:start] + suite + source[end:], encoding="utf-8")


def _restore_native_quality_gate(module) -> None:
    path = module.MARKET / "v5_execution_primitives.py"
    _replace_top_level_function(
        path,
        "quality_gate",
        '''def quality_gate(
    node: SelectedNode,
    response: Mapping[str, Any],
    answer: str,
) -> tuple[bool, float, list[str]]:
    reasons: list[str] = []
    finish = finish_reason(response).casefold()
    if finish in {"length", "max_tokens"}:
        reasons.append("truncated-output")
    minimum_chars = (
        320
        if "synthesis" in node.functions
        else 180
        if "implementation" in node.functions
        else 120
    )
    if len(answer) < minimum_chars:
        reasons.append(f"answer-too-short<{minimum_chars}")
    folded = answer.casefold()
    if any(
        term in folded
        for term in (
            "i cannot access",
            "无法访问互联网",
            "作为ai无法",
            "没有提供任何答案",
        )
    ):
        reasons.append("non-delivery-or-tool-dependency")
    required_fields = [
        str(value)
        for value in node.output_contract.get("required_fields", [])
    ]
    field_hits = sum(
        field.replace("_", " ").casefold() in folded
        or field.casefold() in folded
        for field in required_fields
    )
    if node.output_contract.get("machine_readable_required"):
        try:
            parsed = json.loads(answer)
            if not isinstance(parsed, Mapping):
                reasons.append("machine-readable-output-not-object")
        except json.JSONDecodeError:
            reasons.append("invalid-required-json")
    completeness = min(1.0, len(answer) / max(minimum_chars * 3, 1))
    contract_score = field_hits / max(1, len(required_fields))
    finish_score = 0.0 if finish in {"length", "max_tokens"} else 1.0
    score = max(
        0.0,
        min(
            1.0,
            0.48 * completeness
            + 0.27 * contract_score
            + 0.25 * finish_score,
        ),
    )
    threshold = max(
        0.48,
        min(
            0.82,
            0.50
            + 0.22 * node.estimated_quality
            - 0.10 * node.quality_uncertainty,
        ),
    )
    if score + 1e-12 < threshold:
        reasons.append(f"quality-score<{threshold:.3f}")
    return not reasons, round(score, 6), reasons''',
    )


def _inline_full_planning_fixture(module) -> None:
    path = module.TESTS / "test_v5_general_task_full_planning.py"
    source = path.read_text(encoding="utf-8")
    source = source.replace(
        "from tests.test_v5_planner_executor import TestV5PlannerExecutor  # noqa: E402\n",
        "",
    )
    fixture = '''

class _PlanningFixture:
    @staticmethod
    def models():
        def model(
            model_id,
            description,
            rank,
            prompt,
            completion,
            supported=("reasoning", "structured_outputs"),
        ):
            return SimpleNamespace(
                id=model_id,
                name=model_id,
                description=description,
                author=model_id.split("/", 1)[0],
                context_length=131072,
                max_completion_tokens=16000,
                prompt_price_per_million=prompt,
                completion_price_per_million=completion,
                supported_parameters=list(supported),
                input_modalities=["text"],
                output_modalities=["text"],
                reasoning={"enabled": "reasoning" in supported},
                ranks={"intelligence-high-to-low": rank},
                components={},
            )

        return [
            model(
                "alpha/prime",
                "advanced reasoning mathematics research evidence business coding",
                1,
                8.0,
                24.0,
            ),
            model(
                "beta/value",
                "business finance economics investment strategy analysis research",
                3,
                2.0,
                6.0,
            ),
            model(
                "kappa/risk",
                "legal compliance security safety audit risk adversarial review",
                4,
                3.5,
                10.0,
            ),
            model(
                "theta/code",
                "software coding implementation engineering security repository",
                5,
                4.0,
                12.0,
            ),
            model(
                "delta/research",
                "long context research evidence policy documents reasoning",
                6,
                3.0,
                9.0,
            ),
            model(
                "gamma/general",
                "general analysis reasoning decision writing assistant",
                8,
                0.5,
                1.5,
                ("reasoning",),
            ),
        ]

    @classmethod
    def endpoints(cls):
        payloads = {}
        for index, model in enumerate(cls.models()):
            payloads[model.id] = {
                "data": {
                    "endpoints": [
                        {
                            "tag": f"provider-{index}",
                            "context_length": model.context_length,
                            "max_completion_tokens": model.max_completion_tokens,
                            "pricing": {
                                "prompt": model.prompt_price_per_million,
                                "completion": model.completion_price_per_million,
                            },
                            "supported_parameters": model.supported_parameters,
                            "uptime": 0.99 - index * 0.005,
                        }
                    ]
                }
            }
        return payloads
'''
    marker = "\n\nclass V5GeneralTaskFullPlanningTests"
    if marker not in source:
        raise RuntimeError("full planning fixture insertion marker missing")
    source = source.replace(marker, fixture + marker, 1)
    old_setup = '''    @classmethod
    def setUpClass(cls):
        cls.fixture = TestV5PlannerExecutor()
'''
    if old_setup not in source:
        raise RuntimeError("full planning fixture setup marker missing")
    source = source.replace(
        old_setup,
        '''    @classmethod
    def setUpClass(cls):
        cls.fixture = _PlanningFixture()
''',
        1,
    )
    path.write_text(source, encoding="utf-8")


def _normalize_text_file_endings(root: Path) -> None:
    for pattern in ("*.py", "*.yml", "*.yaml", "*.json", "*.md"):
        for path in root.rglob(pattern):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _prepare_actions_commit_boundary(root: Path) -> None:
    """Keep control-plane YAML and temporary qualification output out of the push.

    GitHub's installation token can write repository contents but cannot update
    workflow files. The exact validated workflow delta is therefore frozen into
    the diagnostic Artifact for a separate, digest-bound control-plane update.
    """
    artifact_root = root / "qualification-artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    patch_path = artifact_root / "validated-workflows.patch"
    patch = subprocess.run(
        ["git", "diff", "--binary", "--", ".github/workflows"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    patch_path.write_bytes(patch)
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--", ".github/workflows"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    manifest = {
        "schema_version": "v5-validated-workflow-patch-1",
        "changed_paths": changed,
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "patch_size": len(patch),
        "validation_boundary": (
            "same-worktree-before-static-unit-p0-and-scenario-qualification"
        ),
    }
    (artifact_root / "validated-workflows-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for relative in changed:
        subprocess.run(
            ["git", "update-index", "--skip-worktree", "--", relative],
            cwd=root,
            check=True,
        )
    exclude = root / ".git" / "info" / "exclude"
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    additions = "\n/qualification-logs/\n/qualification-artifacts/\n"
    if "/qualification-logs/" not in existing:
        exclude.write_text(existing.rstrip() + additions, encoding="utf-8")


def main() -> int:
    module = _load_transformer()
    module.EXPLICIT_LEGACY_MODULES.add("v5_rejection_audit_policy")

    original_replace = module.replace_install_statements
    removed_methods: dict[str, list[str]] = {}

    def replace_install_statements(path: Path) -> int:
        if path.parent == module.TESTS:
            removed = _remove_test_methods_with_install_calls(path)
            if removed:
                removed_methods[str(path.relative_to(module.ROOT))] = removed
        return original_replace(path)

    module.replace_install_statements = replace_install_statements
    module.rewrite_p0_runner = lambda: _rewrite_native_p0_runner(module)

    result = module.main()
    _restore_native_quality_gate(module)
    _inline_full_planning_fixture(module)

    report_path = module.MARKET / "legacy-cleanup-report.json"
    if report_path.is_file() and removed_methods:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["removed_install_test_methods"] = removed_methods
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    _normalize_text_file_endings(module.ROOT)
    _prepare_actions_commit_boundary(module.ROOT)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
