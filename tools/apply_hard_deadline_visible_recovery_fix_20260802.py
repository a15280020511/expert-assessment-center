#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one replacement in {path}: {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch_openrouter_deadline() -> None:
    path = ROOT / "open-model-market" / "openrouter_api.py"
    replace_once(
        path,
        "import random\nimport time\nimport urllib.error\n",
        "import queue\nimport random\nimport threading\nimport time\nimport urllib.error\n",
    )
    marker = '''def request_json(
'''
    helper = '''def _request_with_hard_deadline(
    request: urllib.request.Request,
    url: str,
    timeout_seconds: float,
) -> Dict[str, Any]:
    """Bound the complete open/read/decode operation by wall-clock time.

    ``urllib`` applies its timeout to individual socket operations, not to the
    whole request lifecycle. A daemon worker prevents a slow upstream response
    from holding the production runtime beyond the configured model deadline.
    """
    timeout = max(0.001, float(timeout_seconds))
    results: queue.Queue[tuple[str, Any]] = queue.Queue()

    def worker() -> None:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                results.put(("ok", _decode_response(response, url)))
        except BaseException as exc:  # forwarded to the caller thread
            results.put(("error", exc))

    thread = threading.Thread(
        target=worker,
        name="openrouter-hard-deadline",
        daemon=True,
    )
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise OpenRouterRequestError(
            f"OpenRouter request exceeded hard deadline of {timeout:g} seconds.",
            category="timeout",
            retryable=True,
            request_sent=True,
            response_received=False,
        )
    try:
        status, value = results.get_nowait()
    except queue.Empty as exc:
        raise OpenRouterRequestError(
            "OpenRouter request worker exited without a result.",
            category="invalid_response",
            retryable=False,
            request_sent=True,
            response_received=False,
        ) from exc
    if status == "error":
        raise value
    return value


'''
    replace_once(path, marker, helper + marker)
    replace_once(
        path,
        '''            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return _decode_response(response, url)
''',
        '''            return _request_with_hard_deadline(
                request,
                url,
                timeout_seconds,
            )
''',
    )


