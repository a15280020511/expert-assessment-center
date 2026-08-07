from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

import model_market  # noqa: E402
import openrouter_api  # noqa: E402
from v5_no_tools_policy import (  # noqa: E402
    FORBIDDEN_REQUEST_FIELDS,
    NoToolsPolicyViolation,
    assert_allowed_control_plane_url,
    assert_allowed_model_plane_url,
    assert_request_has_no_tools,
    assert_response_has_no_tools,
    forbidden_request_fields,
)
from v5_runtime import (  # noqa: E402
    ExecutionEngine,
    FailureCategory,
)


class NoToolsNetworkPolicyTests(unittest.TestCase):
    def test_compatibility_tool_fields_are_all_rejected(self) -> None:
        required = {
            "tools",
            "tool_choice",
            "functions",
            "function_call",
            "parallel_tool_calls",
            "tool_resources",
            "plugins",
            "web_search",
            "file_search",
            "code_interpreter",
            "mcp_servers",
            "connectors",
            "models",
        }
        self.assertTrue(required.issubset(FORBIDDEN_REQUEST_FIELDS))
        for field in sorted(required):
            with self.subTest(field=field):
                request = {"model": "vendor/model", field: []}
                with self.assertRaises(NoToolsPolicyViolation):
                    assert_request_has_no_tools(request)

    def test_recorded_and_message_tool_fields_are_rejected(self) -> None:
        for request in (
            {"model": "vendor/model", "request_fields": ["function_call"]},
            {
                "model": "vendor/model",
                "messages": [{"role": "tool", "content": "result"}],
            },
            {
                "model": "vendor/model",
                "messages": [
                    {"role": "assistant", "content": "", "tool_calls": [{}]}
                ],
            },
            {
                "model": "vendor/model",
                "extra_body": {"web_search": {"enabled": True}},
            },
        ):
            with self.subTest(request=request):
                self.assertTrue(forbidden_request_fields(request))
                with self.assertRaises(NoToolsPolicyViolation):
                    assert_request_has_no_tools(request)

    def test_router_and_online_models_are_rejected_but_batch_is_not_a_tool_route(self) -> None:
        for model in (
            "openrouter/free",
            "vendor/model:online",
        ):
            with self.subTest(model=model):
                with self.assertRaises(NoToolsPolicyViolation):
                    assert_request_has_no_tools(
                        {"model": model, "messages": []}
                    )
        # Batch uses a different OpenRouter transport but does not itself grant
        # tools/retrieval. It is filtered by the synchronous transport boundary.
        assert_request_has_no_tools(
            {"model": "vendor/model:batch", "messages": []}
        )
        assert_request_has_no_tools(
            {"model": "vendor/exact-model", "messages": []}
        )

    def test_free_qualification_workflow_is_not_an_active_gate(self) -> None:
        obsolete = (
            ROOT / ".github" / "workflows" / "v5-free-model-qualification.yml"
        )
        self.assertFalse(obsolete.exists())
        active = (
            ROOT / ".github" / "workflows" / "execution-ticket.yml"
        ).read_text(encoding="utf-8").casefold()
        self.assertNotIn("free-first", active)
        self.assertNotIn("free canary", active)
        self.assertNotIn("v5-free-model-qualification", active)

    def test_response_side_tool_and_network_evidence_is_rejected(self) -> None:
        responses = (
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {"content": "text", "tool_calls": [{}]},
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": "text",
                            "function_call": {"name": "lookup"},
                        }
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
                            "content": "text",
                            "annotations": [{"type": "url_citation"}],
                        }
                    }
                ]
            },
        )
        for response in responses:
            with self.subTest(response=response):
                with self.assertRaises(NoToolsPolicyViolation):
                    assert_response_has_no_tools(response)
        assert_response_has_no_tools(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "ok"},
                    }
                ]
            }
        )

    def test_network_egress_is_exactly_allowlisted(self) -> None:
        assert_allowed_model_plane_url(
            "https://openrouter.ai/api/v1/chat/completions"
        )
        assert_allowed_control_plane_url(
            "https://api.github.com/repos/a/b/issues?state=all"
        )
        for fn, url in (
            (
                assert_allowed_model_plane_url,
                "https://example.com/api/v1/models",
            ),
            (
                assert_allowed_model_plane_url,
                "http://openrouter.ai/api/v1/models",
            ),
            (
                assert_allowed_model_plane_url,
                "https://openrouter.ai.evil.test/api/v1/models",
            ),
            (assert_allowed_control_plane_url, "https://api.github.com/user"),
            (
                assert_allowed_control_plane_url,
                "https://github.com/repos/a/b",
            ),
        ):
            with self.subTest(url=url):
                with self.assertRaises(NoToolsPolicyViolation):
                    fn(url)

    def test_openrouter_boundary_rejects_tools_before_transport(self) -> None:
        with mock.patch.object(
            openrouter_api,
            "_request_with_hard_deadline",
        ) as transport:
            with self.assertRaises(NoToolsPolicyViolation):
                openrouter_api.request_json(
                    openrouter_api.CHAT_URL,
                    "key",
                    1,
                    0,
                    {"model": "vendor/model", "functions": []},
                )
            transport.assert_not_called()

    def test_openrouter_boundary_rejects_tool_response(self) -> None:
        response = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {"content": "also text", "tool_calls": [{}]},
                }
            ]
        }
        with mock.patch.object(
            openrouter_api,
            "_request_with_hard_deadline",
            return_value=response,
        ):
            with self.assertRaises(NoToolsPolicyViolation):
                openrouter_api.request_json(
                    openrouter_api.CHAT_URL,
                    "key",
                    1,
                    0,
                    {"model": "vendor/model", "messages": []},
                )

    def test_tool_violation_is_non_retryable_internal_contract_failure(
        self,
    ) -> None:
        violation = NoToolsPolicyViolation(
            "tool call",
            category="tool_invocation_forbidden",
            phase="response",
        )
        failure = ExecutionEngine._failure_from_exception(
            violation,
            mock.Mock(
                model="vendor/model",
                provider_endpoint="vendor/model@provider",
            ),
        )
        self.assertEqual(
            FailureCategory.INTERNAL_CONTRACT_VIOLATION,
            failure.category,
        )
        self.assertFalse(failure.retryable)

    def test_long_tasks_are_not_rejected_by_local_character_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "catalog": {
                            "maximum_models": 150,
                            "minimum_context_length": 16384,
                            "sorts": ["intelligence-high-to-low"],
                        },
                        "execution": {
                            "max_completion_tokens": 10000,
                            "reasoning_effort": "low",
                            "temperature": 0.0,
                            "catalog_timeout_seconds": 30,
                            "catalog_max_retries": 1,
                            "model_timeout_seconds": 240,
                            "maximum_replacements": 1,
                            "parallel_workers": 1,
                        },
                        "provider": {},
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                task="任" * 60000,
                config=str(config),
                output_dir=tmp,
                ranking_limit=150,
                max_completion_tokens=None,
                reasoning_effort=None,
                maximum_recovery_calls=1,
                catalog_file=None,
                dry_run=True,
                require_live_catalog=False,
            )
            run = model_market.build_run_config(args)
            self.assertEqual(60000, len(run.task))

    def test_repository_has_one_policy_and_no_unapproved_network_clients(
        self,
    ) -> None:
        duplicate_definitions = []
        unapproved_urlopen = []
        disallowed_imports = []
        allowed_urlopen = {
            "openrouter_api.py",
            "v5_admission_lock.py",
            "v5_issue_ticket.py",
        }
        blocked_import_fragments = (
            "import requests",
            "import httpx",
            "import aiohttp",
            "import urllib3",
            "import websocket",
            "import selenium",
            "import playwright",
        )
        for path in MARKET.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if path.name != "v5_no_tools_policy.py" and re.search(
                r"^FORBIDDEN_(?:REQUEST_)?FIELDS\s*=",
                text,
                re.M,
            ):
                duplicate_definitions.append(path.name)
            if (
                "urllib.request.urlopen" in text
                and path.name not in allowed_urlopen
            ):
                unapproved_urlopen.append(path.name)
            if any(fragment in text for fragment in blocked_import_fragments):
                disallowed_imports.append(path.name)
        self.assertEqual([], duplicate_definitions)
        self.assertEqual([], unapproved_urlopen)
        self.assertEqual([], disallowed_imports)
        self.assertNotIn(
            "MAX_TASK_CHARS",
            (MARKET / "model_market.py").read_text(),
        )
        for filename in (
            "openrouter_api.py",
            "v5_runtime.py",
            "v5_governance_runtime.py",
        ):
            text = (MARKET / filename).read_text(encoding="utf-8")
            self.assertIn("assert_request_has_no_tools", text)
            self.assertIn("assert_response_has_no_tools", text)

    def test_machine_constitution_locks_network_and_response_boundaries(
        self,
    ) -> None:
        policy = json.loads(
            (MARKET / "constitutional_policy.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            "v5-constitutional-policy-10-fully-dynamic-no-tools",
            policy["schema_version"],
        )
        self.assertEqual("no-tools", policy["only_hard_model_boundary"])
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
