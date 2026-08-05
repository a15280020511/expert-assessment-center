from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "execution-ticket.yml"
SCHEMA = ROOT / "open-model-market" / "execution-ticket.schema.json"
REQUIREMENTS = ROOT / "requirements-runtime.txt"


class ActiveV6ProductionContractTests(unittest.TestCase):
    def test_active_workflow_uses_only_v6_planning_and_evidence_entrypoints(self):
        text = WORKFLOW.read_text("utf-8")
        required = (
            "v6_issue_ticket.py",
            "v6_ticket_gate.py",
            "v6_production_ticket.py",
            "v6_execution_auditor.py",
            "v6_independent_artifact_revalidation.py",
            "v6_final_status.py",
            "v6_final_attestation.py",
            "self-managed-governed-expert-team-v6",
        )
        for marker in required:
            self.assertIn(marker, text)
        forbidden = (
            "v5_issue_ticket.py",
            "v5_ticket_gate.py",
            "v5_production_ticket.py",
            "v5_execution_auditor_integrity.py",
            "v5_independent_artifact_revalidation.py",
            "v5_final_status.py",
            "v5_final_attestation.py",
            "claude-opus",
            "gpt-claude",
        )
        lowered = text.lower()
        for marker in forbidden:
            self.assertNotIn(marker, lowered)

    def test_active_v6_sources_do_not_call_governance_models(self):
        sources = "\n".join(
            path.read_text("utf-8")
            for path in sorted((ROOT / "open-model-market").glob("v6_*.py"))
        ).lower()
        forbidden = (
            "claude-opus",
            "claude_red_team",
            "gpt_proposal",
            "gpt_synthesis",
            "selection_authority\": \"~openai",
            "while true",
            "langchain",
            "autogen",
            "crewai",
        )
        for marker in forbidden:
            self.assertNotIn(marker, sources)
        self.assertIn("claude_mechanism_enabled", sources)
        self.assertIn("governance-signed-roster", sources)
        self.assertIn("networkx", sources)

    def test_schema_requires_plan_roster_and_exact_budget_range(self):
        schema = json.loads(SCHEMA.read_text("utf-8"))
        required = set(schema["required"])
        self.assertIn("team_plan", required)
        self.assertIn("governance_roster", required)
        budget = schema["properties"]["approved_budget"]["properties"]
        self.assertEqual(budget["calls"]["minimum"], 2)
        self.assertEqual(budget["calls"]["maximum"], 12)
        self.assertEqual(budget["maximum_recovery_calls"]["maximum"], 4)
        roster = schema["properties"]["governance_roster"]
        self.assertFalse(roster["additionalProperties"])
        self.assertEqual(
            roster["properties"]["governance_repository"]["const"],
            "a15280020511/decision-system-governance",
        )
        self.assertEqual(roster["properties"]["model_calls_for_selection"]["const"], 0)
        self.assertFalse(roster["properties"]["secret_values_exposed"]["const"])

    def test_networkx_is_the_only_added_orchestration_package(self):
        requirements = REQUIREMENTS.read_text("utf-8").splitlines()
        self.assertIn("networkx==3.6.1", requirements)
        lowered = "\n".join(requirements).lower()
        for package in ("langchain", "autogen", "crewai", "prefect", "dagster"):
            self.assertNotIn(package, lowered)

    def test_production_workflow_has_no_write_or_identity_token_permissions(self):
        text = WORKFLOW.read_text("utf-8").lower()
        self.assertIn("contents: read", text)
        self.assertIn("issues: write", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("id-token: write", text)
        self.assertNotIn("pull-requests: write", text)
        self.assertNotIn("repository_dispatch", text)

    def test_exact_provider_and_no_tool_contract_is_present(self):
        sources = "\n".join(
            path.read_text("utf-8")
            for path in (
                ROOT / "open-model-market" / "v6_governed_roster.py",
                ROOT / "open-model-market" / "v6_execution_auditor.py",
                ROOT / "open-model-market" / "v6_independent_artifact_revalidation.py",
            )
        )
        for marker in (
            '"allow_fallbacks": False',
            "canonical_provider_lock",
            "forbidden_request_fields",
            "external_tools_allowed",
        ):
            self.assertIn(marker, sources)


if __name__ == "__main__":
    unittest.main(verbosity=2)
