"""Single constitutional boundary for model tools and runtime network egress."""
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
    """Return routes that violate the model no-tools/exact-identity boundary.

    ``:batch`` is intentionally not handled here. It does not grant tools or external
    retrieval; it requires a different OpenRouter asynchronous transport. The active
    synchronous executor handles that separately before model assignment.
    """
    model = str(request.get("model") or "").strip()
    folded = model.casefold()
    if folded.startswith("openrouter/"):
        return model
    if ":online" in folded:
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
