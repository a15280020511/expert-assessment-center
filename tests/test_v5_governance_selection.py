from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_governance_selection import (  # noqa: E402
    GovernanceSelectionError,
    validate_governance_selection,
)


def canonical_sha(value: object) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def build_plan() -> dict:
    task = "比较三个城市公共投资方案并给出风险排序。"
    endpoints = []
    nodes = []
    roles = ("证据分析", "方案分析", "交叉审查", "最终综合")
    work_items = []
    for index in range(4):
        model = f"vendor-{index + 1}/flagship"
        provider = f"provider-{index + 1}"
        endpoints.append(
            {
                "model": model,
                "company": f"vendor-{index + 1}",
                "official_intelligence_rank": index + 1,
                "provider": provider,
                "provider_endpoint": f"{model}@{provider}",
                "context_length": 131072,
                "max_completion_tokens": 8192,
                "prompt_price_per_million": 1.0 + index,
                "completion_price_per_million": 2.0 + index,
                "supported_parameters": ["reasoning"],
            }
        )
        work_id = f"work-{index + 1}"
        work_items.append(
            {
                "work_id": work_id,
                "objective": roles[index],
                "dependencies": [] if index < 2 else ["work-1", "work-2"],
                "required_outputs": ["结论", "依据"],
            }
        )
        nodes.append(
            {
                "node_id": f"expert-{index + 1}",
                "model": model,
                "provider": provider,
                "work_ids": [work_id],
                "role": roles[index],
                "functions": ["analysis"],
                "reasoning_effort": "medium",
                "max_output_tokens": 4096,
                "recovery": [],
            }
        )
    recovery_model = "vendor-5/flagship"
    recovery_provider = "provider-5"
    endpoints.append(
        {
            "model": recovery_model,
            "company": "vendor-5",
            "official_intelligence_rank": 5,
            "provider": recovery_provider,
            "provider_endpoint": f"{recovery_model}@{recovery_provider}",
            "context_length": 131072,
            "max_completion_tokens": 8192,
            "prompt_price_per_million": 5.0,
            "completion_price_per_million": 6.0,
            "supported_parameters": ["reasoning"],
        }
    )
    nodes[-1]["recovery"] = [
        {"model": recovery_model, "provider": recovery_provider}
    ]
    proposal = {
        "schema_version": "governance-owned-expert-proposal-v1",
        "work_items": work_items,
        "nodes": nodes,
        "edges": [
            {"source": "expert-1", "target": "expert-3", "relation_type": "review"},
            {"source": "expert-2", "target": "expert-3", "relation_type": "review"},
            {"source": "expert-3", "target": "expert-4", "relation_type": "synthesis"},
        ],
        "final_nodes": ["expert-4"],
    }
    catalog = {
        "schema_version": "governance-expert-catalog-view-v1",
        "selection_authority": "decision-system-governance",
        "endpoints": endpoints,
    }
    task_sha = hashlib.sha256(task.encode("utf-8")).hexdigest()
    plan = {
        "schema_version": "governance-expert-model-selection-v1",
        "status": "PASS",
        "selection_authority": "decision-system-governance",
        "source_repository": "a15280020511/decision-system-governance",
        "source_commit": "a" * 40,
        "task_text": task,
        "task_sha256": task_sha,
        "approved_total_calls": 8,
        "approved_recovery_calls": 1,
        "selected_expert_count": 4,
        "task_envelope": {
            "schema_version": "governance-expert-task-envelope-v1",
            "task_sha256": task_sha,
            "required_context_tokens": 16384,
            "selection_authority": "decision-system-governance",
            "decomposition_authority": "decision-system-governance",
        },
        "catalog": catalog,
        "catalog_sha256": canonical_sha(catalog),
        "proposal": proposal,
        "proposal_sha256": canonical_sha(proposal),
        "model_calls": 0,
        "expert_center_selection_allowed": False,
        "expert_center_catalog_fetch_allowed": False,
        "local_fallback_allowed": False,
    }
    plan["plan_sha256"] = canonical_sha(plan)
    return plan


class GovernanceSelectionValidationTests(unittest.TestCase):
    def test_valid_governance_plan_passes_without_selection(self) -> None:
        receipt = validate_governance_selection(
            build_plan(),
            approved_total_calls=8,
            approved_recovery_calls=1,
        )
        self.assertEqual("PASS", receipt["status"])
        self.assertEqual(
            "decision-system-governance", receipt["selection_authority"]
        )
        self.assertFalse(receipt["expert_center_selection_performed"])
        self.assertFalse(receipt["expert_center_catalog_fetch_performed"])
        self.assertFalse(receipt["local_fallback_used"])

    def test_model_substitution_fails_closed(self) -> None:
        plan = build_plan()
        plan["proposal"]["nodes"][0]["model"] = "attacker/replacement"
        with self.assertRaises(GovernanceSelectionError):
            validate_governance_selection(
                plan,
                approved_total_calls=8,
                approved_recovery_calls=1,
            )

    def test_rehashed_duplicate_company_still_fails(self) -> None:
        plan = build_plan()
        plan["proposal"]["nodes"][1]["model"] = plan["proposal"]["nodes"][0]["model"]
        plan["proposal"]["nodes"][1]["provider"] = plan["proposal"]["nodes"][0]["provider"]
        plan["proposal_sha256"] = canonical_sha(plan["proposal"])
        material = dict(plan)
        material.pop("plan_sha256")
        plan["plan_sha256"] = canonical_sha(material)
        with self.assertRaisesRegex(GovernanceSelectionError, "not unique"):
            validate_governance_selection(
                plan,
                approved_total_calls=8,
                approved_recovery_calls=1,
            )

    def test_budget_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(GovernanceSelectionError, "budget mismatch"):
            validate_governance_selection(
                build_plan(),
                approved_total_calls=7,
                approved_recovery_calls=1,
            )


if __name__ == "__main__":
    unittest.main()
