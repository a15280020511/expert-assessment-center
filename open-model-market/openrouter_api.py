"""Minimal OpenRouter HTTP client with bounded responses and structured failures."""
from __future__ import annotations

import json
import os
import queue
import random
import threading
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from hashlib import sha256
from typing import Any, Dict, Mapping, Optional

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
RETRYABLE_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_DIAGNOSTIC_TOKEN_CHARS = 96


class OpenRouterRequestError(RuntimeError):
    """Protocol-level failure with machine-readable recovery attributes."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "invalid_response",
        retryable: bool = False,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
        request_sent: bool = False,
        response_received: bool = False,
        response_diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = bool(retryable)
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.request_sent = bool(request_sent)
        self.response_received = bool(response_received)
        self.response_diagnostics = dict(response_diagnostics or {})


def headers(api_key: Optional[str]) -> Dict[str, str]:
    result = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        result["Authorization"] = f"Bearer {api_key}"
    result["HTTP-Referer"] = os.getenv("OPENROUTER_SITE_URL", "https://github.com")
    result["X-Title"] = os.getenv("OPENROUTER_APP_NAME", "Self-Managed Open Model Expert Team")
    return result


def _retry_after_seconds(retry_after: str | None) -> float | None:
    if not retry_after:
        return None
    try:
        return min(60.0, max(0.0, float(retry_after)))
    except ValueError:
        try:
            target = parsedate_to_datetime(retry_after).timestamp()
            return min(60.0, max(0.0, target - time.time()))
        except (TypeError, ValueError, OverflowError):
            return None


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    explicit = _retry_after_seconds(retry_after)
    if explicit is not None:
        return explicit
    base = min(2 ** attempt, 8)
    return base + random.uniform(0.0, max(0.25, base * 0.35))


def _header_value(response: Any, name: str) -> str:
    response_headers = getattr(response, "headers", None)
    if response_headers is None:
        return ""
    try:
        value = response_headers.get(name)
    except (AttributeError, KeyError, TypeError):
        return ""
    return str(value or "").strip()


def _safe_leading_token(text: str) -> str:
    stripped = str(text or "").lstrip("\ufeff \t\r\n")
    token = stripped[:MAX_DIAGNOSTIC_TOKEN_CHARS]
    return "".join(
        character if 32 <= ord(character) < 127 else "?"
        for character in token
    )


def _response_diagnostics(
    response: Any,
    raw: bytes,
    text: str,
    *,
    parse_mode: str,
    parse_error: BaseException | None = None,
) -> dict[str, Any]:
    content_type = _header_value(response, "Content-Type")
    content_encoding = _header_value(response, "Content-Encoding")
    status = getattr(response, "status", None)
    diagnostics: dict[str, Any] = {
        "schema_version": "openrouter-response-diagnostics-1",
        "http_status": int(status) if isinstance(status, int) else None,
        "content_type": content_type,
        "content_encoding": content_encoding,
        "content_length_header": _header_value(response, "Content-Length"),
        "bytes_received": len(raw),
        "body_sha256": sha256(raw).hexdigest(),
        "line_count": text.count("\n") + (1 if text else 0),
        "starts_with_sse_data": bool(
            text.lstrip("\ufeff \t\r\n").startswith("data:")
        ),
        "starts_with_html": bool(
            text.lstrip("\ufeff \t\r\n").casefold().startswith(
                ("<!doctype html", "<html")
            )
        ),
        "leading_token": _safe_leading_token(text),
        "parse_mode": parse_mode,
    }
    if isinstance(parse_error, json.JSONDecodeError):
        diagnostics["json_error"] = {
            "message": parse_error.msg,
            "line": parse_error.lineno,
            "column": parse_error.colno,
            "position": parse_error.pos,
        }
    elif parse_error is not None:
        diagnostics["parse_error_type"] = type(parse_error).__name__
        diagnostics["parse_error_message"] = str(parse_error)[:240]
    return diagnostics


def _sse_event_payloads(text: str) -> list[str]:
    events: list[str] = []
    data_lines: list[str] = []
    saw_sse_field = False
    for line in text.splitlines():
        if not line:
            if data_lines:
                events.append("\n".join(data_lines))
                data_lines = []
            continue
        if line.startswith(":"):
            saw_sse_field = True
            continue
        if line.startswith("data:"):
            saw_sse_field = True
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
            continue
        if line.startswith(("event:", "id:", "retry:")):
            saw_sse_field = True
            continue
        raise ValueError("non-SSE line encountered in event-stream response")
    if data_lines:
        events.append("\n".join(data_lines))
    if not saw_sse_field or not events:
        raise ValueError("event-stream response contains no data events")
    return events


def _merge_streaming_choices(payloads: list[Mapping[str, Any]]) -> dict[str, Any]:
    base: dict[str, Any] = {}
    choices_by_index: dict[int, dict[str, Any]] = {}
    usage: Mapping[str, Any] | None = None
    for payload in payloads:
        for key, value in payload.items():
            if key not in {"choices", "usage"} and key not in base:
                base[key] = value
        if isinstance(payload.get("usage"), Mapping):
            usage = dict(payload["usage"])
        choices = payload.get("choices")
        if not isinstance(choices, list):
            continue
        for position, raw_choice in enumerate(choices):
            if not isinstance(raw_choice, Mapping):
                continue
            raw_index = raw_choice.get("index", position)
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                index = position
            choice = choices_by_index.setdefault(
                index,
                {
                    "index": index,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": None,
                },
            )
            direct_message = raw_choice.get("message")
            delta = raw_choice.get("delta")
            source = direct_message if isinstance(direct_message, Mapping) else delta
            if isinstance(source, Mapping):
                message = choice["message"]
                role = source.get("role")
                if role:
                    message["role"] = str(role)
                for field in ("content", "reasoning", "reasoning_content"):
                    value = source.get(field)
                    if isinstance(value, str):
                        message[field] = str(message.get(field) or "") + value
                for field in ("tool_calls", "annotations"):
                    value = source.get(field)
                    if isinstance(value, list):
                        message.setdefault(field, []).extend(value)
            finish_reason = raw_choice.get("finish_reason")
            if finish_reason is not None:
                choice["finish_reason"] = finish_reason
            if "logprobs" in raw_choice:
                choice["logprobs"] = raw_choice.get("logprobs")
    if not choices_by_index:
        if len(payloads) == 1:
            return dict(payloads[0])
        raise ValueError("event-stream response contains no chat completion choices")
    base["choices"] = [choices_by_index[index] for index in sorted(choices_by_index)]
    if usage is not None:
        base["usage"] = dict(usage)
    return base


def _decode_sse(text: str) -> dict[str, Any]:
    decoded_payloads: list[Mapping[str, Any]] = []
    for event in _sse_event_payloads(text):
        if event.strip() == "[DONE]":
            continue
        payload = json.loads(event)
        if not isinstance(payload, Mapping):
            raise ValueError("SSE data payload is not a JSON object")
        decoded_payloads.append(payload)
    if not decoded_payloads:
        raise ValueError("event-stream response ended without a JSON payload")
    return _merge_streaming_choices(decoded_payloads)


def _decode_response(response: Any, url: str) -> Dict[str, Any]:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        diagnostics = _response_diagnostics(
            response,
            raw[:MAX_RESPONSE_BYTES],
            "",
            parse_mode="size-limit",
        )
        raise OpenRouterRequestError(
            f"Response from {url} exceeds the {MAX_RESPONSE_BYTES}-byte safety limit.",
            category="invalid_response",
            retryable=False,
            request_sent=True,
            response_received=True,
            response_diagnostics=diagnostics,
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        diagnostics = _response_diagnostics(
            response,
            raw,
            raw.decode("utf-8", errors="replace"),
            parse_mode="utf8",
            parse_error=exc,
        )
        raise OpenRouterRequestError(
            f"Invalid UTF-8 response from {url}; response_sha256={diagnostics['body_sha256']}",
            category="invalid_response",
            retryable=False,
            request_sent=True,
            response_received=True,
            response_diagnostics=diagnostics,
        ) from exc

    try:
        parsed = json.loads(text)
        parse_mode = "json"
    except json.JSONDecodeError as json_exc:
        content_type = _header_value(response, "Content-Type").casefold()
        looks_like_sse = text.lstrip("\ufeff \t\r\n").startswith("data:")
        if "text/event-stream" not in content_type and not looks_like_sse:
            diagnostics = _response_diagnostics(
                response,
                raw,
                text,
                parse_mode="json",
                parse_error=json_exc,
            )
            raise OpenRouterRequestError(
                f"Invalid JSON from {url}; response_sha256={diagnostics['body_sha256']}; "
                f"content_type={diagnostics['content_type'] or 'unknown'}; "
                f"bytes={diagnostics['bytes_received']}; "
                f"json_error={json_exc.msg} at line {json_exc.lineno} column {json_exc.colno}",
                category="invalid_response",
                retryable=False,
                request_sent=True,
                response_received=True,
                response_diagnostics=diagnostics,
            ) from json_exc
        try:
            parsed = _decode_sse(text)
            parse_mode = "sse"
        except (ValueError, json.JSONDecodeError) as sse_exc:
            diagnostics = _response_diagnostics(
                response,
                raw,
                text,
                parse_mode="sse",
                parse_error=sse_exc,
            )
            diagnostics["initial_json_error"] = {
                "message": json_exc.msg,
                "line": json_exc.lineno,
                "column": json_exc.colno,
                "position": json_exc.pos,
            }
            raise OpenRouterRequestError(
                f"Invalid event-stream response from {url}; "
                f"response_sha256={diagnostics['body_sha256']}; "
                f"content_type={diagnostics['content_type'] or 'unknown'}; "
                f"bytes={diagnostics['bytes_received']}",
                category="invalid_response",
                retryable=False,
                request_sent=True,
                response_received=True,
                response_diagnostics=diagnostics,
            ) from sse_exc
    if not isinstance(parsed, dict):
        diagnostics = _response_diagnostics(
            response,
            raw,
            text,
            parse_mode=parse_mode,
        )
        raise OpenRouterRequestError(
            f"Non-object JSON from {url}; response_sha256={diagnostics['body_sha256']}",
            category="invalid_response",
            retryable=False,
            request_sent=True,
            response_received=True,
            response_diagnostics=diagnostics,
        )
    return parsed


def _structured_error_code(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("code", "type"):
            value = error.get(key)
            if value not in {None, ""}:
                return str(value).strip().casefold()
    for key in ("code", "type"):
        value = payload.get(key)
        if value not in {None, ""}:
            return str(value).strip().casefold()
    return ""


def _http_category(status: int, body: str) -> str:
    code = _structured_error_code(body)
    if status == 429:
        return "rate_limited"
    if status in {408, 504}:
        return "timeout"
    if code in {
        "context_length_exceeded",
        "context_window_exceeded",
        "max_context_length_exceeded",
    }:
        return "context_overflow"
    if code in {
        "unsupported_parameter",
        "invalid_parameter",
        "parameter_not_supported",
    }:
        return "unsupported_parameter"
    return "invalid_response"


def _request_with_hard_deadline(
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
        except Exception as exc:
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


def request_json(
    url: str,
    api_key: Optional[str],
    timeout_seconds: int,
    max_retries: int,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    method = "POST" if payload is not None else "GET"
    last_error: Optional[BaseException] = None
    for attempt in range(max_retries + 1):
        request = urllib.request.Request(url, data=data, headers=headers(api_key), method=method)
        retry_after = None
        try:
            return _request_with_hard_deadline(
                request,
                url,
                timeout_seconds,
            )
        except urllib.error.HTTPError as exc:
            body = exc.read(2000).decode("utf-8", errors="replace")
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            retryable = exc.code in RETRYABLE_HTTP_CODES
            last_error = OpenRouterRequestError(
                f"HTTP {exc.code} from OpenRouter after attempt {attempt + 1}/{max_retries + 1}: {body}",
                category=_http_category(exc.code, body),
                retryable=retryable,
                http_status=exc.code,
                retry_after_seconds=_retry_after_seconds(retry_after),
                request_sent=True,
                response_received=True,
            )
            if not retryable or attempt >= max_retries:
                raise last_error
        except TimeoutError as exc:
            last_error = OpenRouterRequestError(
                f"OpenRouter request timed out after attempt {attempt + 1}/{max_retries + 1}: {exc}",
                category="timeout",
                retryable=True,
                request_sent=True,
                response_received=False,
            )
            if attempt >= max_retries:
                raise last_error from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            category = "timeout" if isinstance(reason, TimeoutError) else "invalid_response"
            last_error = OpenRouterRequestError(
                f"OpenRouter transport failed after attempt {attempt + 1}/{max_retries + 1}: {exc}",
                category=category,
                retryable=True,
                request_sent=True,
                response_received=False,
            )
            if attempt >= max_retries:
                raise last_error from exc
        except OpenRouterRequestError as exc:
            last_error = exc
            if not exc.retryable or attempt >= max_retries:
                raise
        time.sleep(_retry_delay(attempt, retry_after))
    if isinstance(last_error, OpenRouterRequestError):
        raise last_error
    raise OpenRouterRequestError(
        f"OpenRouter request failed: {last_error}",
        category="invalid_response",
        retryable=False,
        request_sent=True,
    )
