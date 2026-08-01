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
from typing import Any, Dict, Optional

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
RETRYABLE_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


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
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = bool(retryable)
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.request_sent = bool(request_sent)
        self.response_received = bool(response_received)


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


def _decode_response(response: Any, url: str) -> Dict[str, Any]:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise OpenRouterRequestError(
            f"Response from {url} exceeds the {MAX_RESPONSE_BYTES}-byte safety limit.",
            category="invalid_response",
            retryable=False,
            request_sent=True,
            response_received=True,
        )
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenRouterRequestError(
            f"Invalid JSON from {url}: {exc}",
            category="invalid_response",
            retryable=False,
            request_sent=True,
            response_received=True,
        ) from exc
    if not isinstance(parsed, dict):
        raise OpenRouterRequestError(
            f"Non-object JSON from {url}",
            category="invalid_response",
            retryable=False,
            request_sent=True,
            response_received=True,
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
