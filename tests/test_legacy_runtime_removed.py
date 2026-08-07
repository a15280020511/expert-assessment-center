import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LegacyRuntimeRemovalTests(unittest.TestCase):
    def test_legacy_runtime_files_are_absent(self):
        removed = [
            "open-model-market/expert_team.py",
            "open-model-market/expert_team_hardened.py",
            "open-model-market/direct_calls.py",
            "open-model-market/task_router.py",
            "open-model-market/seat_scoring.py",
            "open-model-market/v3_benchmark_entry.py",
            "open-model-market/v3_benchmark_entry_final.py",
            "open-model-market/v3_stage_d_bounded.py",
            ".github/workflows/expert-team.yml",
            ".github/workflows/expert-team-canary.yml",
            ".github/workflows/v5-r8-stage-d-paid-blind.yml",
            ".github/workflows/v5-r8-stage-d-paid-blind-r8i.yml",
        ]
        present = [path for path in removed if (ROOT / path).exists()]
        self.assertEqual(present, [])

    def test_runtime_code_has_no_legacy_version_path(self):
        legacy_version = "v" + "3"
        forbidden = {
            legacy_version,
            "expert_team_hardened",
            "expert_team.py",
            "fixed_3_plus_1",
            "fixed 3+1",
            "manual rollback",
        }
        paths = list((ROOT / "open-model-market").glob("*.py"))
        paths += list((ROOT / ".github" / "workflows").glob("*.yml"))
        violations = {}
        for path in paths:
            text = path.read_text(encoding="utf-8").casefold()
            hits = sorted(token for token in forbidden if token.casefold() in text)
            if hits:
                violations[str(path.relative_to(ROOT))] = hits
        self.assertEqual(violations, {})

    def test_production_workflow_uses_dynamic_v5_path(self):
        text = (ROOT / ".github" / "workflows" / "execution-ticket.yml").read_text(encoding="utf-8")
        self.assertIn("v5_price_ranked_issue_ticket.py", text)
        self.assertIn("v5_dynamic_pipeline.py", text)
        self.assertNotIn("v5_price_ranked_production_ticket.py", text)
        self.assertNotIn("v5_price_ranked_ticket_gate.py", text)
        self.assertNotIn("v5_price_ranked_independent_revalidation.py", text)
        self.assertNotIn("v5_admission_lock.py", text)
        self.assertNotIn("ref: production", text)
        self.assertNotIn("expert_team_hardened.py", text)
        self.assertNotIn("manual rollback", text.casefold())


if __name__ == "__main__":
    unittest.main()
