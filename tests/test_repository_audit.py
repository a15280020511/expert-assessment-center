import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import repository_audit  # noqa: E402


class RepositoryAuditTests(unittest.TestCase):
    def test_migration_provenance_is_not_a_live_legacy_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "MIGRATION_MANIFEST.json").write_text(
                json.dumps({
                    "source_repository": "a15280020511/" + "test",
                    "target_repository": "a15280020511/expert-assessment-center",
                }),
                encoding="utf-8",
            )
            report = repository_audit.audit(root)
        self.assertFalse(any(
            row["rule"] == "ARCH-LEGACY-REPOSITORY"
            for row in report["findings"]
        ))

    def test_live_source_reference_is_still_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.json").write_text(
                json.dumps({"repository": "a15280020511/" + "test"}),
                encoding="utf-8",
            )
            report = repository_audit.audit(root)
        findings = [
            row for row in report["findings"]
            if row["rule"] == "ARCH-LEGACY-REPOSITORY"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["path"], "runtime.json")

    def test_workflow_python_entrypoint_is_not_an_orphan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "open-model-market" / "worker.py"
            workflow = root / ".github" / "workflows" / "worker.yml"
            module.parent.mkdir(parents=True)
            workflow.parent.mkdir(parents=True)
            module.write_text("def main():\n    return 0\n", encoding="utf-8")
            workflow.write_text(
                "name: Worker\n"
                "on: workflow_dispatch\n"
                "jobs:\n"
                "  run:\n"
                "    runs-on: ubuntu-24.04\n"
                "    steps:\n"
                "      - run: python open-model-market/worker.py\n",
                encoding="utf-8",
            )
            report = repository_audit.audit(root)
        self.assertIn("worker", report["workflow_entrypoints"])
        self.assertNotIn("open-model-market/worker.py", report["orphan_candidates"])

    def test_fail_on_none_never_fails_for_findings(self):
        report = {
            "findings": [
                {"severity": "critical"},
                {"severity": "high"},
            ]
        }
        self.assertFalse(repository_audit.should_fail(report, "none"))
        self.assertTrue(repository_audit.should_fail(report, "critical"))
        self.assertTrue(repository_audit.should_fail(report, "high"))

    def test_duplicate_production_function_bodies_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market = root / "open-model-market"
            market.mkdir(parents=True)
            body = (
                "def validate(value):\n"
                "    if not isinstance(value, dict):\n"
                "        return False\n"
                "    rows = value.get('rows')\n"
                "    if not isinstance(rows, list) or len(rows) != 1:\n"
                "        return False\n"
                "    normalized = [str(item).strip() for item in rows]\n"
                "    return bool(normalized[0]) and normalized == rows\n"
            )
            (market / "one.py").write_text(body, encoding="utf-8")
            (market / "two.py").write_text(body.replace("validate", "check"), encoding="utf-8")
            report = repository_audit.audit(root)
        rules = [row["rule"] for row in report["findings"]]
        self.assertIn("PY-DUPLICATE-FUNCTION-BODY", rules)

    def test_test_only_import_does_not_hide_production_orphan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "open-model-market" / "unused.py"
            test_file = root / "tests" / "test_unused.py"
            module.parent.mkdir(parents=True)
            test_file.parent.mkdir(parents=True)
            module.write_text("VALUE = 1\n", encoding="utf-8")
            test_file.write_text("import unused\n", encoding="utf-8")
            report = repository_audit.audit(root)
        self.assertIn("open-model-market/unused.py", report["orphan_candidates"])

    def test_unreferenced_module_remains_an_orphan_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "open-model-market" / "unused.py"
            module.parent.mkdir(parents=True)
            module.write_text("VALUE = 1\n", encoding="utf-8")
            report = repository_audit.audit(root)
        self.assertIn("open-model-market/unused.py", report["orphan_candidates"])


if __name__ == "__main__":
    unittest.main()
