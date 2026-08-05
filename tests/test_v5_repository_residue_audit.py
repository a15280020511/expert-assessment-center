import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
WORKFLOWS = ROOT / ".github" / "workflows"


class RepositoryResidueAuditTests(unittest.TestCase):
    def test_production_workflows_do_not_call_removed_selection_entrypoints(self) -> None:
        active_workflows = (
            "execution-ticket.yml",
            "validate.yml",
            "v5-validate.yml",
            "v5-free-model-qualification.yml",
            "v5-price-ranked-full-load.yml",
            "promote-v5-production.yml",
        )
        banned = (
            "v5_pipeline.py",
            "v5_governance_runtime.py",
            "v5_gpt_expert_selector.py",
            "v5_claude_red_team_policy.py",
            "v5_price_ranked_orchestrator.py",
        )
        offenders: list[str] = []
        for name in active_workflows:
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            for module in banned:
                pattern = rf"(?<![A-Za-z0-9_]){re.escape(module)}(?![A-Za-z0-9_])"
                if re.search(pattern, text):
                    offenders.append(f"{name}:{module}")
        self.assertFalse(offenders, offenders)

    def test_active_governance_plan_runtime_has_no_claude_dependency(self) -> None:
        active_paths = (
            MARKET / "v5_price_ranked_pipeline.py",
            MARKET / "v5_governed_plan_orchestrator.py",
            MARKET / "v5_price_ranked_production_ticket.py",
            MARKET / "v5_governance_model_plan.py",
        )
        forbidden_imports = (
            "import v5_claude",
            "from v5_claude",
            "import v5_governance_runtime",
            "from v5_governance_runtime",
            "claude-opus",
            "anthropic/",
        )
        for path in active_paths:
            text = path.read_text(encoding="utf-8").lower()
            for fragment in forbidden_imports:
                self.assertNotIn(fragment, text, path.name)

    def test_active_orchestrator_only_materializes_governance_plan(self) -> None:
        text = (MARKET / "v5_governed_plan_orchestrator.py").read_text(
            encoding="utf-8"
        )
        for required in (
            "build_governed_proposal",
            "validate_governance_model_plan",
            "model_selection_performed_locally",
            "model_reranking_performed_locally",
            "provider_resolution_performed_locally",
        ):
            self.assertIn(required, text)
        for forbidden in (
            "rank_price_ranked_candidates",
            "select_qualified_models",
            "build_price_ranked_proposal",
            "prompt_template",
            "build_request",
            "call_fn",
        ):
            self.assertNotIn(forbidden, text)

    def test_active_production_envelope_proves_governance_boundary(self) -> None:
        production = (
            MARKET / "v5_price_ranked_production_ticket.py"
        ).read_text(encoding="utf-8")
        auditor = (
            MARKET / "v5_price_ranked_execution_auditor.py"
        ).read_text(encoding="utf-8")
        for text in (production, auditor):
            for required in (
                "selection_authority",
                "decision-system-governance",
                "model_selection_performed_locally",
                "model_reranking_performed_locally",
                "model_substitution_allowed",
                "governance_model_plan_sha256",
                "claude_mechanism_enabled",
            ):
                self.assertIn(required, text)
        self.assertIn("governance_model_calls", auditor)
        self.assertIn("expert-final-synthesis", auditor)

    def test_local_price_ranked_selector_is_removed(self) -> None:
        self.assertFalse((MARKET / "v5_price_ranked_orchestrator.py").exists())
        self.assertFalse(
            (WORKFLOWS / "v5-live-flagship-price-ranking.yml").exists()
        )
        active = (MARKET / "v5_price_ranked_pipeline.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("build_price_ranked_proposal", active)
        self.assertIn("build_governed_proposal", active)

    def test_archived_governance_modules_remain_outside_production_path(self) -> None:
        legacy_modules = (
            "v5_claude_red_team.py",
            "v5_gpt_proposer.py",
            "v5_gpt_synthesizer.py",
            "v5_governance_runtime.py",
            "v5_governance_models.py",
        )
        active_workflows = (
            "execution-ticket.yml",
            "validate.yml",
            "v5-validate.yml",
            "v5-free-model-qualification.yml",
            "v5-price-ranked-full-load.yml",
            "promote-v5-production.yml",
        )
        workflow_text = "\n".join(
            (WORKFLOWS / name).read_text(encoding="utf-8")
            for name in active_workflows
        )
        production_text = "\n".join(
            (MARKET / name).read_text(encoding="utf-8")
            for name in (
                "v5_price_ranked_pipeline.py",
                "v5_governed_plan_orchestrator.py",
                "v5_price_ranked_production_ticket.py",
            )
        )
        for name in legacy_modules:
            self.assertNotIn(name, workflow_text)
            self.assertNotIn(name, production_text)


if __name__ == "__main__":
    unittest.main()
