#!/usr/bin/env python3
"""Run non-destructive live checks from the dccai-responses container."""

import base64
import json
import os
import sqlite3
import struct
import time
import zlib

import httpx


def test_png_data_url() -> str:
    width, height = 64, 32
    rows = []
    for _ in range(height):
        pixels = b"".join(
            b"\xff\x00\x00" if x < width // 2 else b"\x00\x00\xff"
            for x in range(width)
        )
        rows.append(b"\x00" + pixels)
    raw = b"".join(rows)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode()


def active_api_key() -> str:
    conn = sqlite3.connect("/webui-data/webui.db")
    try:
        row = conn.execute(
            "SELECT key FROM api_key WHERE expires_at IS NULL OR expires_at > ? ORDER BY created_at DESC LIMIT 1",
            (time.time(),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise RuntimeError("No active Open WebUI API key is available for the live test")
    return row[0]


def output_text(response: dict) -> str:
    parts = []
    for item in response.get("output") or []:
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                parts.append(part.get("text", ""))
    return "\n".join(parts)


def main():
    master_key = os.environ["LITELLM_MASTER_KEY"]
    user_key = active_api_key()
    image_url = test_png_data_url()
    internal_headers = {"Authorization": f"Bearer {master_key}"}
    user_headers = {"Authorization": f"Bearer {user_key}"}
    checks = []

    with httpx.Client(timeout=180) as client:
        for model in ("dccai-high", "dccai-low", "dccai-code", "dccai-local-80b"):
            response = client.post(
                "http://litellm:4000/v1/chat/completions",
                headers=internal_headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with exactly OK."}],
                    "max_tokens": 80,
                },
            )
            response.raise_for_status()
            answer = response.json()["choices"][0]["message"].get("content") or ""
            checks.append((f"{model} text", bool(answer.strip()), answer[:80]))

        response = client.post(
            "http://litellm:4000/v1/chat/completions",
            headers=internal_headers,
            json={
                "model": "dccai-local-80b",
                "messages": [{"role": "user", "content": "東京の天気をツールで取得してください。"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "description": "指定都市の天気を取得する",
                            "parameters": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                                "required": ["city"],
                            },
                        },
                    }
                ],
                "tool_choice": "auto",
                "temperature": 0,
                "max_tokens": 128,
            },
        )
        response.raise_for_status()
        local_message = response.json()["choices"][0]["message"]
        checks.append(
            (
                "dccai-local-80b tools",
                bool(local_message.get("tool_calls")),
                json.dumps(local_message, ensure_ascii=False)[:500],
            )
        )

        with client.stream(
            "POST",
            "http://litellm:4000/v1/chat/completions",
            headers=internal_headers,
            json={
                "model": "dccai-local-80b",
                "messages": [{"role": "user", "content": "Reply with exactly LOCAL_STREAM_OK."}],
                "stream": True,
                "stream_options": {"include_usage": True},
                "temperature": 0,
                "max_tokens": 32,
            },
        ) as response:
            response.raise_for_status()
            local_stream_body = response.read().decode(errors="replace")
        local_stream_text = []
        local_stream_has_usage = False
        for line in local_stream_body.splitlines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                chunk = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if chunk.get("usage", {}).get("total_tokens") is not None:
                local_stream_has_usage = True
            for choice in chunk.get("choices") or []:
                local_stream_text.append(choice.get("delta", {}).get("content") or "")
        checks.append(
            (
                "dccai-local-80b stream usage",
                "LOCAL_STREAM_OK" in "".join(local_stream_text) and local_stream_has_usage,
                f"bytes={len(local_stream_body)}",
            )
        )

        response = client.post(
            "http://litellm:4000/v1/chat/completions",
            headers=internal_headers,
            json={
                "model": "dccai-code",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "画像の左右の色だけを日本語で答えて。"},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
                "max_tokens": 1000,
            },
        )
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"].get("content") or ""
        vision_ok = "赤" in answer and ("青" in answer or "ブルー" in answer)
        checks.append(("dccai-code native vision", vision_ok, answer[:160]))

        responses_payload = {
            "model": "dccai.dccai-code",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "画像の左右の色だけを日本語で答えて。"},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }
            ],
            "max_output_tokens": 1000,
        }
        response = client.post(
            "http://127.0.0.1:3003/v1/responses",
            headers=user_headers,
            json=responses_payload,
        )
        response.raise_for_status()
        body = response.json()
        answer = output_text(body)
        no_reasoning = not any(item.get("type") == "reasoning" for item in body.get("output") or [])
        vision_ok = "赤" in answer and ("青" in answer or "ブルー" in answer)
        checks.append(("Responses Code vision", vision_ok, answer[:160]))
        checks.append(("Responses reasoning hidden", no_reasoning, str([x.get("type") for x in body.get("output") or []])))

        response = client.post(
            "http://127.0.0.1:3003/v1/responses",
            headers=user_headers,
            json={"model": "dccai.dccai-code", "input": "Reply with exactly STREAM_OK.", "stream": True},
        )
        response.raise_for_status()
        stream_body = response.text
        stream_safe = '"type":"reasoning"' not in stream_body and "reasoning_text" not in stream_body
        checks.append(("Responses stream reasoning hidden", stream_safe, f"bytes={len(stream_body)}"))

        response = client.post(
            "http://open-webui:8080/api/chat/completions",
            headers=user_headers,
            json={
                "model": "dccai.dccai-code",
                "messages": [{"role": "user", "content": "東京の天気を取得してください。"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "description": "指定都市の天気を取得する",
                            "parameters": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                                "required": ["city"],
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
                "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
                "stream": False,
            },
        )
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        tool_ok = bool(message.get("tool_calls")) or "DSML" in (message.get("content") or "")
        checks.append(("Chat API tools forwarded", tool_ok, json.dumps(message, ensure_ascii=False)[:500]))
        checks.append(
            (
                "Chat API reasoning hidden",
                "reasoning_content" not in json.dumps(message),
                str(sorted(message.keys())),
            )
        )

        response = client.post(
            "http://open-webui:8080/api/chat/completions",
            headers=user_headers,
            json={
                "model": "dccai.dccai-local-80b",
                "messages": [{"role": "user", "content": "Reply with LOCAL_API_SHOULD_NOT_RUN."}],
                "stream": False,
            },
        )
        local_chat_api_body = response.text
        local_chat_api_blocked = False
        if response.status_code == 400:
            try:
                local_chat_api_blocked = response.json().get("detail") == "Model not found"
            except ValueError:
                local_chat_api_blocked = False
        elif response.status_code == 200:
            local_chat_api_blocked = (
                "Webチャット画面からのみ" in local_chat_api_body
                and "LOCAL_API_SHOULD_NOT_RUN" not in local_chat_api_body
            )
        checks.append(
            (
                "Chat API Local 80B blocked",
                local_chat_api_blocked,
                f"status={response.status_code} body={local_chat_api_body[:300]}",
            )
        )

        response = client.post(
            "http://127.0.0.1:3003/v1/responses",
            headers=user_headers,
            json={"model": "dccai.dccai-local-80b", "input": "LOCAL_API_SHOULD_NOT_RUN"},
        )
        checks.append(
            (
                "Responses API Local 80B blocked",
                response.status_code == 400
                and response.json().get("error", {}).get("type") == "invalid_request_error",
                f"status={response.status_code} body={response.text[:240]}",
            )
        )

        response = client.get("http://searxng:8080/search", params={"q": "OpenAI", "format": "json"})
        response.raise_for_status()
        result_count = len(response.json().get("results") or [])
        checks.append(("SearXNG results", result_count > 0, f"results={result_count}"))

    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    if not all(ok for _, ok, _ in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
