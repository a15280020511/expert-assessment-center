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

    def test_native_production_modules_do_not_import_legacy_executor(self):
        production_modules = [
            "v5_runtime.py",
            "v5_constitutional_runtime.py",
            "v5_pipeline.py",
            "v5_output_contract_delivery.py",
            "v5_cost_reliability_hardening.py",
            "v5_dynamic_prompt_delivery.py",
        ]
        violations = []
        for name in production_modules:
            text = (ROOT / "open-model-market" / name).read_text(
                encoding="utf-8"
            )
            if "import v5_executor" in text or "from v5_executor" in text:
                violations.append(name)
        self.assertEqual([], violations)

    def test_constitution_has_single_machine_source_and_human_contract(self):
        machine = (
            ROOT / "open-model-market" / "v5_constitution.py"
        ).read_text(encoding="utf-8")
        human = (
            ROOT / "open-model-market" / "V5_CONSTITUTION.md"
        ).read_text(encoding="utf-8")
        self.assertIn('CONSTITUTION_VERSION = "v5-constitution-2"', machine)
        self.assertIn("v5-constitution-2", human)
        self.assertIn("词典序目标", human)
        self.assertIn("用户否定语义优先", human)

    def test_production_workflow_is_v5_only(self):
        text = (ROOT / ".github" / "workflows" / "execution-ticket.yml").read_text(encoding="utf-8")
        self.assertIn("v5_production_ticket.py", text)
        self.assertIn("v5_execution_auditor_integrity.py", text)
        self.assertNotIn("python open-model-market/v5_execution_auditor.py", text)
        self.assertNotIn("expert_team_hardened.py", text)
        self.assertNotIn("manual rollback", text.casefold())


if __name__ == "__main__":
    unittest.main()
