"""Minimal OpenRouter HTTP client with bounded responses and jittered retries."""
from __future__ import annotations

import json
import os
import random
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
    pass


def headers(api_key: Optional[str]) -> Dict[str, str]:
    result = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        result["Authorization"] = f"Bearer {api_key}"
    result["HTTP-Referer"] = os.getenv("OPENROUTER_SITE_URL", "https://github.com")
    result["X-Title"] = os.getenv("OPENROUTER_APP_NAME", "Self-Managed Open Model Expert Team")
    return result


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    if retry_after:
        try:
            return min(60.0, max(0.0, float(retry_after)))
        except ValueError:
            try:
                target = parsedate_to_datetime(retry_after).timestamp()
                return min(60.0, max(0.0, target - time.time()))
            except (TypeError, ValueError, OverflowError):
                pass
    base = min(2 ** attempt, 8)
    return base + random.uniform(0.0, max(0.25, base * 0.35))


def _decode_response(response: Any, url: str) -> Dict[str, Any]:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise OpenRouterRequestError(
            f"Response from {url} exceeds the {MAX_RESPONSE_BYTES}-byte safety limit."
        )
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenRouterRequestError(f"Invalid JSON from {url}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise OpenRouterRequestError(f"Non-object JSON from {url}")
    return parsed


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
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return _decode_response(response, url)
        except urllib.error.HTTPError as exc:
            body = exc.read(2000).decode("utf-8", errors="replace")
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            last_error = OpenRouterRequestError(
                f"HTTP {exc.code} from OpenRouter after attempt {attempt + 1}/{max_retries + 1}: {body}"
            )
            if exc.code not in RETRYABLE_HTTP_CODES or attempt >= max_retries:
                raise last_error
        except (urllib.error.URLError, TimeoutError, OpenRouterRequestError) as exc:
            last_error = exc
            if attempt >= max_retries:
                raise OpenRouterRequestError(
                    f"OpenRouter request failed after {attempt + 1} attempt(s): {exc}"
                ) from exc
        time.sleep(_retry_delay(attempt, retry_after))
    raise OpenRouterRequestError(f"OpenRouter request failed: {last_error}")
