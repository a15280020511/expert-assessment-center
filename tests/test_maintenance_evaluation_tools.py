import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "open-model-market" / "maintenance-evaluation-tools.json"
RUNTIME = ROOT / "requirements-runtime.txt"


class MaintenanceEvaluationToolIsolationTests(unittest.TestCase):
    def test_maintenance_tools_never_enter_production_runtime(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], "expert-maintenance-evaluation-tools-v1")
        self.assertFalse(data["production_runtime_integration"])
        self.assertFalse(data["production_model_calls_enabled_by_this_registry"])
        self.assertTrue(data["tools"])
        for tool in data["tools"]:
            self.assertFalse(tool["production_runtime"], tool["id"])
            if "production_tool_use" in tool:
                self.assertFalse(tool["production_tool_use"], tool["id"])

    def test_constitutional_runtime_dependency_set_is_unchanged(self):
        runtime = {
            line.split("==", 1)[0].strip()
            for line in RUNTIME.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertEqual(runtime, {"jsonschema", "networkx", "ortools", "optuna"})

    def test_only_evaluation_tools_are_approved_for_maintenance(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        by_id = {item["id"]: item for item in data["tools"]}
        self.assertEqual(
            by_id["inspect-ai"]["status"],
            "APPROVED_MAINTENANCE_EVALUATION_CANDIDATE",
        )
        self.assertEqual(
            by_id["promptfoo"]["status"],
            "APPROVED_MAINTENANCE_REDTEAM_CANDIDATE",
        )
        self.assertEqual(
            by_id["instructor"]["status"],
            "RADAR_ONLY_RUNTIME_DUPLICATE",
        )
        self.assertEqual(
            by_id["litellm"]["status"],
            "RADAR_ONLY_ROUTING_DUPLICATE",
        )


if __name__ == "__main__":
    unittest.main()
