#!/usr/bin/env python3
"""One-shot PR #227 patch for the governance output-token envelope."""
from __future__ import annotations

from pathlib import Path


GOVERNANCE = Path("open-model-market/v5_governance_runtime.py")
PIPELINE = Path("open-model-market/v5_pipeline.py")
TEST = Path("tests/test_v5_governance_completion_envelope.py")


def replace_once(path: Path, text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one replacement, found {count}")
    return text.replace(old, new, 1)


def patch_governance() -> None:
    text = GOVERNANCE.read_text(encoding="utf-8")
    text = replace_once(
        GOVERNANCE,
        text,
        "def _bind_governance_request(\n",
        '''def bounded_governance_request(
    request: Mapping[str, Any],
    maximum_completion_tokens: int | None,
) -> dict[str, Any]:
    """Apply a task envelope without exceeding a protocol-native limit."""
    bounded = dict(request)
    if maximum_completion_tokens is None:
        return bounded
    limit = int(maximum_completion_tokens)
    if limit <= 0:
        raise GovernanceRuntimeError(
            "maximum governance completion tokens must be positive"
        )
    found = False
    for key in ("max_tokens", "max_completion_tokens"):
        if key not in bounded:
            continue
        value = bounded[key]
        if isinstance(value, bool):
            raise GovernanceRuntimeError(
                "governance output limit must be a positive integer"
            )
        try:
            protocol_limit = int(value)
        except (TypeError, ValueError) as exc:
            raise GovernanceRuntimeError(
                "governance output limit must be a positive integer"
            ) from exc
        if protocol_limit <= 0:
            raise GovernanceRuntimeError(
                "governance output limit must be a positive integer"
            )
        bounded[key] = min(protocol_limit, limit)
        found = True
    if not found:
        raise GovernanceRuntimeError(
            "governance request is missing an enforceable output limit"
        )
    return bounded


def _bind_governance_request(
''',
    )
    text = replace_once(
        GOVERNANCE,
        text,
        '''    cost_anomaly_usd: float | None,
    governance_models: Mapping[str, Any] | None = None,
''',
        '''    cost_anomaly_usd: float | None,
    max_completion_tokens: int | None = None,
    governance_models: Mapping[str, Any] | None = None,
''',
    )
    text = replace_once(
        GOVERNANCE,
        text,
        '''    proposal_request = _bind_governance_request(
        build_proposal_request(
            task=task,
            task_envelope=task_envelope,
            catalog=catalog,
            **limits,
        ),
        gpt_endpoint,
    )
''',
        '''    proposal_request = _bind_governance_request(
        bounded_governance_request(
            build_proposal_request(
                task=task,
                task_envelope=task_envelope,
                catalog=catalog,
                **limits,
            ),
            max_completion_tokens,
        ),
        gpt_endpoint,
    )
''',
    )
    text = replace_once(
        GOVERNANCE,
        text,
        '''    claude_request = _bind_governance_request(
        build_claude_red_team_request(claude_input),
        claude_endpoint,
    )
''',
        '''    claude_request = _bind_governance_request(
        bounded_governance_request(
            build_claude_red_team_request(claude_input),
            max_completion_tokens,
        ),
        claude_endpoint,
    )
''',
    )
    text = replace_once(
        GOVERNANCE,
        text,
        '''    synthesis_request = _bind_governance_request(
        build_synthesis_request(
            task=task,
            initial_proposal=initial,
            claude_advice=claude_advice,
            task_envelope=task_envelope,
            catalog=catalog,
            **limits,
        ),
        gpt_endpoint,
    )
''',
        '''    synthesis_request = _bind_governance_request(
        bounded_governance_request(
            build_synthesis_request(
                task=task,
                initial_proposal=initial,
                claude_advice=claude_advice,
                task_envelope=task_envelope,
                catalog=catalog,
                **limits,
            ),
            max_completion_tokens,
        ),
        gpt_endpoint,
    )
''',
    )
    GOVERNANCE.write_text(text, encoding="utf-8")


def patch_pipeline() -> None:
    text = PIPELINE.read_text(encoding="utf-8")
    text = replace_once(
        PIPELINE,
        text,
        '''from v5_governance_runtime import (
    run_single_pass_governance,
    write_governance_artifacts,
)
''',
        '''from v5_governance_runtime import (
    bounded_governance_request,
    run_single_pass_governance,
    write_governance_artifacts,
)
''',
    )
    text = replace_once(
        PIPELINE,
        text,
        '''    if (
        args.cost_anomaly_usd is not None
        and float(args.cost_anomaly_usd) <= 0
    ):
        raise ValueError("cost_anomaly_usd must be positive")
    return total, recovery, expert_total
''',
        '''    if (
        args.cost_anomaly_usd is not None
        and float(args.cost_anomaly_usd) <= 0
    ):
        raise ValueError("cost_anomaly_usd must be positive")
    if (
        args.max_completion_tokens is not None
        and int(args.max_completion_tokens) <= 0
    ):
        raise ValueError("max_completion_tokens must be positive")
    return total, recovery, expert_total
''',
    )
    text = replace_once(
        PIPELINE,
        text,
        '''        "cost_anomaly_usd": args.cost_anomaly_usd,
        "selection_authority": "gpt-latest",
''',
        '''        "cost_anomaly_usd": args.cost_anomaly_usd,
        "max_completion_tokens": args.max_completion_tokens,
        "selection_authority": "gpt-latest",
''',
    )
    text = replace_once(
        PIPELINE,
        text,
        '''    recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> None:
    proposal_request = build_proposal_request(
        task=task,
        task_envelope=task_envelope,
        catalog=catalog,
        approved_total_calls=total_calls,
        governance_calls_reserved=CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
        approved_recovery_calls=recovery_calls,
        cost_anomaly_usd=cost_anomaly_usd,
    )
''',
        '''    recovery_calls: int,
    cost_anomaly_usd: float | None,
    max_completion_tokens: int | None,
) -> None:
    proposal_request = bounded_governance_request(
        build_proposal_request(
            task=task,
            task_envelope=task_envelope,
            catalog=catalog,
            approved_total_calls=total_calls,
            governance_calls_reserved=CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
            approved_recovery_calls=recovery_calls,
            cost_anomaly_usd=cost_anomaly_usd,
        ),
        max_completion_tokens,
    )
''',
    )
    text = replace_once(
        PIPELINE,
        text,
        '''        governance_models=governance_models,
        call_fn=governance_call_fn,
    )
''',
        '''        governance_models=governance_models,
        max_completion_tokens=args.max_completion_tokens,
        call_fn=governance_call_fn,
    )
''',
    )
    text = replace_once(
        PIPELINE,
        text,
        '''            recovery_calls=recovery_calls,
            cost_anomaly_usd=args.cost_anomaly_usd,
        )
''',
        '''            recovery_calls=recovery_calls,
            cost_anomaly_usd=args.cost_anomaly_usd,
            max_completion_tokens=args.max_completion_tokens,
        )
''',
    )
    PIPELINE.write_text(text, encoding="utf-8")


def write_test() -> None:
    TEST.write_text(
        '''import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_governance_runtime as governance  # noqa: E402
import v5_pipeline  # noqa: E402


class GovernanceCompletionEnvelopeTests(unittest.TestCase):
    def test_gpt_protocol_limit_is_reduced(self):
        request = governance.bounded_governance_request(
            {"max_tokens": 10_000},
            8_000,
        )
        self.assertEqual(request["max_tokens"], 8_000)

    def test_claude_native_limit_is_not_increased(self):
        request = governance.bounded_governance_request(
            {"max_tokens": 512},
            8_000,
        )
        self.assertEqual(request["max_tokens"], 512)

    def test_omitted_task_limit_preserves_protocol_default(self):
        request = governance.bounded_governance_request(
            {"max_tokens": 10_000},
            None,
        )
        self.assertEqual(request["max_tokens"], 10_000)

    def test_nonpositive_task_limit_is_rejected(self):
        with self.assertRaisesRegex(
            governance.GovernanceRuntimeError,
            "must be positive",
        ):
            governance.bounded_governance_request(
                {"max_tokens": 10_000},
                0,
            )

    def test_cap_survives_endpoint_parameter_conversion(self):
        bounded = governance.bounded_governance_request(
            {"max_tokens": 10_000},
            8_000,
        )
        endpoint = {
            "logical_model": "~openai/gpt-latest",
            "resolved_model": "openai/gpt-test",
            "company": "openai",
            "provider": "openai",
            "provider_fallback_allowed": False,
            "official_intelligence_rank": 1,
            "supported_parameters": ["max_completion_tokens"],
        }
        request = governance._bind_governance_request(bounded, endpoint)
        self.assertNotIn("max_tokens", request)
        self.assertEqual(request["max_completion_tokens"], 8_000)

    def test_single_pass_caps_both_gpt_calls_and_preserves_claude(self):
        captured = []

        def fake_call(_run, request):
            captured.append(dict(request))
            provider = request["provider"]["only"][0]
            return {
                "id": f"response-{len(captured)}",
                "model": request["model"],
                "provider": provider,
                "choices": [{"message": {"content": "{}"}}],
                "usage": {},
            }, 0.01

        def endpoint(company, provider, model):
            return {
                "logical_model": f"~{company}/latest",
                "resolved_model": model,
                "company": company,
                "provider": provider,
                "provider_fallback_allowed": False,
                "official_intelligence_rank": 1,
                "supported_parameters": ["max_tokens"],
            }

        governance_models = {
            "status": "PASS",
            "provider_fallback_allowed": False,
            "gpt": endpoint("openai", "openai", "openai/gpt-test"),
            "claude": endpoint("anthropic", "anthropic", "anthropic/claude-test"),
        }
        proposal = {
            "work_items": [],
            "nodes": [],
            "edges": [],
            "final_nodes": [],
        }
        with (
            mock.patch.object(
                governance,
                "build_proposal_request",
                return_value={"max_tokens": 10_000},
            ),
            mock.patch.object(
                governance,
                "build_claude_red_team_request",
                return_value={"max_tokens": 512},
            ),
            mock.patch.object(
                governance,
                "build_synthesis_request",
                return_value={"max_tokens": 10_000},
            ),
            mock.patch.object(
                governance,
                "parse_proposal",
                return_value=proposal,
            ),
            mock.patch.object(
                governance,
                "parse_claude_red_team_advice",
                return_value={"suggestions": []},
            ),
            mock.patch.object(
                governance,
                "claude_unified_review_payload",
                return_value={},
            ),
            mock.patch.object(
                governance,
                "deterministic_violations",
                return_value=[],
            ),
            mock.patch.object(
                governance,
                "materialize_proposal",
                return_value=("graph", "limits", {"status": "PASS"}),
            ),
        ):
            governance.run_single_pass_governance(
                run=SimpleNamespace(api_key="unused"),
                task="closed-world task",
                task_digest="a" * 64,
                task_envelope={},
                catalog={},
                approved_total_calls=4,
                governance_calls_reserved=3,
                approved_recovery_calls=0,
                cost_anomaly_usd=0.25,
                max_completion_tokens=8_000,
                governance_models=governance_models,
                call_fn=fake_call,
            )
        self.assertEqual(
            [row["max_tokens"] for row in captured],
            [8_000, 512, 8_000],
        )

    def test_pipeline_rejects_nonpositive_direct_limit(self):
        args = SimpleNamespace(
            maximum_total_calls=4,
            maximum_recovery_calls=0,
            cost_anomaly_usd=0.25,
            max_completion_tokens=0,
        )
        with self.assertRaisesRegex(
            ValueError,
            "max_completion_tokens must be positive",
        ):
            v5_pipeline._validate_budget(args)


if __name__ == "__main__":
    unittest.main()
''',
        encoding="utf-8",
    )


def main() -> int:
    patch_governance()
    patch_pipeline()
    write_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
