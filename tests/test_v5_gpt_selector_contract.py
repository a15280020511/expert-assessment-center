from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_gpt_expert_selector import (  # noqa: E402
    GPTSelectorError,
    build_proposal_request,
    build_synthesis_request,
    governance_prompt_catalog,
    parse_proposal,
)



def catalog_fixture(
    *,
    model_count: int = 100,
    providers_per_model: int = 4,
) -> dict[str, object]:
    endpoints = []
    for model_index in range(model_count):
        model = f"company-{model_index}/model-{model_index}"
        company = f"company-{model_index}"
        for provider_index in range(providers_per_model):
            endpoints.append(
                {
                    "model": model,
                    "company": company,
                    "official_intelligence_rank": model_index + 1,
                    "provider": f"provider-{provider_index}",
                    "provider_endpoint": (
                        f"{model}@provider-{provider_index}"
                    ),
                    "context_length": 131_072 + provider_index,
                    "max_completion_tokens": 8_192 + provider_index,
                    "prompt_price_per_million": 1.0 + provider_index,
                    "completion_price_per_million": (
                        3.0 + provider_index
                    ),
                    "supported_parameters": [
                        "max_tokens",
                        "reasoning",
                        "reasoning_effort",
                        "temperature",
                        "response_format",
                    ],
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                    "synthetic_fixture_only": False,
                }
            )
    return {
        "schema_version": "v5-gpt-catalog-view-2",
        "required_context_tokens": 16_384,
        "minimum_completion_tokens": 256,
        "endpoints": endpoints,
        "rejected": [],
    }

def valid_proposal() -> dict[str, object]:
    return {
        "work_items": [
            {
                "work_id": "work-1",
                "objective": "比较两个给定方案",
                "dependencies": [],
                "required_outputs": ["唯一建议"],
            }
        ],
        "nodes": [
            {
                "node_id": "node-1",
                "work_ids": ["work-1"],
                "role": "方案比较专家",
                "functions": ["对比月费与流量", "形成唯一建议"],
                "model": "company/model",
                "provider": "provider-a",
                "reasoning_effort": "low",
                "max_output_tokens": 512,
                "recovery": [],
            }
        ],
        "edges": [],
        "final_nodes": ["node-1"],
    }


class GPTSelectorContractTests(unittest.TestCase):
    def test_unicode_descriptions_are_allowed_but_ids_stay_identifiers(self) -> None:
        proposal = valid_proposal()
        parsed = parse_proposal(json.dumps(proposal, ensure_ascii=False))
        self.assertEqual("方案比较专家", parsed["nodes"][0]["role"])
        self.assertEqual("work-1", parsed["work_items"][0]["work_id"])

    def test_empty_descriptive_functions_are_valid(self) -> None:
        proposal = valid_proposal()
        proposal["nodes"][0]["functions"] = []
        parsed = parse_proposal(json.dumps(proposal, ensure_ascii=False))
        self.assertEqual([], parsed["nodes"][0]["functions"])

    def test_unicode_internal_id_is_rejected_before_claude_boundary(self) -> None:
        proposal = valid_proposal()
        proposal["work_items"][0]["work_id"] = "工作一"
        proposal["nodes"][0]["work_ids"] = ["工作一"]
        with self.assertRaisesRegex(GPTSelectorError, "bounded identifier"):
            parse_proposal(json.dumps(proposal, ensure_ascii=False))

    def test_unicode_model_identifier_is_rejected_before_materialization(self) -> None:
        proposal = valid_proposal()
        proposal["nodes"][0]["model"] = "公司/模型"
        with self.assertRaisesRegex(GPTSelectorError, "bounded identifier"):
            parse_proposal(json.dumps(proposal, ensure_ascii=False))

    def test_unknown_relation_type_is_rejected_by_parser(self) -> None:
        proposal = valid_proposal()
        proposal["nodes"].append(copy.deepcopy(proposal["nodes"][0]))
        proposal["nodes"][1]["node_id"] = "node-2"
        proposal["edges"] = [
            {
                "source": "node-1",
                "target": "node-2",
                "relation_type": "invented",
            }
        ]
        proposal["final_nodes"] = ["node-2"]
        with self.assertRaisesRegex(GPTSelectorError, "relation_type"):
            parse_proposal(json.dumps(proposal, ensure_ascii=False))


    def test_governance_catalog_projection_preserves_every_exact_endpoint(
        self,
    ) -> None:
        catalog = catalog_fixture()
        view = governance_prompt_catalog(catalog)
        projected = {
            (model_row[0], provider_row[0])
            for model_row in view["models"]
            for provider_row in model_row[3]
        }
        source = {
            (row["model"], row["provider"])
            for row in catalog["endpoints"]
        }
        self.assertEqual(source, projected)
        self.assertEqual(len(source), view["source_endpoint_count"])
        self.assertFalse(view["local_score_computed"])
        self.assertFalse(view["optimizer_used"])
        self.assertFalse(view["pareto_pruning_used"])
        rendered = json.dumps(
            view,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertGreater(len(rendered), 0)

    def test_governance_catalog_projection_is_order_deterministic(
        self,
    ) -> None:
        catalog = catalog_fixture(model_count=4, providers_per_model=3)
        reversed_catalog = {
            **catalog,
            "endpoints": list(reversed(catalog["endpoints"])),
        }
        self.assertEqual(
            governance_prompt_catalog(catalog),
            governance_prompt_catalog(reversed_catalog),
        )

    def test_gpt_requests_use_compact_catalog_without_candidate_loss(
        self,
    ) -> None:
        catalog = catalog_fixture()
        task_envelope = {
            "required_context_tokens": 16_384,
            "task_constraints": {},
            "explicit_delivery_contract": {},
        }
        proposal = build_proposal_request(
            task="比较两个题面方案并给出唯一建议",
            task_envelope=task_envelope,
            catalog=catalog,
            approved_total_calls=4,
            governance_calls_reserved=3,
            approved_recovery_calls=0,
            cost_anomaly_usd=0.25,
        )
        synthesis = build_synthesis_request(
            task="比较两个题面方案并给出唯一建议",
            initial_proposal=valid_proposal(),
            claude_advice={"suggestions": []},
            task_envelope=task_envelope,
            catalog=catalog,
            approved_total_calls=4,
            governance_calls_reserved=3,
            approved_recovery_calls=0,
            cost_anomaly_usd=0.25,
        )
        total_input_characters = 0
        for request in (proposal, synthesis):
            content = request["messages"][1]["content"]
            total_input_characters += len(content)
            policy = request["governance_policy"]
            self.assertEqual(
                "v5-gpt-catalog-prompt-view-1",
                policy["catalog_prompt_schema"],
            )
            self.assertEqual(400, policy["catalog_source_endpoint_count"])
            self.assertFalse(policy["candidate_pruning_used"])
            self.assertFalse(policy["local_scoring_used"])
            payload = json.loads(content)
            prompt_catalog = payload["catalog"]
            self.assertEqual(
                "v5-gpt-catalog-prompt-view-1",
                prompt_catalog["schema_version"],
            )
            self.assertEqual(
                400,
                prompt_catalog["source_endpoint_count"],
            )
            self.assertNotIn("endpoints", prompt_catalog)
            self.assertLess(len(content), 40_000)
        self.assertLess(total_input_characters, 70_000)

    def test_duplicate_required_outputs_are_rejected_like_schema(self) -> None:
        proposal = valid_proposal()
        proposal["work_items"][0]["required_outputs"] = ["建议", "建议"]
        with self.assertRaisesRegex(GPTSelectorError, "required_outputs contain duplicates"):
            parse_proposal(json.dumps(proposal, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
