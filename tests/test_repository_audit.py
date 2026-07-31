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
