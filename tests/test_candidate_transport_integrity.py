from __future__ import annotations

import base64
import hashlib
import json
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_governance_model_plan import (  # noqa: E402
    plan_sha256,
    validate_governance_model_plan,
)
from v5_price_ranked_issue_ticket import (  # noqa: E402
    POOL_CHUNK_SCHEMA,
    POOL_TRANSPORT_SCHEMA,
    _hydrate_candidate_pool,
)
from v5_top50_pool_optimizer import materialize_candidate_pool_selection  # noqa: E402


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _fixture() -> tuple[dict, list[dict]]:
    task_id = "transport-integrity-test"
    task = {
        "question": "比较候选方案并给出建议",
        "requirements": ["保留协议完整性", "动态组织专家"],
        "language": "zh-CN",
    }
    pool = [
        {
            "model": f"vendor-{index}/reasoner-{index}",
            "company": "shared-company" if index <= 3 else f"vendor-{index}",
            "context_length": 131072,
            "max_completion_tokens": 16384,
            "prompt_usd_per_million": float(index) / 10,
            "completion_usd_per_million": float(index) / 5,
            "popularity_rank": index,
            "official_intelligence_rank": 20 - index,
        }
        for index in range(1, 12)
    ]
    raw = _canonical(pool)
    pool_sha = hashlib.sha256(raw).hexdigest()
    encoded = base64.b64encode(zlib.compress(raw, level=9)).decode("ascii")
    parts = [encoded[offset : offset + 40] for offset in range(0, len(encoded), 40)]
    transport = {
        "schema_version": POOL_TRANSPORT_SCHEMA,
        "chunk_schema_version": POOL_CHUNK_SCHEMA,
        "encoding": "zlib+base64",
        "candidate_count": len(pool),
        "raw_sha256": pool_sha,
        "chunk_count": len(parts),
        "compressed_base64_characters": len(encoded),
        "transport": "governance-created-child-issue-comments-before-run-command",
    }
    plan = {
        "schema_version": "governance-expert-dynamic-candidate-plan-v1",
        "selection_authority": "expert-assessment-center-dynamic-ortools",
        "candidate_pool_authority": "decision-system-governance",
        "model_assignment_authority": "expert-assessment-center-dynamic-ortools",
        "selection_performed_by_governance": False,
        "task_sha256": hashlib.sha256(_canonical(task)).hexdigest(),
        "selected_models": [],
        "recovery_models": [],
        "company_uniqueness_required": False,
        "fixed_team_size_required": False,
        "fixed_role_topology_required": False,
        "optimizer_optimality_required": False,
        "budget_admission_gate_enabled": False,
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "expert_candidate_pool_size": len(pool),
        "expert_candidate_pool_sha256": pool_sha,
        "expert_candidate_pool_transport": transport,
    }
    plan["plan_sha256"] = hashlib.sha256(_canonical(plan)).hexdigest()
    packet = {
        "task_id": task_id,
        "route": "expert-team",
        "task": task,
        "execution_acceptance": ["给出最终建议"],
        "evidence": [],
        "governance_model_plan": plan,
    }
    comments = [
        {
            "body": json.dumps(
                {
                    "schema_version": POOL_CHUNK_SCHEMA,
                    "task_id": task_id,
                    "sha256": pool_sha,
                    "encoding": "zlib+base64",
                    "index": index,
                    "count": len(parts),
                    "data": data,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        }
        for index, data in enumerate(parts, 1)
    ]
    return packet, comments


def _comments_file(comments: list[dict]) -> tempfile.NamedTemporaryFile:
    handle = tempfile.NamedTemporaryFile(mode="w+", suffix=".json", encoding="utf-8")
    json.dump(comments, handle, ensure_ascii=False)
    handle.flush()
    return handle


class CandidateTransportIntegrityTests(unittest.TestCase):
    def test_compact_plan_and_candidate_pool_both_verify(self) -> None:
        packet, comments = _fixture()
        with _comments_file(comments) as handle:
            hydrated, receipt = _hydrate_candidate_pool(packet, handle.name)
        plan = hydrated["governance_model_plan"]
        self.assertTrue(receipt["transport_verified"])
        self.assertEqual(receipt["candidate_count"], 11)
        self.assertEqual(len(plan["expert_candidate_pool"]), 11)
        self.assertNotIn("plan_sha256", plan)
        self.assertEqual(
            plan["governance_transport_plan_sha256"],
            receipt["governance_transport_plan_sha256"],
        )

    def test_sender_hydration_materialization_and_validation_hashes_compose(self) -> None:
        packet, comments = _fixture()
        with _comments_file(comments) as handle:
            hydrated, transport_receipt = _hydrate_candidate_pool(packet, handle.name)
        materialized, selection_receipt = materialize_candidate_pool_selection(hydrated)
        materialized_hash = materialized["governance_model_plan"]["plan_sha256"]
        self.assertEqual(
            materialized_hash,
            plan_sha256(materialized["governance_model_plan"]),
        )
        validated = validate_governance_model_plan(materialized)
        self.assertEqual(validated["plan_sha256"], plan_sha256(validated))
        self.assertEqual(
            validate_governance_model_plan(
                {**materialized, "governance_model_plan": validated}
            )["plan_sha256"],
            validated["plan_sha256"],
        )
        self.assertTrue(transport_receipt["transport_verified"])
        self.assertGreaterEqual(selection_receipt["primary_expert_count"], 1)
        self.assertEqual(validated["candidate_pool_authority"], "decision-system-governance")
        self.assertTrue(validated["selection_authority"].startswith("expert-assessment-center"))
        self.assertTrue(
            validated["model_assignment_authority"].startswith("expert-assessment-center")
        )
        self.assertFalse(validated["selection_performed_by_governance"])
        self.assertFalse(validated["company_uniqueness_required"])
        self.assertFalse(validated["optimizer_optimality_required"])
        self.assertFalse(validated["budget_admission_gate_enabled"])
        self.assertEqual(validated["provider_routing_mode"], "unrestricted-openrouter")
        self.assertFalse(validated["provider_restrictions_applied"])

    def test_tampered_compact_plan_is_rejected_before_hydration(self) -> None:
        packet, comments = _fixture()
        packet["governance_model_plan"]["provider_routing_mode"] = "tampered-provider"
        with _comments_file(comments) as handle:
            with self.assertRaisesRegex(ValueError, "compact plan sha256 mismatch"):
                _hydrate_candidate_pool(packet, handle.name)

    def test_missing_chunk_is_rejected(self) -> None:
        packet, comments = _fixture()
        with _comments_file(comments[:-1]) as handle:
            with self.assertRaisesRegex(ValueError, "transport incomplete"):
                _hydrate_candidate_pool(packet, handle.name)

    def test_conflicting_duplicate_chunk_is_rejected(self) -> None:
        packet, comments = _fixture()
        conflicting = json.loads(comments[0]["body"])
        conflicting["data"] = conflicting["data"] + "A"
        comments.append(
            {
                "body": json.dumps(
                    conflicting,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            }
        )
        with _comments_file(comments) as handle:
            with self.assertRaisesRegex(ValueError, "conflicting governance candidate transport chunk"):
                _hydrate_candidate_pool(packet, handle.name)


if __name__ == "__main__":
    unittest.main()
