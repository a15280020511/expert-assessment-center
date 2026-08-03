from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

import v5_free_shadow_acceptance as shadow


class FreeShadowAcceptanceTests(unittest.TestCase):
    def endpoint(
        self,
        model: str,
        provider: str,
        *,
        structured: bool = True,
        reasoning: bool = True,
        output: bool = True,
        order: int = 1,
    ) -> shadow.FreeEndpoint:
        supported = []
        if structured:
            supported.append("response_format")
        if reasoning:
            supported.append("reasoning")
        if output:
            supported.append("max_tokens")
        return shadow.FreeEndpoint(
            model=model,
            company=shadow.canonical_model_company(model),
            provider=provider,
            context_length=131072,
            max_completion_tokens=8192,
            supported_parameters=tuple(sorted(supported)),
            official_order=order,
        )

    def test_choose_shadow_roles_requires_three_distinct_companies(self) -> None:
        rows = [
            self.endpoint("google/a:free", "Google"),
            self.endpoint("cohere/b:free", "Cohere", order=2),
            self.endpoint("qwen/c:free", "Alibaba", order=3),
        ]
        proposal, red_team, experts = shadow.choose_shadow_roles(rows)
        self.assertEqual(proposal.company, "google")
        self.assertEqual(red_team.company, "cohere")
        self.assertEqual(experts[0].company, "alibaba")

    def test_choose_shadow_roles_rejects_one_governance_company(self) -> None:
        rows = [
            self.endpoint("google/a:free", "Google"),
            self.endpoint("google/b:free", "Google", order=2),
            self.endpoint("qwen/c:free", "Alibaba", order=3),
        ]
        with self.assertRaisesRegex(
            shadow.FreeShadowError,
            "two distinct model companies",
        ):
            shadow.choose_shadow_roles(rows)

    def test_governance_requires_structured_and_reasoning(self) -> None:
        rows = [
            self.endpoint(
                "google/a:free",
                "Google",
                structured=False,
            ),
            self.endpoint("cohere/b:free", "Cohere", order=2),
            self.endpoint("qwen/c:free", "Alibaba", order=3),
        ]
        with self.assertRaises(shadow.FreeShadowError):
            shadow.choose_shadow_roles(rows)

    def test_catalog_is_zero_price_and_explicit_endpoint(self) -> None:
        endpoint = self.endpoint("qwen/c:free", "Alibaba")
        catalog = shadow.expert_catalog(
            [endpoint],
            required_context_tokens=16384,
        )
        row = catalog["endpoints"][0]
        self.assertEqual(row["prompt_price_per_million"], 0.0)
        self.assertEqual(row["completion_price_per_million"], 0.0)
        self.assertEqual(row["provider_endpoint"], "qwen/c:free@Alibaba")
        self.assertFalse(row["synthetic_fixture_only"])

    @patch.object(shadow, "request_json")
    def test_boundary_accepts_exact_free_zero_cost_response(self, request) -> None:
        request.return_value = {
            "id": "free-1",
            "model": "cohere/north:free",
            "provider": "Cohere",
            "usage": {"cost": 0.0},
            "choices": [{"message": {"content": "PASS"}}],
        }
        boundary = shadow.FreeCallBoundary(maximum_calls=1)
        response, _ = boundary(
            SimpleNamespace(api_key="x" * 24, model_timeout_seconds=1),
            {
                "model": "cohere/north:free",
                "messages": [],
                "provider": {
                    "only": ["Cohere"],
                    "order": ["Cohere"],
                    "allow_fallbacks": False,
                },
            },
        )
        self.assertEqual(response["model"], "cohere/north:free")
        self.assertEqual(boundary.receipt()["status"], "PASS")
        payload = request.call_args.args[-1]
        self.assertEqual(payload["provider"]["data_collection"], "allow")
        self.assertIs(payload["provider"]["zdr"], False)

    @patch.object(shadow, "request_json")
    def test_boundary_rejects_paid_model_response(self, request) -> None:
        request.return_value = {
            "id": "paid-1",
            "model": "cohere/north",
            "provider": "Cohere",
            "usage": {"cost": 0.0},
            "choices": [{"message": {"content": "PASS"}}],
        }
        boundary = shadow.FreeCallBoundary(maximum_calls=1)
        with self.assertRaisesRegex(shadow.FreeShadowError, "model mismatch"):
            boundary(
                SimpleNamespace(api_key="x" * 24, model_timeout_seconds=1),
                {
                    "model": "cohere/north:free",
                    "messages": [],
                    "provider": {
                        "only": ["Cohere"],
                        "allow_fallbacks": False,
                    },
                },
            )

    @patch.object(shadow, "request_json")
    def test_boundary_rejects_positive_cost(self, request) -> None:
        request.return_value = {
            "id": "free-2",
            "model": "cohere/north:free",
            "provider": "Cohere",
            "usage": {"cost": 0.000001},
            "choices": [{"message": {"content": "PASS"}}],
        }
        boundary = shadow.FreeCallBoundary(maximum_calls=1)
        with self.assertRaisesRegex(shadow.FreeShadowError, "positive cost"):
            boundary(
                SimpleNamespace(api_key="x" * 24, model_timeout_seconds=1),
                {
                    "model": "cohere/north:free",
                    "messages": [],
                    "provider": {
                        "only": ["Cohere"],
                        "allow_fallbacks": False,
                    },
                },
            )

    @patch.object(shadow, "request_json")
    def test_boundary_rejects_provider_mismatch(self, request) -> None:
        request.return_value = {
            "id": "free-3",
            "model": "cohere/north:free",
            "provider": "Other",
            "usage": {"cost": 0.0},
            "choices": [{"message": {"content": "PASS"}}],
        }
        boundary = shadow.FreeCallBoundary(maximum_calls=1)
        with self.assertRaisesRegex(shadow.FreeShadowError, "provider mismatch"):
            boundary(
                SimpleNamespace(api_key="x" * 24, model_timeout_seconds=1),
                {
                    "model": "cohere/north:free",
                    "messages": [],
                    "provider": {
                        "only": ["Cohere"],
                        "allow_fallbacks": False,
                    },
                },
            )

    def test_shadow_identity_can_never_authorize_production(self) -> None:
        proposal = self.endpoint("google/a:free", "Google")
        red_team = self.endpoint("cohere/b:free", "Cohere", order=2)
        expert = self.endpoint("qwen/c:free", "Alibaba", order=3)
        from tempfile import TemporaryDirectory
        import json

        with TemporaryDirectory() as directory:
            root = Path(directory)
            shadow._write_shadow_identity(root, proposal, red_team, [expert])
            value = json.loads((root / "free-shadow-identity.json").read_text())
        self.assertFalse(value["formal_model_identity_qualified"])
        self.assertFalse(value["production_promotion_authorized"])
        self.assertFalse(value["production_ref_moved"])


if __name__ == "__main__":
    unittest.main()
