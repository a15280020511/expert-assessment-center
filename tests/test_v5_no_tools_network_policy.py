from __future__ import annotations

import importlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from execution_graph import SelectedNode  # noqa: E402
from v5_no_tools_policy import (  # noqa: E402
    NoToolsPolicyViolation,
    assert_allowed_control_plane_url,
    assert_allowed_model_plane_url,
    assert_request_has_no_tools,
    assert_response_has_no_tools,
    forbidden_model_route,
)
from v5_runtime import OpenRouterRun  # noqa: E402


class NoToolsNetworkPolicyTests(unittest.TestCase):
    def test_recorded_and_message_tool_fields_are_rejected(self) -> None:
        for payload in (
            {"request_fields": ["tools"]},
            {"messages": [{"role": "assistant", "tool_calls": [{}]}]},
            {"messages": [{"role": "tool", "content": "x"}]},
            {"provider": {"tools": []}},
            {"extra_body": {"web_search": {}}},
        ):
            with self.assertRaises(NoToolsPolicyViolation):
                assert_request_has_no_tools(payload)

    def test_compatibility_tool_fields_are_all_rejected(self) -> None:
        fields = (
            "tools",
            "tool_choice",
            "functions",
            "function_call",
            "parallel_tool_calls",
            "tool_resources",
            "tool_config",
            "built_in_tools",
            "builtin_tools",
            "plugins",
            "web_search",
            "web_search_options",
            "web_search_preview",
            "search_parameters",
            "file_search",
            "browser",
            "code_interpreter",
            "computer",
            "computer_use",
            "mcp",
            "mcp_servers",
            "connectors",
            "external_tools",
        )
        for field in fields:
            with self.subTest(field=field):
                with self.assertRaises(NoToolsPolicyViolation):
                    assert_request_has_no_tools({field: {}})

    def test_response_side_tool_and_network_evidence_is_rejected(self) -> None:
        responses = (
            {"tool_calls": [{"id": "x"}]},
            {"function_call": {"name": "x"}},
            {"web_search_results": [{"url": "https://example.com"}]},
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {"content": "", "tool_calls": [{}]},
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": [{"type": "web_search_call"}],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": "answer",
                            "citations": [{"url": "https://example.com"}],
                        }
                    }
                ]
            },
        )
        for response in responses:
            with self.assertRaises(NoToolsPolicyViolation):
                assert_response_has_no_tools(response)

    def test_router_and_online_models_are_rejected(self) -> None:
        for model in (
            "openrouter/auto",
            "openrouter/free",
            "vendor/model:online",
            "vendor/model:batch",
        ):
            self.assertEqual(model, forbidden_model_route({"model": model}))
            with self.assertRaises(NoToolsPolicyViolation):
                assert_request_has_no_tools({"model": model})
        self.assertEqual("", forbidden_model_route({"model": "vendor/model"}))

    def test_network_egress_is_exactly_allowlisted(self) -> None:
        assert_allowed_model_plane_url("https://openrouter.ai/api/v1/chat/completions")
        assert_allowed_control_plane_url("https://api.github.com/repos/a/b/issues/1")
        for url in (
            "http://openrouter.ai/api/v1/chat/completions",
            "https://openrouter.ai.evil.example/api/v1/chat/completions",
            "https://openrouter.ai:444/api/v1/chat/completions",
            "https://user:pass@openrouter.ai/api/v1/chat/completions",
            "https://openrouter.ai/not-api/chat/completions",
        ):
            with self.assertRaises(NoToolsPolicyViolation):
                assert_allowed_model_plane_url(url)
        for url in (
            "https://github.com/a/b",
            "https://api.github.com/users/a",
            "https://api.github.com.evil.example/repos/a/b",
        ):
            with self.assertRaises(NoToolsPolicyViolation):
                assert_allowed_control_plane_url(url)

    def test_tool_violation_is_non_retryable_internal_contract_failure(self) -> None:
        violation = NoToolsPolicyViolation(
            "no tools",
            category="tool_invocation_forbidden",
            phase="request",
        )
        self.assertFalse(violation.retryable)
        self.assertFalse(violation.request_sent)
        self.assertFalse(violation.response_received)
        self.assertEqual(
            "tool_invocation_forbidden",
            violation.response_diagnostics["category"],
        )

    def test_openrouter_boundary_rejects_tools_before_transport(self) -> None:
        runtime = importlib.import_module("v5_runtime")
        run = OpenRouterRun(
            api_url="https://openrouter.ai/api/v1/chat/completions",
            api_key="key",
            request_timeout_seconds=10,
        )
        with mock.patch.object(runtime, "request_json") as transport:
            with self.assertRaises(NoToolsPolicyViolation):
                runtime._call_openrouter(
                    run,
                    {"model": "vendor/model", "tools": []},
                )
        transport.assert_not_called()

    def test_openrouter_boundary_rejects_tool_response(self) -> None:
        runtime = importlib.import_module("v5_runtime")
        run = OpenRouterRun(
            api_url="https://openrouter.ai/api/v1/chat/completions",
            api_key="key",
            request_timeout_seconds=10,
        )
        response = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [{}]},
                }
            ]
        }
        with mock.patch.object(runtime, "request_json", return_value=response):
            with self.assertRaises(NoToolsPolicyViolation):
                runtime._call_openrouter(
                    run,
                    {"model": "vendor/model", "messages": []},
                )

    def test_free_qualification_workflow_is_not_an_active_gate(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "v5-zero-cost-free-canary.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("paid_execution_requires_prior_qualification: true", workflow)
        self.assertNotIn("exact_sha_evidence_required: true", workflow)

    def test_repository_has_one_policy_and_no_unapproved_network_clients(self) -> None:
        policy_modules = sorted(MARKET.glob("*no_tools*policy*.py"))
        self.assertEqual(
            ["v5_no_tools_policy.py"],
            [path.name for path in policy_modules],
        )
        approved_transport_modules = {
            "v5_governance_runtime.py",
            "v5_runtime.py",
            "openrouter_api.py",
        }
        for path in MARKET.glob("*.py"):
            if path.name in approved_transport_modules:
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("urllib.request.urlopen", text, path.name)
            self.assertNotIn("requests.get(", text, path.name)
            self.assertNotIn("requests.post(", text, path.name)

    def test_long_tasks_are_not_rejected_by_local_character_gate(self) -> None:
        dynamic = importlib.import_module("v5_dynamic_pipeline")
        source = (MARKET / "v5_dynamic_pipeline.py").read_text(encoding="utf-8")
        self.assertNotIn("MAX_TASK_CHARS", source)
        self.assertNotIn("MAX_TASK_CHARS", (MARKET / "model_market.py").read_text())
        self.assertTrue(dynamic.ROUTED_BATCH_BUSINESS_GATE_DISABLED)

    def test_machine_constitution_locks_network_and_response_boundaries(
        self,
    ) -> None:
        policy = json.loads(
            (MARKET / "constitutional_policy.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            "v5-constitutional-policy-12-parameter-design-closure",
            policy["schema_version"],
        )
        self.assertEqual("CONSTITUTION.md", policy["authority"])
        self.assertEqual("no-tools", policy["only_hard_model_boundary"])
        self.assertTrue(policy["dynamic_task_matching"]["parameter_design_required"])
        self.assertTrue(
            policy["dynamic_task_matching"][
                "constitutional_invariants_must_not_be_disguised_as_dynamic"
            ]
        )
        tools = policy["tool_policy"]
        self.assertTrue(tools["request_tool_fields_forbidden"])
        self.assertTrue(tools["response_tool_evidence_forbidden"])
        self.assertFalse(tools["expert_external_tools_allowed"])
        integrity = policy["security_and_integrity_invariants"]
        self.assertFalse(integrity["arbitrary_network_egress_allowed"])
        self.assertEqual(
            ["openrouter.ai"],
            integrity["model_plane_hosts"],
        )
        self.assertEqual(
            ["api.github.com"],
            integrity["control_plane_hosts"],
        )


if __name__ == "__main__":
    unittest.main()
