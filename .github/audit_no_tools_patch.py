from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
TESTS = ROOT / "tests"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def replace_once(path: Path, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one exact match, found {count}: {old[:80]!r}")
    write(path, text.replace(old, new, 1))


def regex_once(path: Path, pattern: str, replacement: str) -> None:
    text = read(path)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{path}: expected one regex match, found {count}: {pattern[:100]!r}")
    write(path, updated)


NO_TOOLS_POLICY = '''"""Single constitutional boundary for model tools and runtime network egress."""
from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse

FORBIDDEN_REQUEST_FIELDS = frozenset(
    {
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
        "models",
    }
)
FORBIDDEN_MESSAGE_FIELDS = frozenset(
    {"tool_calls", "function_call", "tool_call_id"}
)
FORBIDDEN_MESSAGE_ROLES = frozenset({"tool", "function"})
FORBIDDEN_RESPONSE_FIELDS = frozenset(
    {
        "tool_calls",
        "function_call",
        "web_search_results",
        "file_search_results",
        "computer_calls",
        "mcp_calls",
    }
)
FORBIDDEN_CONTENT_PART_TYPES = frozenset(
    {
        "tool_call",
        "tool_use",
        "function_call",
        "web_search_call",
        "file_search_call",
        "code_interpreter_call",
        "computer_call",
        "computer_use",
        "mcp_call",
    }
)
FORBIDDEN_FINISH_REASONS = frozenset({"tool_calls", "function_call"})
MODEL_PLANE_HOSTS = frozenset({"openrouter.ai"})
CONTROL_PLANE_HOSTS = frozenset({"api.github.com"})


class NoToolsPolicyViolation(RuntimeError):
    """Fail-closed constitutional violation with runtime recovery metadata."""

    def __init__(self, message: str, *, category: str, phase: str) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = False
        self.request_sent = phase == "response"
        self.response_received = phase == "response"
        self.response_diagnostics = {
            "schema_version": "v5-no-tools-violation-1",
            "phase": phase,
            "category": category,
        }


def _casefold_keys(value: Mapping[str, Any]) -> dict[str, str]:
    return {str(key).casefold(): str(key) for key in value}


def forbidden_request_fields(request: Mapping[str, Any]) -> set[str]:
    """Return actionable tool-capability fields without scanning JSON schemas."""
    found: set[str] = set()
    keys = _casefold_keys(request)
    found.update(keys[key] for key in FORBIDDEN_REQUEST_FIELDS.intersection(keys))

    recorded = request.get("request_fields")
    if isinstance(recorded, list):
        for value in recorded:
            folded = str(value).casefold()
            if folded in FORBIDDEN_REQUEST_FIELDS:
                found.add(str(value))

    for container_name in ("provider", "extra_body"):
        container = request.get(container_name)
        if isinstance(container, Mapping):
            nested = _casefold_keys(container)
            found.update(
                f"{container_name}.{nested[key]}"
                for key in FORBIDDEN_REQUEST_FIELDS.intersection(nested)
            )

    messages = request.get("messages")
    if isinstance(messages, list):
        for index, message in enumerate(messages):
            if not isinstance(message, Mapping):
                continue
            role = str(message.get("role") or "").casefold()
            if role in FORBIDDEN_MESSAGE_ROLES:
                found.add(f"messages[{index}].role={role}")
            message_keys = _casefold_keys(message)
            found.update(
                f"messages[{index}].{message_keys[key]}"
                for key in FORBIDDEN_MESSAGE_FIELDS.intersection(message_keys)
            )
    return found


def forbidden_model_route(request: Mapping[str, Any]) -> str:
    model = str(request.get("model") or "").strip()
    folded = model.casefold()
    if folded.startswith("openrouter/"):
        return model
    if any(marker in folded for marker in (":online", ":batch")):
        return model
    return ""


def assert_request_has_no_tools(
    request: Mapping[str, Any],
    *,
    context: str = "model request",
) -> None:
    fields = sorted(forbidden_request_fields(request))
    route = forbidden_model_route(request)
    if fields or route:
        details = []
        if fields:
            details.append("fields=" + ",".join(fields))
        if route:
            details.append("model_route=" + route)
        raise NoToolsPolicyViolation(
            f"{context} exposes forbidden tool or network capability: "
            + "; ".join(details),
            category="tool_invocation_forbidden",
            phase="request",
        )


def _content_part_evidence(content: Any, prefix: str) -> list[str]:
    if not isinstance(content, list):
        return []
    evidence: list[str] = []
    for index, part in enumerate(content):
        if not isinstance(part, Mapping):
            continue
        kind = str(part.get("type") or "").casefold()
        if kind in FORBIDDEN_CONTENT_PART_TYPES:
            evidence.append(f"{prefix}.content[{index}].type={kind}")
    return evidence


def response_tool_evidence(response: Mapping[str, Any]) -> list[str]:
    evidence: list[str] = []
    top = _casefold_keys(response)
    for key in FORBIDDEN_RESPONSE_FIELDS.intersection(top):
        value = response.get(top[key])
        if value not in (None, "", [], {}):
            evidence.append(top[key])

    choices = response.get("choices")
    if not isinstance(choices, list):
        return evidence
    for index, choice in enumerate(choices):
        if not isinstance(choice, Mapping):
            continue
        finish = str(choice.get("finish_reason") or "").casefold()
        if finish in FORBIDDEN_FINISH_REASONS:
            evidence.append(f"choices[{index}].finish_reason={finish}")
        for source_name in ("message", "delta"):
            source = choice.get(source_name)
            if not isinstance(source, Mapping):
                continue
            role = str(source.get("role") or "").casefold()
            if role in FORBIDDEN_MESSAGE_ROLES:
                evidence.append(f"choices[{index}].{source_name}.role={role}")
            source_keys = _casefold_keys(source)
            for key in FORBIDDEN_RESPONSE_FIELDS.intersection(source_keys):
                value = source.get(source_keys[key])
                if value not in (None, "", [], {}):
                    evidence.append(
                        f"choices[{index}].{source_name}.{source_keys[key]}"
                    )
            for citation_field in ("annotations", "citations"):
                value = source.get(citation_field)
                if value not in (None, "", [], {}):
                    evidence.append(
                        f"choices[{index}].{source_name}.{citation_field}"
                    )
            evidence.extend(
                _content_part_evidence(
                    source.get("content"),
                    f"choices[{index}].{source_name}",
                )
            )
    return evidence


def assert_response_has_no_tools(
    response: Mapping[str, Any],
    *,
    context: str = "model response",
) -> None:
    evidence = response_tool_evidence(response)
    if evidence:
        raise NoToolsPolicyViolation(
            f"{context} contains forbidden tool or network evidence: "
            + ",".join(evidence),
            category="tool_invocation_forbidden",
            phase="response",
        )


def _assert_https_allowlist(
    url: str,
    *,
    hosts: frozenset[str],
    path_prefix: str,
    context: str,
) -> None:
    parsed = urlparse(str(url))
    host = str(parsed.hostname or "").casefold()
    try:
        port = parsed.port
    except ValueError as exc:
        raise NoToolsPolicyViolation(
            f"{context} URL has an invalid port",
            category="network_egress_forbidden",
            phase="request",
        ) from exc
    valid = (
        parsed.scheme.casefold() == "https"
        and host in hosts
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and parsed.path.startswith(path_prefix)
    )
    if not valid:
        raise NoToolsPolicyViolation(
            f"{context} URL is outside the constitutional allowlist: {url}",
            category="network_egress_forbidden",
            phase="request",
        )


def assert_allowed_model_plane_url(url: str) -> None:
    _assert_https_allowlist(
        url,
        hosts=MODEL_PLANE_HOSTS,
        path_prefix="/api/v1/",
        context="model-plane egress",
    )


def assert_allowed_control_plane_url(url: str) -> None:
    _assert_https_allowlist(
        url,
        hosts=CONTROL_PLANE_HOSTS,
        path_prefix="/repos/",
        context="control-plane egress",
    )
'''
write(MARKET / "v5_no_tools_policy.py", NO_TOOLS_POLICY)

# OpenRouter is the single model-plane network boundary.
path = MARKET / "openrouter_api.py"
replace_once(
    path,
    "from typing import Any, Dict, Mapping, Optional\n",
    "from typing import Any, Dict, Mapping, Optional\n\n"
    "from v5_no_tools_policy import (\n"
    "    assert_allowed_model_plane_url,\n"
    "    assert_request_has_no_tools,\n"
    "    assert_response_has_no_tools,\n"
    ")\n",
)
replace_once(
    path,
    ") -> Dict[str, Any]:\n    data = json.dumps(payload, ensure_ascii=False).encode(\"utf-8\") if payload is not None else None\n",
    ") -> Dict[str, Any]:\n"
    "    assert_allowed_model_plane_url(url)\n"
    "    if payload is not None and url == CHAT_URL:\n"
    "        assert_request_has_no_tools(payload, context=\"OpenRouter chat request\")\n"
    "    data = json.dumps(payload, ensure_ascii=False).encode(\"utf-8\") if payload is not None else None\n",
)
replace_once(
    path,
    "            return _request_with_hard_deadline(\n                request,\n                url,\n                timeout_seconds,\n            )\n",
    "            response = _request_with_hard_deadline(\n"
    "                request,\n"
    "                url,\n"
    "                timeout_seconds,\n"
    "            )\n"
    "            if url == CHAT_URL:\n"
    "                assert_response_has_no_tools(\n"
    "                    response, context=\"OpenRouter chat response\"\n"
    "                )\n"
    "            return response\n",
)

# Remove the remaining local hard task-character rejection.
path = MARKET / "model_market.py"
replace_once(path, "MAX_TASK_CHARS = 50_000\n", "")
replace_once(
    path,
    "    if len(task) > MAX_TASK_CHARS:\n        raise ExpertTeamError(f\"Task exceeds {MAX_TASK_CHARS} characters\")\n\n",
    "",
)

# Expert request constructor delegates to the single policy.
path = MARKET / "v5_execution_primitives.py"
replace_once(
    path,
    "from execution_graph import SelectedNode\n",
    "from execution_graph import SelectedNode\n"
    "from v5_no_tools_policy import assert_request_has_no_tools\n",
)
regex_once(
    path,
    r"FORBIDDEN_FIELDS = \{.*?\}\nPROMPT_MODULES",
    "PROMPT_MODULES",
)
regex_once(
    path,
    r"    forbidden = sorted\(FORBIDDEN_FIELDS\.intersection\(payload\)\).*?"
    r"    if \"max_tokens\" in payload or \"max_completion_tokens\" in payload:",
    "    assert_request_has_no_tools(\n"
    "        payload, context=f\"expert node {node.node_id} request\"\n"
    "    )\n"
    "    if \"max_tokens\" in payload or \"max_completion_tokens\" in payload:",
)

# Native runtime: one request rule, one response rule, no duplicate set.
path = MARKET / "v5_runtime.py"
replace_once(
    path,
    "from v5_json_io import write_json\n",
    "from v5_json_io import write_json\n"
    "from v5_no_tools_policy import (\n"
    "    assert_request_has_no_tools,\n"
    "    assert_response_has_no_tools,\n"
    "    forbidden_request_fields,\n"
    ")\n",
)
regex_once(
    path,
    r"FORBIDDEN_REQUEST_FIELDS = \{.*?\}\n\n\nclass FailureCategory",
    "class FailureCategory",
)
regex_once(
    path,
    r"        forbidden = sorted\(FORBIDDEN_REQUEST_FIELDS\.intersection\(payload\)\).*?"
    r"        return payload",
    "        assert_request_has_no_tools(\n"
    "            payload, context=f\"expert node {node.node_id} request\"\n"
    "        )\n"
    "        return payload",
)
replace_once(
    path,
    "        if category_name in {\"rate_limited\", FailureCategory.PROVIDER_RATE_LIMITED.value} or status == 429:\n",
    "        if category_name in {\"tool_invocation_forbidden\", \"network_egress_forbidden\"}:\n"
    "            category = FailureCategory.INTERNAL_CONTRACT_VIOLATION\n"
    "            retryable = False\n"
    "        elif category_name in {\"rate_limited\", FailureCategory.PROVIDER_RATE_LIMITED.value} or status == 429:\n",
)
replace_once(
    path,
    "            response, latency = call_fn(run, payload)\n            answer = cost_hardening.robust_extract_answer(response)\n",
    "            response, latency = call_fn(run, payload)\n"
    "            assert_response_has_no_tools(\n"
    "                response, context=f\"expert node {node.node_id} response\"\n"
    "            )\n"
    "            answer = cost_hardening.robust_extract_answer(response)\n",
)
replace_once(
    path,
    "                    not FORBIDDEN_REQUEST_FIELDS.intersection(request)\n",
    "                    not forbidden_request_fields(request)\n",
)

# Constitutional prompt layer delegates to the same boundary.
path = MARKET / "v5_constitutional_runtime.py"
replace_once(
    path,
    "from v5_model_company import canonical_model_company\n",
    "from v5_model_company import canonical_model_company\n"
    "from v5_no_tools_policy import assert_request_has_no_tools\n",
)
regex_once(
    path,
    r"FORBIDDEN_REQUEST_FIELDS = \{.*?\}\n\n\ndef validate_scope_boundaries",
    "def validate_scope_boundaries",
)
regex_once(
    path,
    r"        forbidden = sorted\(FORBIDDEN_REQUEST_FIELDS\.intersection\(payload\)\).*?"
    r"        return payload",
    "        assert_request_has_no_tools(\n"
    "            payload, context=f\"constitutional node {node.node_id} request\"\n"
    "        )\n"
    "        return payload",
)

# Complete request audit delegates to the same policy.
path = MARKET / "v5_pipeline.py"
replace_once(
    path,
    "from v5_provider_lock import canonical_provider_lock\n",
    "from v5_provider_lock import canonical_provider_lock\n"
    "from v5_no_tools_policy import (\n"
    "    forbidden_request_fields as policy_forbidden_request_fields,\n"
    ")\n",
)
regex_once(
    path,
    r"_FORBIDDEN_REQUEST_FIELDS = frozenset\(.*?\n\)\n\n\ndef _forbidden_request_fields\(.*?\n    return direct\n",
    "def _forbidden_request_fields(row: Mapping[str, Any]) -> set[str]:\n"
    "    return set(policy_forbidden_request_fields(row))\n",
)

# Independent artifact revalidation uses the same exhaustive field set.
path = MARKET / "v5_independent_artifact_revalidation.py"
replace_once(
    path,
    "from v5_model_company import canonical_model_company\n",
    "from v5_model_company import canonical_model_company\n"
    "from v5_no_tools_policy import forbidden_request_fields\n",
)
regex_once(
    path,
    r"FORBIDDEN_REQUEST_FIELDS = \{.*?\}\n\n\ndef _load",
    "def _load",
)
text = read(path)
text = re.sub(
    r"FORBIDDEN_REQUEST_FIELDS\.intersection\(([^\n\)]+)\)",
    r"forbidden_request_fields(\1)",
    text,
)
if "FORBIDDEN_REQUEST_FIELDS" in text:
    raise RuntimeError("independent revalidation still contains a local forbidden set")
write(path, text)

# Governance custom-call paths cannot bypass request or response checks.
path = MARKET / "v5_governance_runtime.py"
replace_once(
    path,
    "from v5_json_io import write_json\n" if "from v5_json_io import write_json\n" in read(path) else "from v5_structured_output_compat import normalize_strict_response_format\n",
    ("from v5_json_io import write_json\n" if "from v5_json_io import write_json\n" in read(path) else "from v5_structured_output_compat import normalize_strict_response_format\n")
    + "from v5_no_tools_policy import (\n"
    "    assert_request_has_no_tools,\n"
    "    assert_response_has_no_tools,\n"
    ")\n",
)
replace_once(
    path,
    "    api_request = _api_payload(request)\n    response, latency = call_fn(run, api_request)\n    text = extract_answer(response)\n",
    "    api_request = _api_payload(request)\n"
    "    try:\n"
    "        assert_request_has_no_tools(\n"
    "            api_request, context=f\"governance {kind} request\"\n"
    "        )\n"
    "        response, latency = call_fn(run, api_request)\n"
    "        assert_response_has_no_tools(\n"
    "            response, context=f\"governance {kind} response\"\n"
    "        )\n"
    "    except Exception as exc:\n"
    "        ledger.mark_failure(kind=kind, error=exc, visible_output=\"\")\n"
    "        _persist_governance_ledger(artifact_root, ledger, status=\"FAIL\")\n"
    "        raise\n"
    "    text = extract_answer(response)\n",
)

# GitHub control-plane HTTP helpers are explicitly host/path allowlisted.
for filename in ("v5_admission_lock.py", "v5_issue_ticket.py"):
    path = MARKET / filename
    replace_once(
        path,
        "import urllib.request\n",
        "import urllib.request\n\n"
        "from v5_no_tools_policy import assert_allowed_control_plane_url\n",
    )
    replace_once(
        path,
        "def _api_json(url: str) -> Any:\n",
        "def _api_json(url: str) -> Any:\n"
        "    assert_allowed_control_plane_url(url)\n",
    )

# Machine-readable constitution records both request and response boundaries.
policy_path = MARKET / "constitutional_policy.json"
policy = json.loads(read(policy_path))
policy["schema_version"] = "v5-constitutional-policy-4"
tool = dict(policy["tool_prohibition"])
tool.update(
    {
        "request_and_response_boundary_required": True,
        "response_tool_calls_allowed": False,
        "online_or_router_models_allowed": False,
        "arbitrary_network_egress_allowed": False,
        "model_plane_network_allowlist": ["openrouter.ai"],
        "control_plane_network_allowlist": ["api.github.com"],
        "network_violation_action": "fail_closed_without_recovery",
    }
)
policy["tool_prohibition"] = tool
policy["network_isolation"] = {
    "arbitrary_egress_allowed": False,
    "model_plane_hosts": ["openrouter.ai"],
    "control_plane_hosts": ["api.github.com"],
    "model_access_to_network_allowed": False,
    "data_collection_or_web_search_allowed": False,
    "url_allowlist_enforced_before_request": True,
}
write(policy_path, json.dumps(policy, ensure_ascii=False, indent=2) + "\n")

# Existing policy test follows the schema version.
constitution_test = TESTS / "test_v5_constitution_policy.py"
text = read(constitution_test).replace(
    '"v5-constitutional-policy-3"', '"v5-constitutional-policy-4"'
)
write(constitution_test, text)

NO_TOOLS_TEST = '''from __future__ import annotations

import argparse
import json
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

    def test_router_and_online_models_are_rejected(self) -> None:
        for model in ("openrouter/free", "vendor/model:online", "vendor/model:batch"):
            with self.subTest(model=model):
                with self.assertRaises(NoToolsPolicyViolation):
                    assert_request_has_no_tools({"model": model, "messages": []})
        assert_request_has_no_tools({"model": "vendor/exact-model", "messages": []})

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
            {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
        )

    def test_network_egress_is_exactly_allowlisted(self) -> None:
        assert_allowed_model_plane_url(
            "https://openrouter.ai/api/v1/chat/completions"
        )
        assert_allowed_control_plane_url(
            "https://api.github.com/repos/a/b/issues?state=all"
        )
        for fn, url in (
            (assert_allowed_model_plane_url, "https://example.com/api/v1/models"),
            (assert_allowed_model_plane_url, "http://openrouter.ai/api/v1/models"),
            (assert_allowed_model_plane_url, "https://openrouter.ai.evil.test/api/v1/models"),
            (assert_allowed_control_plane_url, "https://api.github.com/user"),
            (assert_allowed_control_plane_url, "https://github.com/repos/a/b"),
        ):
            with self.subTest(url=url):
                with self.assertRaises(NoToolsPolicyViolation):
                    fn(url)

    def test_openrouter_boundary_rejects_tools_before_transport(self) -> None:
        with mock.patch.object(openrouter_api, "_request_with_hard_deadline") as transport:
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

    def test_tool_violation_is_non_retryable_internal_contract_failure(self) -> None:
        violation = NoToolsPolicyViolation(
            "tool call",
            category="tool_invocation_forbidden",
            phase="response",
        )
        failure = ExecutionEngine._failure_from_exception(
            violation,
            mock.Mock(model="vendor/model", provider_endpoint="vendor/model@provider"),
        )
        self.assertEqual(FailureCategory.INTERNAL_CONTRACT_VIOLATION, failure.category)
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

    def test_repository_has_one_policy_and_no_unapproved_network_clients(self) -> None:
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
                r"^FORBIDDEN_(?:REQUEST_)?FIELDS\s*=", text, re.M
            ):
                duplicate_definitions.append(path.name)
            if "urllib.request.urlopen" in text and path.name not in allowed_urlopen:
                unapproved_urlopen.append(path.name)
            if any(fragment in text for fragment in blocked_import_fragments):
                disallowed_imports.append(path.name)
        self.assertEqual([], duplicate_definitions)
        self.assertEqual([], unapproved_urlopen)
        self.assertEqual([], disallowed_imports)
        self.assertNotIn("MAX_TASK_CHARS", (MARKET / "model_market.py").read_text())
        for filename in (
            "openrouter_api.py",
            "v5_runtime.py",
            "v5_governance_runtime.py",
        ):
            text = (MARKET / filename).read_text(encoding="utf-8")
            self.assertIn("assert_request_has_no_tools", text)
            self.assertIn("assert_response_has_no_tools", text)

    def test_machine_constitution_locks_network_and_response_boundaries(self) -> None:
        policy = json.loads(
            (MARKET / "constitutional_policy.json").read_text(encoding="utf-8")
        )
        self.assertEqual("v5-constitutional-policy-4", policy["schema_version"])
        tool = policy["tool_prohibition"]
        self.assertTrue(tool["request_and_response_boundary_required"])
        self.assertFalse(tool["response_tool_calls_allowed"])
        self.assertFalse(tool["arbitrary_network_egress_allowed"])
        self.assertEqual(["openrouter.ai"], tool["model_plane_network_allowlist"])
        self.assertEqual(["api.github.com"], tool["control_plane_network_allowlist"])


if __name__ == "__main__":
    unittest.main()
'''
write(TESTS / "test_v5_no_tools_network_policy.py", NO_TOOLS_TEST)

AUDIT_DOC = '''# 禁止工具与网络隔离颗粒化审计（2026-08-04）

## 范围

对最高宪法、机器策略、GPT/Claude 治理请求、专家请求、恢复请求、OpenRouter HTTP 边界、GitHub 控制平面、原生审计、独立 Artifact 复算及回归测试进行逐层审计。

## 发现并修复

1. 工具字段黑名单在多个模块重复维护，字段集合已经漂移；统一为 `v5_no_tools_policy.py` 单一权威实现。
2. 原规则缺少 `functions`、`function_call`、`parallel_tool_calls`、`tool_resources`、MCP 与 connector 等兼容字段；全部补齐。
3. 原执行链只审计请求，不统一拒绝响应中的 `tool_calls`、函数调用、搜索调用、工具内容块或 URL citation；新增请求前与响应后的双向失败关闭。
4. 自定义 `call_fn` 可绕过 OpenRouter 默认调用边界；治理链和专家运行时均新增本地双向校验。
5. `request_json` 可接受任意 URL；现在模型平面只允许 HTTPS `openrouter.ai/api/v1/*`。
6. GitHub 串行准入与 Issue 状态读取函数可接受任意 URL；现在控制平面只允许 HTTPS `api.github.com/repos/*`。
7. 在线路由、router 模型和 batch 路由可能绕过无网络原则；在统一策略中失败关闭。
8. `model_market.py` 仍以 50,000 字符拒绝任务，构成本地资源硬门；已删除，改由真实端点上下文容量决定兼容性。
9. 原生请求审计与独立复算使用不完整的各自字段集合；统一引用同一策略。
10. 缺少覆盖响应工具调用、URL 出口、兼容字段和长任务的防回退测试；新增专项测试与全仓静态残留检查。

## 保留的必要网络

- 模型平面：仅 OpenRouter API，用于读取模型目录、端点和调用已选模型。
- 控制平面：仅 GitHub API，用于单任务串行准入、Issue 状态、Actions 与 Artifact 治理。
- 模型自身不得浏览网页、搜索、调用 MCP/插件/API/数据库/文件或访问其他中心。

## 判定

修复后必须以零费用 CI、完整单元测试、全仓逐行审计和免费 Canary 为准。未完成正式付费端到端验收前，不移动 `production`。
'''
write(ROOT / "docs" / "no-tools-network-granular-audit-20260804.md", AUDIT_DOC)

# Temporary applier files must not enter the final PR.
(ROOT / ".github" / "audit_no_tools_patch.py").unlink()
(ROOT / ".github" / "workflows" / "apply-no-tools-audit-patch.yml").unlink()