def patch_runtime_recovery() -> None:
    path = ROOT / "open-model-market" / "v5_runtime.py"
    replace_once(
        path,
        "from dataclasses import asdict, dataclass, field\n",
        "from dataclasses import asdict, dataclass, field, replace\n",
    )
    insert_after = '''    @staticmethod
    def _actual_cost(response: Mapping[str, Any]) -> float:
        usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
        for key in ("cost", "total_cost"):
            try:
                if usage.get(key) is not None:
                    return max(0.0, float(usage[key]))
            except (TypeError, ValueError):
                continue
        return 0.0

'''
    helpers = insert_after + '''    @staticmethod
    def _reasoning_saturation_evidence(
        usage: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        details = usage.get("completion_tokens_details")
        details = details if isinstance(details, Mapping) else {}
        try:
            completion = max(0, int(usage.get("completion_tokens") or 0))
        except (TypeError, ValueError):
            completion = 0
        try:
            reasoning = max(0, int(details.get("reasoning_tokens") or 0))
        except (TypeError, ValueError):
            reasoning = 0
        reasoning_request = request.get("reasoning")
        reasoning_request = (
            reasoning_request if isinstance(reasoning_request, Mapping) else {}
        )
        try:
            requested_reasoning = max(
                0,
                int(reasoning_request.get("max_tokens") or 0),
            )
        except (TypeError, ValueError):
            requested_reasoning = 0
        ratio = reasoning / completion if completion else 0.0
        saturated = bool(
            completion > 0
            and reasoning > 0
            and (
                ratio >= 0.75
                or (
                    requested_reasoning > 0
                    and reasoning >= requested_reasoning
                )
            )
        )
        return {
            "completion_tokens": completion,
            "reasoning_tokens": reasoning,
            "requested_reasoning_max_tokens": requested_reasoning,
            "reasoning_share": round(ratio, 6),
            "reasoning_saturated_empty_output": saturated,
        }

    @classmethod
    def _reasoning_saturated_attempt(
        cls,
        attempt: RuntimeAttempt | None,
    ) -> bool:
        if attempt is None or attempt.answer:
            return False
        evidence = cls._reasoning_saturation_evidence(
            attempt.usage,
            attempt.request,
        )
        return bool(evidence["reasoning_saturated_empty_output"])

    @staticmethod
    def _visible_output_only_candidate(
        node: SelectedNode,
    ) -> SelectedNode:
        reasoning_profile = dict(node.reasoning_profile)
        reasoning_profile.update({
            "reasoning_enabled": False,
            "effort": "minimal",
            "recovery_visible_output_only": True,
        })
        parameter_profile = dict(node.parameter_profile)
        decisions = parameter_profile.get("dynamic_parameter_decisions")
        decisions = dict(decisions) if isinstance(decisions, Mapping) else {}
        decisions.update({
            "reasoning_effort": "disabled-after-reasoning-saturation",
            "visible_output_only_recovery": True,
        })
        parameter_profile["dynamic_parameter_decisions"] = decisions
        parameter_profile["visible_output_only_recovery"] = True
        request_config = dict(node.request_config)
        request_config.pop("reasoning", None)
        return replace(
            node,
            reasoning_profile=reasoning_profile,
            parameter_profile=parameter_profile,
            request_config=request_config,
        )

'''
    replace_once(path, insert_after, helpers)

    old_empty = '''            if not answer:
                failure = ExecutionFailure(
                    category=FailureCategory.PROVIDER_EMPTY_RESPONSE,
                    retryable=True,
                    model=node.model,
                    provider_endpoint=node.provider_endpoint,
                    request_sent=True,
                    response_received=True,
                    usage_received=bool(usage),
                    actual_cost_usd=actual_cost,
                    message="provider returned no usable answer",
                )
                return RuntimeAttempt(
                    attempt_index, kind, node.node_id, node.model, node.provider_endpoint,
                    payload, "call_failed", None, 0.0, ["empty-output"],
                    round(float(latency), 6), usage,
                    str(response.get("id") or "") or None,
                    str(response.get("model") or node.model) or None,
                    str(response.get("provider") or "") or None,
                    failure.to_dict(),
                )
'''
    new_empty = '''            if not answer:
                saturation = self._reasoning_saturation_evidence(usage, payload)
                gate_reasons = ["empty-output"]
                transformations: list[Mapping[str, Any]] = []
                message = "provider returned no usable answer"
                if saturation["reasoning_saturated_empty_output"]:
                    gate_reasons.append("reasoning-saturated-empty-output")
                    message += " after reasoning consumed the visible-output path"
                    transformations.append({
                        "type": "reasoning-saturation-evidence",
                        **saturation,
                    })
                failure = ExecutionFailure(
                    category=FailureCategory.PROVIDER_EMPTY_RESPONSE,
                    retryable=True,
                    model=node.model,
                    provider_endpoint=node.provider_endpoint,
                    request_sent=True,
                    response_received=True,
                    usage_received=bool(usage),
                    actual_cost_usd=actual_cost,
                    message=message,
                )
                return RuntimeAttempt(
                    attempt_index, kind, node.node_id, node.model, node.provider_endpoint,
                    payload, "call_failed", None, 0.0, gate_reasons,
                    round(float(latency), 6), usage,
                    str(response.get("id") or "") or None,
                    str(response.get("model") or node.model) or None,
                    str(response.get("provider") or "") or None,
                    failure.to_dict(),
                    answer_transformations=transformations,
                )
'''
    replace_once(path, old_empty, new_empty)

    old_recovery = '''        alternatives = [self._candidate(row, selected) for row in recovery_rows]
        last_attempted_node = selected
        if category in self.recovery_policy.replace_categories:
            for replacement in alternatives:
                attempted = call(replacement, "replacement")
                if attempted is None:
                    continue
                last_attempted_node = replacement
                if attempted.status == "passed":
                    return self._node_result(
                        selected, replacement, attempts, attempted, "success_recovered"
                    )
                if self._degraded_usable(replacement, attempted) and (
                    best is None or attempted.quality_score > best[0].quality_score
                ):
                    best = (attempted, replacement)
'''
    new_recovery = '''        alternatives = [self._candidate(row, selected) for row in recovery_rows]
        last_attempted_node = selected
        source_attempt = attempts[-1] if attempts else initial
        reasoning_saturated = self._reasoning_saturated_attempt(source_attempt)
        if category in self.recovery_policy.replace_categories:
            for replacement in alternatives:
                original_replacement = replacement
                adaptation: dict[str, Any] | None = None
                if reasoning_saturated:
                    replacement = self._visible_output_only_candidate(replacement)
                    adaptation = {
                        "type": "recovery-request-adaptation",
                        "policy": "reasoning-saturated-empty-output-visible-only-v1",
                        "source_model": source_attempt.model if source_attempt else None,
                        "source_provider_endpoint": (
                            source_attempt.provider_endpoint if source_attempt else None
                        ),
                        "replacement_model": replacement.model,
                        "replacement_provider_endpoint": replacement.provider_endpoint,
                        "reasoning_removed": True,
                        "substantive_prompt_changed": False,
                    }
                attempted = call(replacement, "replacement")
                if attempted is None:
                    continue
                if adaptation is not None:
                    attempted.answer_transformations.append(adaptation)
                last_attempted_node = replacement
                if attempted.status == "passed":
                    return self._node_result(
                        selected, replacement, attempts, attempted, "success_recovered"
                    )
                quality_node = replacement if adaptation is not None else original_replacement
                if self._degraded_usable(quality_node, attempted) and (
                    best is None or attempted.quality_score > best[0].quality_score
                ):
                    best = (attempted, replacement)
'''
    replace_once(path, old_recovery, new_recovery)


