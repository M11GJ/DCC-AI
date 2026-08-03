#!/usr/bin/env python3
"""
DCC AI - Responses API Gateway
外部からの Responses API 呼び出し(POST /v1/responses)を litellm へ中継する。
Open WebUI の Pipe(dccai_pipe.py)は Chat Completions 専用で Responses API 形式を
扱えないため(Open WebUI 本体の /responses ルートも「Connection」モデル専用で
Pipeモデルには使えない)、同等のポリシー(認証・モデルルーティング・Code専用の
同時実行制限・月間トークン上限)を持つ別サービスとして用意する。

月間トークン上限のカウンタファイルは dccai_pipe.py の TOKEN_USAGE_FILE と同じパスを
共有し、Chat Completions API・Responses API の両方をまとめて「API経由」の同じ枠
として消費する(WebUIチャットは引き続き対象外)。
"""
import fcntl
import html
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import date
from http import server as http_server
from urllib.parse import urlparse

import httpx

PORT = int(os.environ.get("PORT", "3003"))
LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
WEBUI_DB_PATH = os.environ.get("WEBUI_DB_PATH", "/webui-data/webui.db")
# dccai_pipe.py の Valve TOKEN_USAGE_FILE と同じボリューム上の同じファイルを指すこと
# (WebUI側は /app/backend/data/... というパスで見えるが、実体は同じ名前付きボリューム)。
TOKEN_USAGE_FILE = os.environ.get("TOKEN_USAGE_FILE", "/webui-data/dcc_ai_token_usage.json")
# dccai_pipe.py の Valve MONTHLY_TOKEN_LIMIT と同じ値を維持すること。
MONTHLY_TOKEN_LIMIT = int(os.environ.get("MONTHLY_TOKEN_LIMIT", "10000000"))
CODE_MAX_CONCURRENCY = int(os.environ.get("CODE_MAX_CONCURRENCY", "1"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "1200"))

_code_sem = threading.Semaphore(CODE_MAX_CONCURRENCY)


def resolve_user(api_key: str):
    """Open WebUIのapi_keyテーブルでBearerトークンを検証し、user_idを返す
    (Open WebUI自身の認証と同じ検証方法)。"""
    if not api_key:
        return None
    conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, expires_at FROM api_key WHERE key = ?", (api_key,))
        row = cur.fetchone()
        if not row:
            return None
        user_id, expires_at = row
        if expires_at and expires_at < time.time():
            return None
        return user_id
    finally:
        conn.close()


# ---- 月間トークンカウンタ(dccai_pipe.py と同じファイル・同じflock方式で共有) ----
def _locked_rmw(path, mutate) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".lock", "a+") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
                data = mutate(data)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)
    except Exception:
        pass


