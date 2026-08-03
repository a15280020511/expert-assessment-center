"""Account-compatible ZDR adapter for zero-cost shadow validation.

The adapter is explicitly non-production. It preserves the production builders,
parsers, materializer, runtime, and evidence chain while adapting unsupported
wire-only structured-output controls into an equivalent prompt constraint.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence

from openrouter_api import CHAT_URL, request_json
from v5_execution_primitives import actual_cost
from v5_free_shadow_support import FreeEndpoint, FreeShadowError
from v5_model_company import canonical_model_company

USER_MODELS_URL = "https://openrouter.ai/api/v1/models/user"
ZDR_ENDPOINTS_URL = "https://openrouter.ai/api/v1/endpoints/zdr"


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "HTTP-Referer": (
            "https://github.com/a15280020511/expert-assessment-center"
        ),
        "X-Title": "expert-center-zdr-free-shadow",
    }


def _get_json(url: str, api_key: str) -> Mapping[str, Any]:
    request = urllib.request.Request(url, headers=_headers(api_key))
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise FreeShadowError(
            f"account-compatible catalog request failed: HTTP {exc.code}: "
            f"{body[:300]}"
        ) from exc
    if not isinstance(value, Mapping):
        raise FreeShadowError("account-compatible catalog root is not an object")
    return value


def _data_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, Mapping)]


def _price(value: Any, fallback: float = 1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _zero_price(pricing: Mapping[str, Any]) -> bool:
    return (
        abs(_price(pricing.get("prompt"))) <= 1e-15
        and abs(_price(pricing.get("completion"))) <= 1e-15
    )


def _model_rows(api_key: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in _data_rows(_get_json(USER_MODELS_URL, api_key)):
        model_id = str(row.get("id") or "").strip()
        pricing = row.get("pricing")
        pricing = pricing if isinstance(pricing, Mapping) else {}
        if model_id.endswith(":free") and _zero_price(pricing):
            result[model_id] = row
    return result


def _zdr_candidates(
    api_key: str,
    models: Mapping[str, Mapping[str, Any]],
    *,
    minimum_context_tokens: int,
    minimum_completion_tokens: int,
) -> list[FreeEndpoint]:
    candidates: list[FreeEndpoint] = []
    for index, row in enumerate(
        _data_rows(_get_json(ZDR_ENDPOINTS_URL, api_key)),
        1,
    ):
        model_id = str(row.get("model_id") or "").strip()
        if model_id not in models or not model_id.endswith(":free"):
            continue
        pricing = row.get("pricing")
        pricing = pricing if isinstance(pricing, Mapping) else {}
        if not _zero_price(pricing):
            continue
        provider = str(
            row.get("tag")
            or row.get("provider_slug")
            or row.get("provider_name")
            or ""
        ).strip()
        if not provider:
            continue
        model = models[model_id]
        context = int(
            row.get("context_length")
            or model.get("context_length")
            or 0
        )
        top = model.get("top_provider")
        top = top if isinstance(top, Mapping) else {}
        maximum = int(
            row.get("max_completion_tokens")
            or top.get("max_completion_tokens")
            or 0
        )
        supported = tuple(
            sorted(
                {
                    str(value).casefold()
                    for value in (
                        row.get("supported_parameters")
                        or model.get("supported_parameters")
                        or []
                    )
                }
            )
        )
        if context < minimum_context_tokens:
            continue
        if maximum < minimum_completion_tokens:
            continue
        if not {"max_tokens", "max_completion_tokens"}.intersection(supported):
            continue
        candidates.append(
            FreeEndpoint(
                model=model_id,
                company=canonical_model_company(model_id),
                provider=provider,
                context_length=context,
                max_completion_tokens=maximum,
                supported_parameters=supported,
                official_order=index,
            )
        )
    candidates.sort(
        key=lambda row: (
            -row.max_completion_tokens,
            -row.context_length,
            row.official_order,
            row.model,
            row.provider.casefold(),
        )
    )
    return candidates


def discover_account_zdr_free_endpoint(
    api_key: str,
    *,
    minimum_context_tokens: int,
    minimum_completion_tokens: int,
) -> FreeEndpoint:
    models = _model_rows(api_key)
    candidates = _zdr_candidates(
        api_key,
        models,
        minimum_context_tokens=minimum_context_tokens,
        minimum_completion_tokens=minimum_completion_tokens,
    )
    if not candidates:
        raise FreeShadowError(
            "no account-compatible zero-price ZDR endpoint is usable"
        )
    return candidates[0]


def compatibility_governance_resolution(
    endpoint: FreeEndpoint,
    *,
    required_context_tokens: int,
    minimum_completion_tokens: int,
) -> dict[str, Any]:
    native = sorted(set(endpoint.supported_parameters))
    adapted = sorted(
        set(native)
        | {
            "max_tokens",
            "reasoning",
            "response_format",
            "temperature",
        }
    )

    def role(logical_model: str) -> dict[str, Any]:
        return {
            "logical_model": logical_model,
            "resolved_model": endpoint.model,
            "company": endpoint.company,
            "provider": endpoint.provider,
            "official_intelligence_rank": endpoint.official_order,
            "context_length": endpoint.context_length,
            "max_completion_tokens": endpoint.max_completion_tokens,
            "supported_parameters": adapted,
            "native_supported_parameters": native,
            "temperature_supported": True,
            "provider_fallback_allowed": False,
            "synthetic_fixture_only": False,
            "free_shadow_only": True,
            "wire_adapter_required": True,
            "structured_output_native": False,
            "structured_output_prompt_adapter": True,
        }

    return {
        "schema_version": "v5-free-shadow-compatible-governance-resolution-1",
        "status": "PASS",
        "selection_basis": "account-filtered-zero-price-zdr-endpoint",
        "required_context_tokens": required_context_tokens,
        "minimum_completion_tokens": minimum_completion_tokens,
        "provider_fallback_allowed": False,
        "formal_model_identity_qualified": False,
        "governance_company_diversity_qualified": False,
        "wire_parity_qualified": False,
        "gpt": role("~shadow/zdr-free-proposal"),
        "claude": role("~shadow/zdr-free-red-team"),
    }


def _schema_instruction(response_format: Mapping[str, Any]) -> str:
    json_schema = response_format.get("json_schema")
    json_schema = json_schema if isinstance(json_schema, Mapping) else {}
    schema = json_schema.get("schema")
    schema = schema if isinstance(schema, Mapping) else {}
    compact = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "\n\nFREE_SHADOW_JSON_COMPATIBILITY:\n"
        "只输出一个有效JSON对象；不要Markdown代码块、解释或额外文字。"
        "对象必须严格满足以下JSON Schema，禁止额外字段：\n"
        + compact
    )


def _adapt_messages(
    messages: Any,
    response_format: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        raise FreeShadowError("free compatibility request has no messages")
    adapted = [dict(row) for row in messages if isinstance(row, Mapping)]
    if response_format:
        instruction = _schema_instruction(response_format)
        if adapted and adapted[-1].get("role") == "user":
            adapted[-1]["content"] = (
                str(adapted[-1].get("content") or "") + instruction
            )
        else:
            adapted.append({"role": "user", "content": instruction.strip()})
    return adapted


def _output_limit(payload: Mapping[str, Any]) -> int:
    for key in ("max_tokens", "max_completion_tokens"):
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    raise FreeShadowError("free compatibility request has no output limit")


def _content_text(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, Mapping):
        return ""
    message = first.get("message")
    if not isinstance(message, Mapping):
        return ""
    return str(message.get("content") or "").strip()


def _strip_json_fence(text: str) -> tuple[str, bool]:
    stripped = text.strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return stripped, False
    lines = stripped.splitlines()
    if len(lines) < 3:
        return stripped, False
    if lines[0].strip().casefold() not in {"```", "```json"}:
        return stripped, False
    if lines[-1].strip() != "```":
        return stripped, False
    return "\n".join(lines[1:-1]).strip(), True


class CompatibilityFreeCallBoundary:
    """Zero-cost ZDR boundary with explicit non-production wire adaptation."""

    def __init__(self, endpoint: FreeEndpoint, maximum_calls: int = 4) -> None:
        self.endpoint = endpoint
        self.maximum_calls = int(maximum_calls)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        run: Any,
        request: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], float]:
        if len(self.calls) >= self.maximum_calls:
            raise FreeShadowError("free compatibility call ceiling exceeded")
        payload, adaptation = self._adapt_payload(request)
        started = time.monotonic()
        response = request_json(
            CHAT_URL,
            str(getattr(run, "api_key", "")),
            int(getattr(run, "model_timeout_seconds", 180)),
            0,
            payload,
        )
        latency = time.monotonic() - started
        normalized, fence_removed = self._validate_and_normalize(response)
        adaptation["json_fence_removed"] = fence_removed
        self._record(normalized, adaptation)
        return normalized, latency

    def _adapt_payload(
        self,
        request: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        requested_model = str(request.get("model") or "")
        if requested_model != self.endpoint.model:
            raise FreeShadowError(
                "free compatibility request model differs from bound endpoint"
            )
        provider = request.get("provider")
        provider = dict(provider) if isinstance(provider, Mapping) else {}
        only = provider.get("only")
        if not isinstance(only, list) or len(only) != 1:
            raise FreeShadowError("free compatibility provider lock is invalid")
        if str(only[0]).casefold() != self.endpoint.provider.casefold():
            raise FreeShadowError("free compatibility provider lock mismatch")
        if provider.get("allow_fallbacks") is not False:
            raise FreeShadowError("free compatibility fallback is forbidden")

        payload = dict(request)
        response_format = payload.pop("response_format", None)
        response_format = (
            dict(response_format)
            if isinstance(response_format, Mapping)
            else None
        )
        payload["messages"] = _adapt_messages(
            payload.get("messages"),
            response_format,
        )
        limit = _output_limit(payload)
        payload.pop("max_completion_tokens", None)
        payload["max_tokens"] = min(
            limit,
            self.endpoint.max_completion_tokens,
        )
        payload.pop("temperature", None)
        payload["reasoning"] = {"effort": "none", "exclude": True}
        payload["provider"] = {
            "only": [self.endpoint.provider],
            "order": [self.endpoint.provider],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "allow",
            "zdr": True,
        }
        original_digest = hashlib.sha256(
            json.dumps(
                request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        wire_digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return payload, {
            "response_format_moved_to_prompt": response_format is not None,
            "reasoning_forced_off": True,
            "temperature_removed": "temperature" in request,
            "zdr_forced": True,
            "provider_fallback_allowed": False,
            "original_request_sha256": original_digest,
            "wire_request_sha256": wire_digest,
        }

    def _validate_and_normalize(
        self,
        response: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        model = str(response.get("model") or "")
        provider = str(response.get("provider") or "")
        cost = float(actual_cost(response))
        if model != self.endpoint.model:
            raise FreeShadowError(
                f"free compatibility model mismatch: {model}"
            )
        if provider.casefold() != self.endpoint.provider.casefold():
            raise FreeShadowError(
                f"free compatibility provider mismatch: {provider}"
            )
        if abs(cost) > 1e-12:
            raise FreeShadowError(
                f"free compatibility returned positive cost: {cost}"
            )
        normalized = dict(response)
        choices = normalized.get("choices")
        if not isinstance(choices, list) or not choices:
            return normalized, False
        first = choices[0]
        if not isinstance(first, Mapping):
            return normalized, False
        message = first.get("message")
        if not isinstance(message, Mapping):
            return normalized, False
        text, removed = _strip_json_fence(_content_text(normalized))
        new_message = dict(message)
        new_message["content"] = text
        new_first = dict(first)
        new_first["message"] = new_message
        new_choices = list(choices)
        new_choices[0] = new_first
        normalized["choices"] = new_choices
        return normalized, removed

    def _record(
        self,
        response: Mapping[str, Any],
        adaptation: Mapping[str, Any],
    ) -> None:
        usage = response.get("usage")
        usage = dict(usage) if isinstance(usage, Mapping) else {}
        self.calls.append(
            {
                "sequence": len(self.calls) + 1,
                "model": str(response.get("model") or ""),
                "company": self.endpoint.company,
                "provider": str(response.get("provider") or ""),
                "actual_cost_usd": float(actual_cost(response)),
                "response_id": str(response.get("id") or "") or None,
                "usage": usage,
                "wire_adaptation": dict(adaptation),
            }
        )

    def receipt(self) -> dict[str, Any]:
        total = sum(float(row["actual_cost_usd"]) for row in self.calls)
        return {
            "schema_version": "v5-free-shadow-compatible-boundary-1",
            "status": (
                "PASS" if len(self.calls) == self.maximum_calls else "FAIL"
            ),
            "mode": "single-endpoint-zdr-prompt-json-compatibility",
            "maximum_calls": self.maximum_calls,
            "actual_calls": len(self.calls),
            "paid_model_calls": 0,
            "paid_fallback_allowed": False,
            "actual_cost_usd": round(total, 12),
            "zdr_required": True,
            "formal_model_identity_qualified": False,
            "wire_parity_qualified": False,
            "calls": list(self.calls),
        }
