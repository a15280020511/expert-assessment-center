import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_issue_ticket as issue_ticket  # noqa: E402


class TicketJsonSchemaTests(unittest.TestCase):
    @staticmethod
    def valid_packet():
        return {
            "task_id": "schema-task-0001",
            "route": "expert-team",
            "task": {"question": "验证机器可读票据合同", "requirements": ["中文"]},
            "approved_budget": {
                "calls": 6,
                "maximum_recovery_calls": 2,
                "cost_policy": "unbounded_with_anomaly_guard",
            },
        }

    def test_repository_schema_is_valid_draft_2020_12(self):
        Draft202012Validator.check_schema(issue_ticket.TICKET_SCHEMA)
        self.assertEqual(issue_ticket.TICKET_SCHEMA["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_valid_packet_has_no_schema_errors(self):
        validated, errors = issue_ticket._validate_ticket(self.valid_packet())
        self.assertEqual(errors, [])
        self.assertEqual(validated["calls"], 6)
        self.assertEqual(validated["max_cost_usd"], 0.0)
        self.assertEqual(validated["quality_tier"], "value")

    def test_structured_cross_center_evidence_is_accepted(self):
        payload = self.valid_packet()
        payload["evidence"] = [
            {
                "source_level": "A",
                "source": "compute-center result",
                "center": "compute",
                "run_id": "30264815234",
                "artifact_id": "8655681338",
                "file": "compute-result.json",
                "sha256": "a" * 64,
                "observed_at": "2026-07-27T12:00:00Z",
                "note": "正文由网页GPT取回并核验",
            }
        ]
        _, errors = issue_ticket._validate_ticket(payload)
        self.assertEqual(errors, [])

    def test_invalid_cross_center_hash_and_center_are_rejected(self):
        payload = self.valid_packet()
        payload["evidence"] = [{"center": "database", "sha256": "bad"}]
        _, errors = issue_ticket._validate_ticket(payload)
        self.assertTrue(errors)
        self.assertIn("evidence must be an object or an array.", errors)
        direct_errors = list(issue_ticket.TICKET_VALIDATOR.iter_errors(payload))
        self.assertTrue(direct_errors)
        one_of = next(error for error in direct_errors if list(error.absolute_path) == ["evidence"])
        nested_messages = "; ".join(item.message for item in one_of.context)
        self.assertIn("database", nested_messages)
        self.assertIn("bad", nested_messages)

    def test_legacy_max_cost_is_rejected(self):
        payload = self.valid_packet()
        payload["approved_budget"]["max_cost_usd"] = 1.0
        _, errors = issue_ticket._validate_ticket(payload)
        self.assertTrue(errors)

    def test_schema_reports_all_structural_error_categories(self):
        payload = {
            "task_id": "schema-task-0001",
            "route": "wrong",
            "task": {"requirements": "not-an-array", "instructions": "unsupported"},
            "evidence": "raw string",
            "approved_budget": {"calls": 1, "max_rounds": 2},
            "unexpected": True,
        }
        _, errors = issue_ticket._validate_ticket(payload)
        text = "; ".join(errors)
        self.assertIn("Unknown ticket fields", text)
        self.assertIn("route must be expert-team", text)
        self.assertIn("Unknown task fields", text)
        self.assertIn("task.question is required", text)
        self.assertIn("task.requirements must be an array", text)
        self.assertIn("evidence must be an object or an array", text)
        self.assertIn("approved_budget", text)
        self.assertIn("maximum_recovery_calls is required", text)
        self.assertGreaterEqual(len(errors), 8)

        raw_messages = "; ".join(
            error.message for error in issue_ticket.TICKET_VALIDATOR.iter_errors(payload)
        )
        self.assertIn("cost_policy", raw_messages)

    def test_schema_rejects_wrong_private_output_type(self):
        payload = self.valid_packet()
        payload["private_output"] = "yes"
        _, errors = issue_ticket._validate_ticket(payload)
        self.assertIn("private_output must be boolean.", errors)

    def test_schema_rejects_explicit_null_evidence(self):
        payload = self.valid_packet()
        payload["evidence"] = None
        _, errors = issue_ticket._validate_ticket(payload)
        self.assertIn("evidence must be an object or an array.", errors)


if __name__ == "__main__":
    unittest.main()