def month_tokens_used(user_id: str) -> int:
    try:
        with open(TOKEN_USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    month = date.today().strftime("%Y-%m")
    return int(data.get(month, {}).get(user_id, 0))


def add_tokens(user_id: str, n: int) -> None:
    if n <= 0:
        return
    month = date.today().strftime("%Y-%m")

    def mutate(data):
        data = {month: data.get(month, {})}  # 当月分だけ残して掃除(dccai_pipe.pyと同じ挙動)
        data[month][user_id] = data[month].get(user_id, 0) + n
        return data

    _locked_rmw(TOKEN_USAGE_FILE, mutate)


def resolve_model(raw_model: str):
    """公開向けID("dccai.dccai-high-vision"等)・litellm内部名("dccai-high"等)の
    どちらで来ても内部名に正規化する(dccai_pipe.pyと同じsuffix判定ロジック)。
    戻り値は (litellm_model, is_code)。"""
    suffix = (raw_model or "").rsplit(".", 1)[-1].lower()
    if "code" in suffix:
        return "dccai-code", True
    if "high" in suffix:
        return "dccai-high", False
    return "dccai-low", False


def normalize_request_tools(payload: dict) -> None:
    """Responses APIとChat Completionsの両方のfunction tool表現を受け付ける。

    LiteLLM 1.93.0はResponses API上でChat Completions形式の
    {"type":"function","function":{...}}を受けると、ネスト内のnameを読まず
    空の関数名へ変換する。Codex系クライアントもこの形式を送ることがあるため、
    LiteLLMへ渡す前にResponses API標準のトップレベル形式へ揃える。
    """
    tools = payload.get("tools")
    if isinstance(tools, list):
        normalized = []
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("type") != "function":
                normalized.append(tool)
                continue
            nested = tool.get("function")
            if not isinstance(nested, dict):
                normalized.append(tool)
                continue
            flat = {k: v for k, v in tool.items() if k != "function"}
            for key in ("name", "description", "parameters", "strict"):
                if key not in flat and key in nested:
                    flat[key] = nested[key]
            normalized.append(flat)
        payload["tools"] = normalized

    choice = payload.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") == "function":
        nested = choice.get("function")
        if isinstance(nested, dict) and not choice.get("name") and nested.get("name"):
            payload["tool_choice"] = {"type": "function", "name": nested["name"]}


_DSML = r"[|｜]DSML[|｜]"
_DSML_BLOCK_RE = re.compile(
    rf"<{_DSML}tool_calls>(.*?)</{_DSML}tool_calls>", re.DOTALL
)
_DSML_INVOKE_RE = re.compile(
    rf"<{_DSML}invoke\s+name=\"([^\"]+)\"\s*>(.*?)</{_DSML}invoke>", re.DOTALL
)
_DSML_PARAM_RE = re.compile(
    rf"<{_DSML}parameter\s+name=\"([^\"]+)\"\s+string=\"(true|false)\"\s*>(.*?)</{_DSML}parameter>",
    re.DOTALL,
)


def extract_dsml_tool_calls(text: str):
    """DeepSeek V4のDSML文字列を(name, arguments)へ戻す。

    公式encoding_dsv4.pyの形式に合わせ、半角/全角の縦棒表記をどちらも許容する。
    DSMLが無い場合は元テキストと空リストを返し、壊れたDSMLはValueErrorにする。
    """
    blocks = list(_DSML_BLOCK_RE.finditer(text))
    if not blocks:
        return text, []

    calls = []
    for block in blocks:
        body = block.group(1)
        invokes = list(_DSML_INVOKE_RE.finditer(body))
        if not invokes:
            raise ValueError("DSML tool_calls block has no invoke")
        for invoke in invokes:
            name = html.unescape(invoke.group(1))
            params = {}
            for match in _DSML_PARAM_RE.finditer(invoke.group(2)):
                key = html.unescape(match.group(1))
                if key in params:
                    raise ValueError(f"duplicate DSML parameter: {key}")
                raw_value = html.unescape(match.group(3))
                if match.group(2) == "true":
                    value = raw_value
                else:
                    value = json.loads(raw_value)
                params[key] = value
            calls.append({"name": name, "arguments": params})

    cleaned = _DSML_BLOCK_RE.sub("", text).strip()
    return cleaned, calls


def normalize_dsml_response(response: dict, request_payload: dict) -> bool:
    """漏れたDSMLを標準Responses APIのoutput itemへ変換する。"""
    output = response.get("output")
    if not isinstance(output, list):
        return False

    tool_types = {}
    for tool in request_payload.get("tools") or []:
        if isinstance(tool, dict) and isinstance(tool.get("name"), str):
            tool_types[tool["name"]] = tool.get("type", "function")

    converted = False
    rebuilt = []
    for item in output:
        rebuilt.append(item)
        if not isinstance(item, dict):
            continue
        pending_calls = []
        for part in item.get("content") or []:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            text = part.get("text")
            if not isinstance(text, str) or "DSML" not in text:
                continue
            try:
                cleaned, calls = extract_dsml_tool_calls(text)
            except (ValueError, json.JSONDecodeError) as exc:
                print(f"responses_gateway: malformed DSML left as text: {exc}", flush=True)
                continue
            if not calls:
                continue
            unknown_names = [call["name"] for call in calls if call["name"] not in tool_types]
            if unknown_names:
                print(
                    f"responses_gateway: DSML names not present in request tools: {unknown_names}",
                    flush=True,
                )
                continue
            part["text"] = cleaned
            pending_calls.extend(calls)
            converted = True

        for call in pending_calls:
            call_id = f"call_dsml_{uuid.uuid4().hex}"
            name = call["name"]
            arguments = call["arguments"]
            if tool_types.get(name) == "custom":
                if set(arguments) == {"content"} and isinstance(arguments["content"], str):
                    custom_input = arguments["content"]
                else:
                    custom_input = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
                rebuilt.append(
                    {
                        "type": "custom_tool_call",
                        "id": call_id,
                        "call_id": call_id,
                        "name": name,
                        "input": custom_input,
                        "status": "completed",
                    }
                )
            else:
                rebuilt.append(
                    {
                        "type": "function_call",
                        "id": call_id,
                        "call_id": call_id,
                        "name": name,
                        "arguments": json.dumps(
                            arguments, ensure_ascii=False, separators=(",", ":")
                        ),
                        "status": "completed",
                    }
                )
    response["output"] = rebuilt
    return converted


def response_sse_events(response: dict):
    """非stream応答から、クライアントが処理できる標準Responses SSEを生成する。"""
    sequence = 0

    def event(event_type, **fields):
        nonlocal sequence
        value = {"type": event_type, "sequence_number": sequence, **fields}
        sequence += 1
        return value

    shell = dict(response)
    shell["status"] = "in_progress"
    shell["output"] = []
    yield event("response.created", response=shell)
    yield event("response.in_progress", response=shell)

    for output_index, original in enumerate(response.get("output") or []):
        if not isinstance(original, dict):
            continue
        item = dict(original)
        item_type = item.get("type")
        if item_type == "function_call":
            arguments = item.get("arguments") or ""
            added = {**item, "arguments": "", "status": "in_progress"}
            yield event("response.output_item.added", output_index=output_index, item=added)
            if arguments:
                yield event(
                    "response.function_call_arguments.delta",
                    output_index=output_index,
                    item_id=item.get("id"),
                    delta=arguments,
                )
            yield event(
                "response.function_call_arguments.done",
                output_index=output_index,
                item_id=item.get("id"),
                name=item.get("name"),
                arguments=arguments,
            )
            yield event("response.output_item.done", output_index=output_index, item=item)
        elif item_type == "custom_tool_call":
            custom_input = item.get("input") or ""
            added = {**item, "input": "", "status": "in_progress"}
            yield event("response.output_item.added", output_index=output_index, item=added)
            if custom_input:
                yield event(
                    "response.custom_tool_call_input.delta",
                    output_index=output_index,
                    item_id=item.get("id"),
                    delta=custom_input,
                )
            yield event(
                "response.custom_tool_call_input.done",
                output_index=output_index,
                item_id=item.get("id"),
                input=custom_input,
            )
            yield event("response.output_item.done", output_index=output_index, item=item)
        elif item_type == "message":
            added = {**item, "content": [], "status": "in_progress"}
            yield event("response.output_item.added", output_index=output_index, item=added)
            for content_index, part in enumerate(item.get("content") or []):
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                text = part.get("text") or ""
                empty_part = {**part, "text": ""}
                yield event(
                    "response.content_part.added",
                    output_index=output_index,
                    content_index=content_index,
                    item_id=item.get("id"),
                    part=empty_part,
                )
                if text:
                    yield event(
                        "response.output_text.delta",
                        output_index=output_index,
                        content_index=content_index,
                        item_id=item.get("id"),
                        delta=text,
                    )
                yield event(
                    "response.output_text.done",
                    output_index=output_index,
                    content_index=content_index,
                    item_id=item.get("id"),
                    text=text,
                )
                yield event(
                    "response.content_part.done",
                    output_index=output_index,
                    content_index=content_index,
                    item_id=item.get("id"),
                    part=part,
                )
            yield event("response.output_item.done", output_index=output_index, item=item)
        else:
            yield event(
                "response.output_item.added",
                output_index=output_index,
                item={**item, "status": "in_progress"},
            )
            yield event("response.output_item.done", output_index=output_index, item=item)

    yield event("response.completed", response=response)


class Handler(http_server.BaseHTTPRequestHandler):
    def _send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/v1/responses", "/responses"):
            self._send_json(404, {"error": {"message": "not found"}})
            return

        auth = self.headers.get("Authorization", "")
        api_key = auth[7:] if auth.startswith("Bearer ") else ""
        user_id = resolve_user(api_key)
        if not user_id:
            self._send_json(401, {"error": {"message": "invalid API key", "type": "authentication_error"}})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except Exception:
            self._send_json(400, {"error": {"message": "invalid JSON body"}})
            return

        normalize_request_tools(payload)

        if MONTHLY_TOKEN_LIMIT > 0 and month_tokens_used(user_id) >= MONTHLY_TOKEN_LIMIT:
            self._send_json(
                429,
                {
                    "error": {
                        "message": (
                            f"今月の DCC AI API利用上限({MONTHLY_TOKEN_LIMIT:,} トークン)に達しました。"
                            "来月また使えます。"
                        ),
                        "type": "rate_limit_exceeded",
                    }
                },
            )
            return

        litellm_model, is_code = resolve_model(payload.get("model", ""))
        payload["model"] = litellm_model
        payload["user"] = f"{user_id}:api"  # litellm側の集計もdccai_pipe.pyと同じ形式に揃える
        stream = bool(payload.get("stream"))

        sem = _code_sem if is_code else None
        if sem:
            sem.acquire()
        try:
            self._proxy(payload, stream, user_id)
        finally:
            if sem:
                sem.release()

    def _proxy(self, payload, stream, user_id):
        url = f"{LITELLM_BASE_URL}/v1/responses"
        headers = {
            "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
            "Content-Type": "application/json",
        }
        try:
            # DSMLはストリーム途中では通常のoutput_text deltaと区別できず、転送後に
            # 取り消せない。ツール付きstreamだけ上流を一旦non-streamで受け、DSMLを
            # 正規化してから標準SSEを組み立てる。通常テキストstreamは従来どおり中継。
            if stream and payload.get("tools"):
                upstream_payload = dict(payload)
                upstream_payload["stream"] = False
                with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                    r = client.post(url, json=upstream_payload, headers=headers)
                    if r.status_code >= 400:
                        body = r.content
                        self.send_response(r.status_code)
                        self.send_header(
                            "Content-Type", r.headers.get("content-type", "application/json")
                        )
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return

                    resp_json = r.json()
                    normalize_dsml_response(resp_json, payload)
                    usage = resp_json.get("usage") or {}
                    if usage.get("total_tokens"):
                        add_tokens(user_id, usage["total_tokens"])

                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    for event in response_sse_events(resp_json):
                        event_type = event["type"]
                        data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                        self.wfile.write(f"event: {event_type}\ndata: {data}\n\n".encode())
                        self.wfile.flush()
                    self.close_connection = True
            elif stream:
                with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                    with client.stream("POST", url, json=payload, headers=headers) as r:
                        self.send_response(r.status_code)
                        self.send_header("Content-Type", r.headers.get("content-type", "text/event-stream"))
                        self.end_headers()
                        captured_tokens = 0
                        for line in r.iter_lines():
                            if not line:
                                continue
                            self.wfile.write((line + "\n\n").encode())
                            if line.startswith("data:"):
                                data_str = line[5:].strip()
                                if data_str and data_str != "[DONE]":
                                    try:
                                        chunk = json.loads(data_str)
                                        usage = (chunk.get("response") or chunk).get("usage")
                                        if usage and usage.get("total_tokens"):
                                            captured_tokens = usage["total_tokens"]
                                    except Exception:
                                        pass
                        if captured_tokens:
                            add_tokens(user_id, captured_tokens)
            else:
                with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                    r = client.post(url, json=payload, headers=headers)
                    body = r.content
                    try:
                        resp_json = r.json()
                        if r.status_code < 400:
                            normalize_dsml_response(resp_json, payload)
                            body = json.dumps(
                                resp_json, ensure_ascii=False, separators=(",", ":")
                            ).encode()
                        usage = resp_json.get("usage") or {}
                        if usage.get("total_tokens"):
                            add_tokens(user_id, usage["total_tokens"])
                    except Exception:
                        pass
                    self.send_response(r.status_code)
                    self.send_header("Content-Type", r.headers.get("content-type", "application/json"))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
        except Exception as e:
            self._send_json(502, {"error": {"message": f"upstream error: {e}"}})

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = http_server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
