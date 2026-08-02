from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from openrouter_api import OpenRouterRequestError, _decode_response  # noqa: E402


class _Headers(dict[str, str]):
    def get(self, key: str, default=None):
        for existing, value in self.items():
            if existing.casefold() == key.casefold():
                return value
        return default


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        content_type: str = "application/json",
        status: int = 200,
    ) -> None:
        self.body = body
        self.headers = _Headers(
            {
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
            }
        )
        self.status = status

    def read(self, _limit: int) -> bytes:
        return self.body


class OpenRouterResponseProtocolTests(unittest.TestCase):
    def test_utf8_bom_json_is_accepted(self) -> None:
        body = b"\xef\xbb\xbf" + json.dumps({"ok": True}).encode("utf-8")
        self.assertEqual({"ok": True}, _decode_response(_Response(body), "test"))

    def test_explicit_sse_chunks_are_reassembled(self) -> None:
        chunks = [
            {
                "id": "gen-1",
                "model": "example/model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "北门"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "gen-1",
                "model": "example/model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "安全"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        ]
        text = "".join(
            f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            for chunk in chunks
        ) + "data: [DONE]\n\n"
        parsed = _decode_response(
            _Response(text.encode("utf-8"), content_type="text/event-stream"),
            "test",
        )
        self.assertEqual("北门安全", parsed["choices"][0]["message"]["content"])
        self.assertEqual("stop", parsed["choices"][0]["finish_reason"])
        self.assertEqual(2, parsed["usage"]["completion_tokens"])

    def test_invalid_json_fails_closed_with_protocol_diagnostics(self) -> None:
        body = b'{"choices": [\n'
        with self.assertRaises(OpenRouterRequestError) as raised:
            _decode_response(_Response(body), "test")
        error = raised.exception
        self.assertEqual("invalid_response", error.category)
        self.assertFalse(error.retryable)
        diagnostics = error.response_diagnostics
        self.assertEqual("openrouter-response-diagnostics-1", diagnostics["schema_version"])
        self.assertEqual("application/json", diagnostics["content_type"])
        self.assertEqual(len(body), diagnostics["bytes_received"])
        self.assertEqual(64, len(diagnostics["body_sha256"]))
        self.assertEqual("json", diagnostics["parse_mode"])
        self.assertEqual(2, diagnostics["json_error"]["line"])
        self.assertLessEqual(len(diagnostics["leading_token"]), 96)
        self.assertNotIn("choices", str(error))
        self.assertNotIn(body.decode("utf-8"), str(error))

    def test_html_or_arbitrary_text_is_not_repaired(self) -> None:
        body = b"<!doctype html><title>upstream error</title>"
        with self.assertRaises(OpenRouterRequestError) as raised:
            _decode_response(
                _Response(body, content_type="text/html"),
                "test",
            )
        self.assertTrue(raised.exception.response_diagnostics["starts_with_html"])
        self.assertEqual("json", raised.exception.response_diagnostics["parse_mode"])

    def test_malformed_sse_is_not_partially_accepted(self) -> None:
        body = b'data: {"choices": []}\n\nnot-an-sse-field\n'
        with self.assertRaises(OpenRouterRequestError) as raised:
            _decode_response(
                _Response(body, content_type="text/event-stream"),
                "test",
            )
        self.assertEqual("sse", raised.exception.response_diagnostics["parse_mode"])


if __name__ == "__main__":
    unittest.main()
