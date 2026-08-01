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
import json
import os
import sqlite3
import threading
import time
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
            if stream:
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
