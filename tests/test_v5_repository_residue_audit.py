from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"


class RepositoryResidueAuditTests(unittest.TestCase):
    def test_obsolete_local_planning_files_are_absent(self) -> None:
        forbidden = (
            MARKET / "v5_general_task_planning.py",
            MARKET / "task_semantic_compiler.py",
            MARKET / "resource_matrix.py",
            MARKET / "atomic_work_graph.py",
            MARKET / "task_resource_artifacts.py",
            MARKET / "v5_single_pass_advisory.py",
            MARKET / "v5_planning_runtime.py",
            MARKET / "issue_ticket.py",
            MARKET / "issue_ticket_hardened.py",
            MARKET / "FULL_DYNAMIC_RESOURCE_PLANNING.md",
            MARKET / "TASK_MATRIX_SELECTION.md",
            ROOT / "docs" / "v5-dynamic-parameter-audit-r9.md",
            ROOT / "docs" / "v5-low-cost-pilot-run-30526856028.md",
            MARKET / "team_policy.json",
            MARKET / "VALUE_SELECTION.md",
            MARKET / "legacy-cleanup-report.json",
            MARKET / "constitutional-qualification-report.json",
            ROOT / "MIGRATION_MANIFEST.json",
            ROOT / "tools" / "v5_fixture_planning_diagnostics.py",
            ROOT / "tests" / "test_v5_general_task_planning.py",
            ROOT / "tests" / "test_v5_task_resource_compiler.py",
            ROOT / "tests" / "test_v5_planning_scenario_matrix.py",
            ROOT / "tests" / "test_v5_tabletop_production_semantics.py",
            ROOT / "tests" / "test_v5_single_pass_advisory.py",
            ROOT / ".remediation",
            ROOT / ".github" / "workflows" / "one-time-apply-claude-remediation-20260803.yml",
        )
        existing = [str(path.relative_to(ROOT)) for path in forbidden if path.exists()]
        self.assertEqual([], existing)

    def test_runtime_dependency_set_has_no_numeric_planning_stack(self) -> None:
        requirements = (ROOT / "requirements-runtime.txt").read_text(
            encoding="utf-8"
        ).casefold()
        self.assertNotIn("numpy", requirements)
        self.assertNotIn("ortools", requirements)
        self.assertNotIn("scipy", requirements)

    def test_production_source_has_no_removed_imports_or_artifacts(self) -> None:
        forbidden = (
            "import resource_matrix",
            "from resource_matrix",
            "task_semantic_compiler",
            "v5_general_task_planning",
            "atomic_work_graph",
            "task_resource_artifacts",
            "compile_v5_task_resources",
            "compile_task_semantics",
            "v5-task-resources.json",
            "v5-task-semantics.json",
            "v5-atomic-work-graphs.json",
            "v5-resource-matrices.json",
            "task-resource-manifest.json",
            "import numpy",
            "from numpy",
            "cp_model",
            "optimize_execution_graph",
            "pareto_prune",
            "v5_planning_runtime",
            "planner_policy",
            "maximum_candidates_per_work",
            "solver_timeout_seconds",
            "v5-optimization.json",
            "selected_interpretation",
            "issue_ticket_hardened",
            "import issue_ticket",
        )
        offenders: list[str] = []
        for path in MARKET.glob("*.py"):
            text = path.read_text(encoding="utf-8").casefold()
            for fragment in forbidden:
                if fragment.casefold() in text:
                    offenders.append(f"{path.name}:{fragment}")
        self.assertEqual([], offenders)

    def test_catalog_layer_is_task_agnostic(self) -> None:
        text = (MARKET / "model_market.py").read_text(encoding="utf-8")
        for fragment in (
            "class TaskProfile",
            "def classify_task",
            "POLICY_FILE",
            "complexity_score",
            "high_stakes",
            "long_context",
        ):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, text)

    def test_pipeline_uses_minimal_task_envelope_only(self) -> None:
        text = (MARKET / "v5_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("from v5_task_envelope import build_task_envelope", text)
        self.assertIn('output / "v5-task-envelope.json"', text)
        self.assertNotIn("profile =", text)
        self.assertNotIn("resources =", text)
        self.assertNotIn("compact_resources", text)

    def test_materializer_does_not_score_or_repair(self) -> None:
        text = (MARKET / "v5_proposal_materializer.py").read_text(
            encoding="utf-8"
        ).casefold()
        for fragment in (
            "capability_weights",
            "demand_vector",
            "confidence_vector",
            "interpretation_id",
            "quality_per_cost",
            "fixed_weight",
            "repair_proposal",
        ):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, text)
        self.assertIn('"proposal_repaired_by_validator": false', text)

    def test_repository_overview_does_not_describe_obsolete_production_chain(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        forbidden = (
            "任务语义编译\n→ 原子工作图",
            "任务资源矩阵\n→ OpenRouter",
            "Google OR-Tools CP-SAT联合求解",
            "模型总目标权重和任务适配特征权重",
            "候选池范围和求解搜索规模",
            "风险调整后的任务效用",
        )
        for fragment in forbidden:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, text)
        self.assertIn("GPT latest", text)
        self.assertIn("Claude Opus latest", text)
        self.assertIn("确定性宪法校验器：唯一硬门", text)

    def test_all_current_docs_reject_obsolete_algorithm_as_active_architecture(self) -> None:
        forbidden_active = (
            "当前系统把模型选择改成确定性资源矩阵和CP-SAT",
            "V5 CP-SAT 优化器",
            "任务资源矩阵、候选池、优化结果",
            "v5_planning_runtime.py 仅为失败关闭哨兵",
        )
        paths = [ROOT / "README.md", *MARKET.glob("*.md"), *(ROOT / "docs").glob("*.md")]
        offenders = []
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for fragment in forbidden_active:
                if fragment in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{fragment}")
        self.assertEqual([], offenders)

    def test_claude_review_is_one_unified_advisory_call(self) -> None:
        policy = (MARKET / "v5_claude_red_team_policy.py").read_text(encoding="utf-8")
        runtime = (MARKET / "v5_governance_runtime.py").read_text(encoding="utf-8")
        self.assertIn('RED_TEAM_SCOPE = "unified_selection_and_information"', policy)
        self.assertIn('CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK = 1', policy)
        self.assertIn('"covers_internal_selection": True', policy)
        self.assertIn('"covers_external_information": True', policy)
        self.assertNotIn("RedTeamScope.INTERNAL_SELECTION", runtime)
        self.assertNotIn("RedTeamScope.EXTERNAL_INFORMATION", runtime)
        self.assertIn('"claude_red_team": 1', runtime)
        self.assertIn('"claude_gatekeeping_allowed": False', runtime)

    def test_ticket_entrypoint_has_no_runtime_monkey_patch(self) -> None:
        text = (MARKET / "v5_issue_ticket.py").read_text(encoding="utf-8")
        self.assertNotIn("_install_schema_messages", text)
        self.assertNotIn("mock.patch", text)
        self.assertNotIn("issue_ticket_hardened", text)
        self.assertNotIn("._format_schema_error =", text)


if __name__ == "__main__":
    unittest.main()
