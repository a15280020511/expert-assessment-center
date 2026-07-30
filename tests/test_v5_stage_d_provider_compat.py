import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_stage_d_provider_compat as compat


class V5StageDProviderCompatibilityTests(unittest.TestCase):
    def _node(self, *, machine_readable=False):
        return SimpleNamespace(
            output_contract={
                "machine_readable_required": machine_readable,
                "required_fields": ["conclusions", "risks"],
            },
            parameter_profile={
                "supported_parameters": ["response_format", "structured_outputs"]
            },
        )

    def test_truncation_is_node_scoped_not_provider_scoped(self):
        attempt = SimpleNamespace(
            error=None,
            gate_reasons=["truncated-output", "quality-score<0.64"],
            answer="partial but usable output",
        )
        failure = compat.node_scoped_failure_class(attempt, self._node())
        self.assertEqual(failure, "node_truncated_output")

    def test_invalid_json_is_node_scoped_not_provider_scoped(self):
        attempt = SimpleNamespace(error=None, gate_reasons=[], answer="not-json")
        failure = compat.node_scoped_failure_class(attempt, self._node(machine_readable=True))
        self.assertEqual(failure, "node_invalid_json")

    def test_portable_schema_removes_unsupported_array_cardinality(self):
        schema = compat.portable_strict_json_schema(self._node(machine_readable=True))
        self.assertIsNotNone(schema)
        rendered = str(schema)
        self.assertNotIn("maxItems", rendered)
        self.assertNotIn("minItems", rendered)
        self.assertIn("additionalProperties", rendered)
        self.assertEqual(
            schema["json_schema"]["schema"]["required"],
            ["conclusions", "risks"],
        )

    def test_provider_failures_remain_provider_scoped(self):
        attempt = SimpleNamespace(
            error="HTTP 503 upstream unavailable",
            gate_reasons=["call-failed"],
            answer=None,
        )
        failure = compat.node_scoped_failure_class(attempt, self._node())
        self.assertEqual(failure, "transient_provider")


if __name__ == "__main__":
    unittest.main()