def write_tests() -> None:
    timeout_test = ROOT / "tests" / "test_openrouter_hard_deadline.py"
    timeout_test.write_text(
        '''from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from openrouter_api import OpenRouterRequestError, request_json  # noqa: E402


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class OpenRouterHardDeadlineTests(unittest.TestCase):
    def test_complete_request_is_bounded_by_wall_clock_deadline(self) -> None:
        def slow_urlopen(*_args, **_kwargs):
            time.sleep(0.40)
            return _Response({"ok": True})

        started = time.monotonic()
        with patch("openrouter_api.urllib.request.urlopen", slow_urlopen):
            with self.assertRaises(OpenRouterRequestError) as raised:
                request_json(
                    "https://example.invalid/chat",
                    "key",
                    0.05,
                    0,
                    {"model": "test"},
                )
        elapsed = time.monotonic() - started
        self.assertEqual("timeout", raised.exception.category)
        self.assertTrue(raised.exception.retryable)
        self.assertLess(elapsed, 0.30)

    def test_fast_response_still_decodes_normally(self) -> None:
        with patch(
            "openrouter_api.urllib.request.urlopen",
            return_value=_Response({"ok": True}),
        ):
            value = request_json(
                "https://example.invalid/models",
                "key",
                1.0,
                0,
            )
        self.assertEqual({"ok": True}, value)


if __name__ == "__main__":
    unittest.main()
''',
        encoding="utf-8",
    )

    recovery_test = ROOT / "tests" / "test_v5_reasoning_saturation_recovery.py"
    recovery_test.write_text(
        '''from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import SelectedNode  # noqa: E402
from v5_runtime import (  # noqa: E402
    BudgetController,
    ExecutionEngine,
    FailureCategory,
    OutputPolicy,
    PromptPolicy,
    RecoveryPolicy,
    RetryPolicy,
    RuntimeConfig,
)


class _Quality:
    def evaluate(self, _node, _response, answer):
        return (bool(answer.strip()), 0.95, [])


def selected_node() -> SelectedNode:
    return SelectedNode(
        node_id="node-visible-recovery",
        assigned_work=("work-a",),
        professional_capabilities={"analysis": 0.8},
        functions=("quantitative_modeling",),
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "high"},
        parameter_profile={
            "supported_parameters": ["reasoning", "max_tokens"],
            "recommended_output_allowance_tokens": 4096,
            "model_company": "qwen",
            "dynamic_parameter_decisions": {"reasoning_effort": "high"},
        },
        model="qwen/test",
        provider_endpoint="qwen/test@provider-a",
        output_contract={
            "required_fields": ["conclusions"],
            "exact_markdown_headings": ["conclusions"],
            "machine_readable_required": False,
        },
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.05,
        request_config={
            "reasoning": {"effort": "high", "exclude": True},
            "provider": {
                "order": ["provider-a"],
                "only": ["provider-a"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
        },
    )


def recovery_row() -> dict:
    node = selected_node()
    return {
        **node.to_dict(),
        "candidate_id": "recovery-google",
        "model": "google/test",
        "provider_endpoint": "google/test@provider-b",
        "parameter_profile": {
            **dict(node.parameter_profile),
            "model_company": "google",
        },
        "request_config": {
            "reasoning": {"effort": "high", "exclude": True},
            "provider": {
                "order": ["provider-b"],
                "only": ["provider-b"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
        },
    }


class V5ReasoningSaturationRecoveryTests(unittest.TestCase):
    def test_replacement_for_reasoning_saturated_empty_output_is_visible_only(self) -> None:
        node = selected_node()
        config = RuntimeConfig(2, 1, 0.35, "value")
        graph = SimpleNamespace(nodes=[node], final_nodes=[])
        budget = BudgetController(config, graph)
        requests: list[dict] = []

        def call_fn(_run, payload):
            requests.append(dict(payload))
            if len(requests) == 1:
                requested = payload["reasoning"]["max_tokens"]
                return ({
                    "id": "empty-reasoning",
                    "model": "qwen/test",
                    "provider": "provider-a",
                    "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                    "usage": {
                        "completion_tokens": requested + 100,
                        "completion_tokens_details": {
                            "reasoning_tokens": requested + 100,
                        },
                        "cost": 0.001,
                    },
                }, 0.1)
            self.assertNotIn("reasoning", payload)
            return ({
                "id": "visible-answer",
                "model": "google/test",
                "provider": "provider-b",
                "choices": [{
                    "message": {"content": "## conclusions\\n\\n结论：保持安全隔离。"},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "completion_tokens": 64,
                    "completion_tokens_details": {"reasoning_tokens": 0},
                    "cost": 0.001,
                },
            }, 0.1)

        engine = ExecutionEngine(
            config,
            prompt_policy=PromptPolicy(),
            retry_policy=RetryPolicy(
                retry_same_endpoint_categories=(
                    FailureCategory.PROVIDER_RATE_LIMITED,
                    FailureCategory.PROVIDER_TIMEOUT,
                )
            ),
            recovery_policy=RecoveryPolicy(),
            quality_policy=_Quality(),
            output_policy=OutputPolicy(),
        )
        result = engine.execute_node(
            node,
            "仅依据题面给出结论。",
            [],
            SimpleNamespace(),
            call_fn,
            [recovery_row()],
            budget,
        )
        self.assertEqual("success_recovered", result.status)
        self.assertEqual(2, len(result.attempts))
        self.assertIn("reasoning", requests[0])
        self.assertNotIn("reasoning", requests[1])
        self.assertIn(
            "reasoning-saturated-empty-output",
            result.attempts[0].gate_reasons,
        )
        adaptation = result.attempts[1].answer_transformations[-1]
        self.assertEqual("recovery-request-adaptation", adaptation["type"])
        self.assertTrue(adaptation["reasoning_removed"])
        self.assertFalse(adaptation["substantive_prompt_changed"])

    def test_ordinary_empty_output_does_not_disable_reasoning_without_evidence(self) -> None:
        usage = {
            "completion_tokens": 10,
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
        evidence = ExecutionEngine._reasoning_saturation_evidence(
            usage,
            {"reasoning": {"max_tokens": 1000}},
        )
        self.assertFalse(evidence["reasoning_saturated_empty_output"])


if __name__ == "__main__":
    unittest.main()
''',
        encoding="utf-8",
    )


def main() -> None:
    patch_openrouter_deadline()
    patch_runtime_recovery()
    write_tests()


if __name__ == "__main__":
    main()
