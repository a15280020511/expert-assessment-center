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
        requirements = (ROOT / "requirements-runtime.txt").read_text(encoding="utf-8").casefold()
        for package in ("numpy", "ortools", "scipy", "langchain", "crewai"):
            self.assertNotIn(package, requirements)
        self.assertIn("networkx", requirements)

    def test_price_ranked_runtime_is_the_only_active_execution_path(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        active = (
            "v5_price_ranked_issue_ticket.py",
            "v5_price_ranked_ticket_gate.py",
            "v5_price_ranked_production_ticket.py",
            "v5_price_ranked_execution_auditor.py",
            "v5_price_ranked_independent_revalidation.py",
        )
        for name in active:
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

    def test_active_orchestrator_is_deterministic_and_bounded(self) -> None:
        text = (MARKET / "v5_price_ranked_orchestrator.py").read_text(encoding="utf-8")
        self.assertIn("estimated_call_cost_usd", text)
        self.assertIn("networkx", text)
        self.assertIn("distinct model companies", text)
        self.assertIn("MIN_EXPERT_COUNT = 3", text)
        self.assertIn("MAX_EXPERT_COUNT = 6", text)
        self.assertNotIn("request_json", text)
        self.assertNotIn("OPENROUTER_API_KEY", text)
        self.assertNotIn("langchain", text.casefold())
        self.assertNotIn("crewai", text.casefold())

    def test_active_production_envelope_proves_zero_governance_calls(self) -> None:
        ticket = (MARKET / "v5_price_ranked_production_ticket.py").read_text(encoding="utf-8")
        pipeline = (MARKET / "v5_price_ranked_pipeline.py").read_text(encoding="utf-8")
        evidence = (MARKET / "v5_price_ranked_evidence.py").read_text(encoding="utf-8")
        auditor = (MARKET / "v5_price_ranked_execution_auditor.py").read_text(encoding="utf-8")
        for text in (ticket, pipeline, evidence, auditor):
            self.assertIn("claude_mechanism_enabled", text)
            self.assertIn("governance_model_calls", text)
        self.assertIn('"claude_calls": 0', ticket)
        self.assertIn("GOVERNANCE_CALLS_RESERVED = 0", pipeline)
        self.assertIn("governance model calls must equal zero", evidence)
        self.assertIn("governance ledger is not zero-call", auditor)

    def test_old_claude_modules_are_inactive_compatibility_code(self) -> None:
        # Retained temporarily for diff diagnosis and rollback evidence, but no
        # production workflow is allowed to reference them.
        for name in (
            "v5_claude_red_team_policy.py",
            "v5_governance_runtime.py",
            "v5_gpt_expert_selector.py",
        ):
            self.assertTrue((MARKET / name).is_file())
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("v5_claude_red_team_policy.py", workflow)
        self.assertNotIn("v5_governance_runtime.py", workflow)
        self.assertNotIn("v5_gpt_expert_selector.py", workflow)

    def test_ticket_entrypoint_has_no_runtime_monkey_patch(self) -> None:
        text = (MARKET / "v5_issue_ticket.py").read_text(encoding="utf-8")
        self.assertNotIn("_install_schema_messages", text)
        self.assertNotIn("mock.patch", text)
        self.assertNotIn("issue_ticket_hardened", text)
        self.assertNotIn("._format_schema_error =", text)


if __name__ == "__main__":
    unittest.main()
