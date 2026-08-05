from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
WORKFLOW = ROOT / ".github" / "workflows" / "execution-ticket.yml"


class RepositoryResidueAuditTests(unittest.TestCase):
    def test_obsolete_numeric_planning_stack_is_absent(self) -> None:
        forbidden = (
            MARKET / "v5_general_task_planning.py",
            MARKET / "task_semantic_compiler.py",
            MARKET / "resource_matrix.py",
            MARKET / "atomic_work_graph.py",
            MARKET / "v5_planning_runtime.py",
            MARKET / "team_policy.json",
        )
        self.assertEqual([], [str(path) for path in forbidden if path.exists()])
        requirements = (ROOT / "requirements-runtime.txt").read_text(
            encoding="utf-8"
        ).casefold()
        for package in ("numpy", "ortools", "scipy", "langchain", "crewai"):
            self.assertNotIn(package, requirements)
        self.assertIn("networkx", requirements)

    def test_governance_selected_runtime_is_the_only_active_execution_path(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for name in (
            "v5_price_ranked_issue_ticket.py",
            "v5_price_ranked_ticket_gate.py",
            "v5_price_ranked_production_ticket.py",
            "v5_price_ranked_execution_auditor.py",
            "v5_price_ranked_independent_revalidation.py",
        ):
            self.assertIn(name, text)
        for fragment in (
            "v5_production_ticket.py",
            "v5_pipeline.py",
            "v5_gpt_expert_selector.py",
            "v5_claude_red_team_policy.py",
            "v5_governance_runtime.py",
            "claude-opus",
            "anthropic",
        ):
            self.assertNotIn(fragment, text.casefold())

    def test_local_price_ranked_selector_is_removed(self) -> None:
        self.assertFalse((MARKET / "v5_price_ranked_orchestrator.py").exists())
        pipeline = (MARKET / "v5_price_ranked_pipeline.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("v5_governance_selection", pipeline)
        self.assertIn("decision-system-governance", pipeline)
        self.assertIn("expert_center_selection_performed", pipeline)
        self.assertNotIn("build_price_ranked_proposal", pipeline)
        self.assertNotIn("fetch_live_endpoint_payloads", pipeline)
        self.assertNotIn("model_market.fetch_catalog", pipeline)

    def test_execution_fails_closed_without_governance_plan(self) -> None:
        pipeline = (MARKET / "v5_price_ranked_pipeline.py").read_text(
            encoding="utf-8"
        )
        production = (MARKET / "v5_price_ranked_production_ticket.py").read_text(
            encoding="utf-8"
        )
        issue = (MARKET / "v5_price_ranked_issue_ticket.py").read_text(
            encoding="utf-8"
        )
        for text in (pipeline, production, issue):
            self.assertIn("governance-selection", text)
            self.assertIn("local", text.casefold())
        self.assertIn("local model selection is removed", pipeline)
        self.assertIn("fail-closed-no-local-selection-runtime", production)
        self.assertIn("专家团中心选模：`已移除；0次`", issue)

    def test_artifact_and_audit_authority_is_governance(self) -> None:
        manifest = (MARKET / "artifact_manifest.py").read_text(
            encoding="utf-8"
        )
        evidence = (MARKET / "v5_price_ranked_evidence.py").read_text(
            encoding="utf-8"
        )
        auditor = (MARKET / "v5_price_ranked_execution_auditor.py").read_text(
            encoding="utf-8"
        )
        for text in (manifest, evidence, auditor):
            self.assertIn("decision-system-governance", text)
        self.assertIn("selection_occurs_in_this_repository", manifest)
        self.assertIn("catalog_fetch_occurs_in_this_repository", manifest)
        self.assertIn("expert_center_selection_performed", evidence)
        self.assertIn("expert_center_catalog_fetch_performed", auditor)
        self.assertNotIn("python-price-ranked-orchestrator", manifest)
        self.assertNotIn("python-price-ranked-orchestrator", evidence)
        self.assertNotIn("python-price-ranked-orchestrator", auditor)

    def test_obsolete_local_selection_workflows_are_absent(self) -> None:
        workflows = ROOT / ".github" / "workflows"
        obsolete = (
            workflows / "v5-live-flagship-price-ranking.yml",
            workflows / "v5-price-ranked-full-load.yml",
            workflows / "v5-price-ranked-paid-candidate-acceptance.yml",
            workflows / "v5-free-model-qualification.yml",
        )
        self.assertEqual([], [str(path) for path in obsolete if path.exists()])

    def test_ticket_entrypoint_has_no_runtime_monkey_patch(self) -> None:
        text = (MARKET / "v5_issue_ticket.py").read_text(encoding="utf-8")
        self.assertNotIn("_install_schema_messages", text)
        self.assertNotIn("mock.patch", text)
        self.assertNotIn("issue_ticket_hardened", text)
        self.assertNotIn("._format_schema_error =", text)


if __name__ == "__main__":
    unittest.main()
