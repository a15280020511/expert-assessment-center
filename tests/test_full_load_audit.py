"""Deterministic, zero-cost full-load audit tests.

These tests intentionally encode repository invariants rather than mocking a
successful result. They use no network and no model API key. A failure is an
auditable finding that the current production implementation violates the
stated control-plane policy under load or recovery conditions.
"""
from __future__ import annotations

import inspect
import itertools
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

import issue_ticket_hardened  # noqa: E402
import performance_history  # noqa: E402
import seat_scoring  # noqa: E402
import expert_team_hardened  # noqa: E402


class FullLoadAuditTests(unittest.TestCase):
    @staticmethod
    def _packet(requirements: list[str]) -> dict:
        return {
            "task_id": "full-load-audit-0001",
            "route": "expert-team",
            "objective": "控制面审计元数据",
            "task": {
                "question": "对同一个实质任务执行固定三专家加一裁判分析。",
                "requirements": requirements,
                "language": "zh-CN",
            },
            "evidence": {"note": "确定性测试输入"},
            "approved_budget": {"calls": 6},
            "private_output": False,
        }

    def test_semantic_fingerprint_is_invariant_to_requirement_order(self):
        """Reordering a set of identical requirements must not bypass deduplication."""
        requirements = ["覆盖成本", "覆盖风险", "给出唯一建议", "使用中文"]
        fingerprints = {
            issue_ticket_hardened._substantive_task_fingerprint(self._packet(list(order)))
            for order in itertools.permutations(requirements)
        }
        self.assertEqual(
            len(fingerprints),
            1,
            "The same substantive requirements generate different fingerprints when reordered.",
        )

    def test_32_parallel_history_updates_are_lossless_and_parseable(self):
        """All expert/judge history writes must survive a forced read-modify-write collision."""
        writers = 32
        barrier = threading.Barrier(writers)
        original_load = performance_history.load_history

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model-performance.json"

            def synchronized_load(target: Path):
                snapshot = original_load(target)
                barrier.wait(timeout=15)
                return snapshot

            def write_one(index: int) -> None:
                performance_history.record(
                    path,
                    model_id=f"provider-{index}/model-{index}",
                    success=True,
                    latency_seconds=0.1,
                    actual_cost=0.01,
                    estimated_cost=0.01,
                    finish_reason="stop",
                    error=None,
                    reasoning_tokens=10,
                    completion_tokens=100,
                )

            with mock.patch.object(performance_history, "load_history", side_effect=synchronized_load):
                with ThreadPoolExecutor(max_workers=writers) as pool:
                    list(pool.map(write_one, range(writers)))

            payload = json.loads(path.read_text(encoding="utf-8"))
            models = payload.get("models", {})
            self.assertEqual(
                len(models),
                writers,
                "Concurrent history writes lost model records; an atomic lock/merge is required.",
            )

    def test_one_transient_truncation_does_not_blacklist_a_new_model(self):
        """A single sample must not make a model ineligible before it can recover."""
        stats = {
            "calls": 1,
            "successes": 0,
            "empty_answers": 0,
            "truncated": 1,
            "timeouts": 0,
            "avg_actual_to_estimated_cost": 1.0,
            "avg_reasoning_share": 0.1,
        }
        score = performance_history.history_score(stats)
        self.assertGreaterEqual(
            score,
            0.30,
            "One transient truncation falls below the production eligibility bucket and prevents recovery sampling.",
        )

    def test_expert_replacement_inherits_original_quality_tier(self):
        """Replacement ordering must preserve budget/value/quality semantics."""
        signature = inspect.signature(seat_scoring.replacement_candidates)
        source = inspect.getsource(seat_scoring.replacement_candidates)
        accepts_policy = "tier" in signature.parameters or "run" in signature.parameters
        hardcoded_value = '"value"' in source or "'value'" in source
        self.assertTrue(
            accepts_policy and not hardcoded_value,
            "Replacement candidates are hard-coded to the value tier instead of inheriting the active policy.",
        )

    def test_judge_replacement_keeps_stability_and_top50_gates(self):
        """Judge fallback must not escape the same eligibility gates used by initial selection."""
        source = inspect.getsource(expert_team_hardened._hardened_candidate_judges)
        self.assertIn("_stable_pool", source)
        self.assertIn("_history_rejects_judge", source)
        self.assertIn("run.quality_tier", source)

    def test_32_parallel_updates_to_one_model_preserve_call_count(self):
        writers = 32
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model-performance.json"

            def write_one(_index: int) -> None:
                performance_history.record(
                    path,
                    model_id="provider/shared-model",
                    success=True,
                    latency_seconds=0.1,
                    actual_cost=0.01,
                    estimated_cost=0.01,
                    finish_reason="stop",
                    error=None,
                    reasoning_tokens=10,
                    completion_tokens=100,
                )

            with ThreadPoolExecutor(max_workers=writers) as pool:
                list(pool.map(write_one, range(writers)))
            stats = performance_history.load_history(path)["provider/shared-model"]
            self.assertEqual(stats["calls"], writers)
            self.assertEqual(stats["successes"], writers)


if __name__ == "__main__":
    unittest.main()
