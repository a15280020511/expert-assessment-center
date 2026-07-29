"""Normalize model responses and preserve diagnostics without chain-of-thought."""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping

from model_market import ExpertTeamError


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        chunks = []
        for part in value:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, Mapping):
                for key in ("text", "output_text", "content"):
                    item = part.get(key)
                    if isinstance(item, str) and item.strip():
                        chunks.append(item)
                        break
        return "".join(chunks).strip()
    if isinstance(value, Mapping):
        for key in ("text", "output_text", "content"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def diagnostics(response: Mapping[str, Any]) -> Dict[str, Any]:
    choices = response.get("choices") if isinstance(response, Mapping) else None
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
    message = choice.get("message") if isinstance(choice.get("message"), Mapping) else {}
    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), Mapping) else {}
    return {
        "response_id": response.get("id"),
        "model": response.get("model"),
        "provider": response.get("provider"),
        "finish_reason": choice.get("finish_reason"),
        "native_finish_reason": choice.get("native_finish_reason"),
        "content_present": bool(_text(message.get("content")) or _text(choice.get("text"))),
        "reasoning_present": bool(message.get("reasoning") or message.get("reasoning_content") or message.get("reasoning_details")),
        "refusal_present": bool(message.get("refusal")),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cost": usage.get("cost", usage.get("total_cost")),
    }


def sanitized(response: Mapping[str, Any]) -> Dict[str, Any]:
    clean = copy.deepcopy(dict(response))
    choices = clean.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
                continue
            message = choice["message"]
            for key in ("reasoning", "reasoning_content", "reasoning_details"):
                if key in message:
                    value = message.pop(key)
                    message[f"{key}_omitted"] = True
                    if isinstance(value, list):
                        message[f"{key}_item_count"] = len(value)
    return clean


def extract_answer(response: Mapping[str, Any]) -> str:
    choices = response.get("choices") if isinstance(response, Mapping) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ExpertTeamError("Response missing choices[0]. " + json.dumps(diagnostics(response), ensure_ascii=False))
    choice = choices[0]
    message = choice.get("message") if isinstance(choice.get("message"), Mapping) else {}
    text = _text(message.get("content")) or _text(choice.get("text"))
    if text:
        return text
    info = diagnostics(response)
    if info["refusal_present"]:
        raise ExpertTeamError("Model refused to provide a final answer. " + json.dumps(info, ensure_ascii=False))
    if info["reasoning_present"]:
        raise ExpertTeamError("Model returned reasoning but no final answer. " + json.dumps(info, ensure_ascii=False))
    raise ExpertTeamError("Model returned an empty final answer. " + json.dumps(info, ensure_ascii=False))
