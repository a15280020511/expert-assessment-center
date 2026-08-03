from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
from v5_governance_catalog import (  # noqa: E402
    GovernanceCatalogError,
    resolve_live_governance_models,
)
from v5_governance_runtime import (  # noqa: E402
    _api_payload,
    _bind_governance_request,
)


class GovernanceModelResolutionTests(unittest.TestCase):
    @staticmethod
    def model(model_id: str, rank: int) -> model_market.ModelInfo:
        return model_market.ModelInfo(
            id=model_id,
            name=model_id,
            description="fixture",
            author=model_id.split("/", 1)[0],
            context_length=1_050_000,
            max_completion_tokens=128_000,
            prompt_price_per_million=1.0,
            completion_price_per_million=3.0,
            supported_parameters=[
                "max_tokens",
                "reasoning",
                "response_format",
            ],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            ranks={"intelligence-high-to-low": rank},
        )

    @staticmethod
    def endpoint(provider: str, *, structured: bool = True) -> dict:
        supported = ["max_tokens", "reasoning"]
        if structured:
            supported.extend(["response_format", "structured_outputs"])
        return {
            "data": {
                "endpoints": [
                    {
                        "tag": provider,
                        "context_length": 1_050_000,
                        "max_completion_tokens": 128_000,
                        "supported_parameters": supported,
                        "pricing": {
                            "prompt": "0.000001",
                            "completion": "0.000003",
                        },
                    }
                ]
            }
        }

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
                        "value": {"type": "string", "minLength": 1}
                    },
                    "required": ["value"],
                },
            },
        }

    def fixture(self) -> tuple[dict, dict]:
        models = {
            "openai/gpt-5.6-sol": self.model(
                "openai/gpt-5.6-sol", 3
            ),
            "openai/gpt-5.5": self.model("openai/gpt-5.5", 7),
            "openai/gpt-5.4-mini": self.model(
                "openai/gpt-5.4-mini", 2
            ),
            "anthropic/claude-opus-5": self.model(
                "anthropic/claude-opus-5", 1
            ),
            "anthropic/claude-opus-4.8": self.model(
                "anthropic/claude-opus-4.8", 5
            ),
        }
        endpoints = {
            "openai/gpt-5.6-sol": self.endpoint("openai"),
            "openai/gpt-5.5": self.endpoint("openai"),
            "openai/gpt-5.4-mini": self.endpoint("openai"),
            "anthropic/claude-opus-5": self.endpoint("anthropic"),
            "anthropic/claude-opus-4.8": self.endpoint("anthropic"),
        }
        return models, endpoints

    def test_official_rank_selects_strongest_full_models(self) -> None:
        models, endpoints = self.fixture()
        resolved = resolve_live_governance_models(
            models,
            endpoints,
            required_context_tokens=16_384,
        )
        self.assertEqual(
            "openai/gpt-5.6-sol",
            resolved["gpt"]["resolved_model"],
        )
        self.assertEqual(
            "anthropic/claude-opus-5",
            resolved["claude"]["resolved_model"],
        )
        self.assertEqual("openai", resolved["gpt"]["provider"])
        self.assertEqual("anthropic", resolved["claude"]["provider"])
        self.assertFalse(resolved["provider_fallback_allowed"])

    def test_mini_gpt_is_not_promoted_over_full_model(self) -> None:
        models, endpoints = self.fixture()
        resolved = resolve_live_governance_models(
            models,
            endpoints,
            required_context_tokens=16_384,
        )
        self.assertNotIn("mini", resolved["gpt"]["resolved_model"])

    def test_binding_removes_unsupported_temperature(self) -> None:
        models, endpoints = self.fixture()
        resolved = resolve_live_governance_models(
            models,
            endpoints,
            required_context_tokens=16_384,
        )
        request = {
            "model": "~openai/gpt-latest",
            "messages": [{"role": "user", "content": "fixture"}],
            "temperature": 0,
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
        bound = _bind_governance_request(request, resolved["gpt"])
        self.assertNotIn("temperature", bound)
        self.assertEqual("openai/gpt-5.6-sol", bound["model"])
        self.assertEqual("~openai/gpt-latest", bound["logical_model"])
        self.assertEqual(["openai"], bound["provider"]["only"])

    def test_binding_metadata_is_not_sent_to_openrouter(self) -> None:
        models, endpoints = self.fixture()
        resolved = resolve_live_governance_models(
            models,
            endpoints,
            required_context_tokens=16_384,
        )
        request = {
            "model": "~openai/gpt-latest",
            "messages": [{"role": "user", "content": "fixture"}],
            "max_tokens": 512,
            "reasoning": {"effort": "high", "exclude": True},
            "response_format": self.response_format(),
            "provider": {},
        }
        payload = _api_payload(
            _bind_governance_request(request, resolved["gpt"])
        )
        self.assertNotIn("logical_model", payload)
        self.assertNotIn("governance_endpoint", payload)
        self.assertEqual("openai/gpt-5.6-sol", payload["model"])
        self.assertNotIn(
            "minLength",
            payload["response_format"]["json_schema"]["schema"]
            ["properties"]["value"],
        )

    def test_resolution_accepts_endpoint_without_output_limit_parameter(self) -> None:
        models, endpoints = self.fixture()
        for payload in endpoints.values():
            endpoint = payload["data"]["endpoints"][0]
            endpoint["supported_parameters"] = [
                value
                for value in endpoint["supported_parameters"]
                if value not in {"max_tokens", "max_completion_tokens"}
            ]
        resolved = resolve_live_governance_models(
            models,
            endpoints,
            required_context_tokens=16_384,
        )
        self.assertFalse(
            resolved["local_token_ceiling_parameter_required"]
        )
        self.assertFalse(
            resolved["gpt"]["local_token_ceiling_parameter_required"]
        )
        self.assertNotIn(
            "max_tokens",
            resolved["gpt"]["supported_parameters"],
        )
        self.assertEqual(
            resolved["minimum_completion_tokens"],
            resolved["minimum_native_completion_capacity_tokens"],
        )

    def test_missing_direct_structured_endpoint_fails_closed(self) -> None:
        models, endpoints = self.fixture()
        endpoints["anthropic/claude-opus-5"] = self.endpoint(
            "anthropic",
            structured=False,
        )
        endpoints["anthropic/claude-opus-4.8"] = self.endpoint(
            "anthropic",
            structured=False,
        )
        with self.assertRaises(GovernanceCatalogError):
            resolve_live_governance_models(
                models,
                endpoints,
                required_context_tokens=16_384,
            )

    def test_binding_accepts_endpoint_without_output_limit_parameter(self) -> None:
        endpoint = {
            "logical_model": "~openai/gpt-latest",
            "resolved_model": "openai/gpt-5.6-sol",
            "company": "openai",
            "provider": "openai",
            "supported_parameters": [
                "reasoning",
                "response_format",
            ],
            "provider_fallback_allowed": False,
        }
        request = {
            "model": "~openai/gpt-latest",
            "messages": [],
            "reasoning": {"effort": "high"},
            "response_format": self.response_format(),
        }
        bound = _bind_governance_request(request, endpoint)
        self.assertEqual("openai/gpt-5.6-sol", bound["model"])
        self.assertNotIn("max_tokens", bound)
        self.assertNotIn("max_completion_tokens", bound)


if __name__ == "__main__":
    unittest.main()
