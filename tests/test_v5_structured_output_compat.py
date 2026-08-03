from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_governance_runtime import _api_payload, _request_receipt  # noqa: E402
from v5_structured_output_compat import (  # noqa: E402
    StructuredOutputCompatibilityError,
    normalize_strict_response_format,
)


class StructuredOutputCompatibilityTests(unittest.TestCase):
    @staticmethod
    def response_format() -> dict:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "fixture_contract",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "items": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 8,
                            "uniqueItems": True,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "id": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 64,
                                        "pattern": "^[A-Za-z0-9_-]+$",
                                    },
                                    "score": {
                                        "type": "integer",
                                        "minimum": 0,
                                        "maximum": 100,
                                    },
                                    "kind": {
                                        "type": "string",
                                        "enum": ["fact", "inference"],
                                    },
                                },
                                "required": ["id", "score", "kind"],
                            },
                        }
                    },
                    "required": ["items"],
                },
            },
        }

    @staticmethod
    def contains_key(value, target: str) -> bool:
        if isinstance(value, dict):
            return target in value or any(
                StructuredOutputCompatibilityTests.contains_key(item, target)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(
                StructuredOutputCompatibilityTests.contains_key(item, target)
                for item in value
            )
        return False

    def test_recursive_unsupported_keywords_are_removed(self) -> None:
        normalized, audit = normalize_strict_response_format(
            self.response_format()
        )
        for key in (
            "uniqueItems",
            "minItems",
            "maxItems",
            "minLength",
            "maxLength",
            "pattern",
            "minimum",
            "maximum",
        ):
            self.assertFalse(self.contains_key(normalized, key), key)
            self.assertGreater(audit["removed_keyword_counts"].get(key, 0), 0)
        self.assertEqual("PASS", audit["status"])
        self.assertTrue(audit["deterministic_post_parse_validation_required"])

    def test_strict_shape_required_and_enum_are_preserved(self) -> None:
        normalized, _ = normalize_strict_response_format(
            self.response_format()
        )
        json_schema = normalized["json_schema"]
        schema = json_schema["schema"]
        self.assertTrue(json_schema["strict"])
        self.assertEqual(["items"], schema["required"])
        self.assertFalse(schema["additionalProperties"])
        item = schema["properties"]["items"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(["id", "score", "kind"], item["required"])
        self.assertEqual(
            ["fact", "inference"],
            item["properties"]["kind"]["enum"],
        )

    def test_non_strict_schema_is_rejected(self) -> None:
        value = self.response_format()
        value["json_schema"]["strict"] = False
        with self.assertRaises(StructuredOutputCompatibilityError):
            normalize_strict_response_format(value)

    def test_wire_payload_is_sanitized_without_internal_metadata(self) -> None:
        request = {
            "model": "openai/gpt-5.6-sol",
            "logical_model": "~openai/gpt-latest",
            "messages": [{"role": "user", "content": "fixture"}],
            "max_tokens": 512,
            "reasoning": {"effort": "high", "exclude": True},
            "response_format": self.response_format(),
            "provider": {
                "only": ["openai"],
                "order": ["openai"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            "governance_endpoint": {
                "provider": "openai",
                "supported_parameters": [
                    "max_tokens",
                    "reasoning",
                    "response_format",
                ],
            },
        }
        payload = _api_payload(request)
        self.assertNotIn("logical_model", payload)
        self.assertNotIn("governance_endpoint", payload)
        self.assertFalse(
            self.contains_key(payload["response_format"], "uniqueItems")
        )
        self.assertEqual("openai/gpt-5.6-sol", payload["model"])

    def test_receipt_records_schema_normalization_without_raw_content(self) -> None:
        request = {
            "model": "openai/gpt-5.6-sol",
            "logical_model": "~openai/gpt-latest",
            "messages": [{"role": "user", "content": "sensitive fixture"}],
            "max_tokens": 512,
            "reasoning": {"effort": "high", "exclude": True},
            "response_format": self.response_format(),
            "provider": {
                "only": ["openai"],
                "order": ["openai"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
        }
        receipt = _request_receipt(request)
        audit = receipt["schema_compatibility"]
        self.assertEqual("PASS", audit["status"])
        self.assertEqual(
            1,
            audit["removed_keyword_counts"]["uniqueItems"],
        )
        self.assertEqual("fixture_contract", receipt["response_schema"])
        self.assertFalse(receipt["raw_message_content_persisted"])
        self.assertNotIn("sensitive fixture", str(receipt))


if __name__ == "__main__":
    unittest.main()
