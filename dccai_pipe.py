"""
title: DCC AI
author: DCC
version: 1.0.0
license: MIT
description: DCC部員向け DCC AI High/Low/Code。順番待ちUI・High日次上限つきで LiteLLM 経由で中継。
requirements: httpx
"""
import asyncio
import fcntl
import json
import os
import re
import base64
import glob
import mimetypes
from datetime import date
from typing import Optional, Callable, Awaitable, Any

import httpx
from pydantic import BaseModel, Field

# --- プロセス内で共有する同時実行の状態（順番待ち表示のため自前カウント） ---
# グループ(モデル系統)ごとに別々のセマフォを持つ。上限が違うグループを同じ
# セマフォで管理すると cap 変更のたびに作り直されて壊れるため分離している。
# モデル系統ごとのcapは独立して変更できる。
_STATE_BY_GROUP = {}


def _get_group_state(group: str, cap: int) -> dict:
    st = _STATE_BY_GROUP.get(group)
    if st is None or st["cap"] != cap:
        st = {"sem": asyncio.Semaphore(cap), "cap": cap, "active": 0, "waiting": 0}
        _STATE_BY_GROUP[group] = st
    return st


class Pipe:
    class Valves(BaseModel):
        LITELLM_BASE_URL: str = Field(
            default="http://litellm:4000/v1",
            description="LiteLLM の OpenAI 互換エンドポイント",
        )
        LITELLM_API_KEY: str = Field(
            default="", description="LiteLLM の master_key と同じ値"
        )
        HIGH_UPSTREAM: str = Field(
            default="dccai-high", description="LiteLLM 側の High モデル名"
        )
        LOW_UPSTREAM: str = Field(
            default="dccai-low", description="LiteLLM 側の Low モデル名"
        )
        CODE_UPSTREAM: str = Field(
            default="dccai-code", description="LiteLLM 側の Code モデル名"
        )
        VISION_UPSTREAM: str = Field(
            default="dccai-high",
            description="画像非対応モデルに画像が送られた場合の転送先（マルチモーダル対応必須）",
        )
        MAX_CONCURRENCY: int = Field(
            default=3,
            description="High/Lowの同時生成上限(DeepSeek公式APIが主経路のため実質rpm制限のみが効く)",
        )
        CODE_MAX_CONCURRENCY: int = Field(
            default=3,
            description="Codeモデル専用の同時生成上限",
        )
        MAX_QUEUE_WAIT: int = Field(
            default=180,
            description="混雑(429)時に順番待ちで自動再試行する最大秒数。超えたら案内を返す",
        )
        HIGH_DAILY_LIMIT: int = Field(
            default=20, description="High の 1 人 1 日あたり上限（0 で無制限）"
        )
        USAGE_FILE: str = Field(
            default="/app/backend/data/dcc_ai_high_usage.json",
            description="High 利用回数の保存先（データボリューム内）",
        )
        MONTHLY_TOKEN_LIMIT: int = Field(
            default=10_000_000,
            description="ユーザー1人あたりの月間トークン上限（High/Low/Code合算、API経由のみ対象。WebUI経由のチャットは対象外。0で無制限）",
        )
        TOKEN_USAGE_FILE: str = Field(
            default="/app/backend/data/dcc_ai_token_usage.json",
            description="月間トークン使用量(API経由分のみ)の保存先（データボリューム内）",
        )
        SYSTEM_PROMPT: str = Field(
            default=(
                "あなたは DCC（デジタルクリエイターズコミュニティ）の部員を支援する日本語アシスタント DCC AI です。"
                "DCC の部長は郡谷 考祐（こうすけ）、副部長は碇 隼匠（しゅんた）です。"
                "自身の正体や内部のAIモデルについて聞かれた場合は、必ず「私は DCC AI です」と回答してください。"
                "丁寧かつ簡潔に、正確に回答してください。"
            ),
            description="共通システムプロンプト（空で無効）",
        )
        REQUEST_TIMEOUT: int = Field(
            default=1200,
            description="API タイムアウト秒。max effortの長い推論に備えて余裕を持たせる",
        )
        OLLAMA_BASE_URL: str = Field(
            default="http://100.80.194.51:11434",
            description="画像処理用ローカルOllamaのURL",
        )
        VISION_MODEL: str = Field(
            default="qwen2.5vl:7b",
            description="画像説明用モデル名",
        )
        VISION_PROMPT: str = Field(
            default="この画像を詳細に説明し、文字があればすべて書き出してください。",
            description="画像からテキストを抽出する際のプロンプト",
        )

    def __init__(self):
        self.valves = self.Valves()

    # Open WebUI に 2 つのモデルとして出す
    def pipes(self):
        return [
            {
                "id": "dccai-high-vision",
                "name": "DCC AI High",
                "meta": {
                    "vision": True,
                    "capabilities": {
                        "citations": False
                    }
                }
            },
            {
                "id": "dccai-low-vision",
                "name": "DCC AI Low",
                "meta": {
                    "vision": True,
                    "capabilities": {
                        "citations": False
                    }
                }
            },
            {
                "id": "dccai-code",
                "name": "DCC AI Code",
                "meta": {
                    "vision": False,
                    "capabilities": {
                        "citations": False
                    }
                }
            },
        ]

    # ---- High 日次利用回数の簡易カウンタ（ファイル保存） ----
    def _load_usage(self) -> dict:
        try:
            with open(self.valves.USAGE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_usage(self, data: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.valves.USAGE_FILE), exist_ok=True)
            with open(self.valves.USAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _high_used_today(self, user_id: str) -> int:
        data = self._load_usage()
        today = date.today().isoformat()
        return int(data.get(today, {}).get(user_id, 0))

    # ---- ファイルベースカウンタの排他ロック付きread-modify-write ----
    # ロック無しだと、High/Low/Codeの同時実行(セマフォで最大数件が並行)で
    # 複数リクエストがほぼ同時にカウンタを更新した際、後勝ちで前の更新が
    # 消える(lost update)。実際にこれが原因で月間トークンカウンタが実測の
    # 3割程度しか記録されない事例が発生したため、flockで直列化する。
    def _locked_rmw(self, path: str, mutate) -> None:
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

    def _incr_high(self, user_id: str) -> None:
        today = date.today().isoformat()

        def mutate(data):
            data = {today: data.get(today, {})}  # 当日分だけ残して掃除
            data[today][user_id] = data[today].get(user_id, 0) + 1
            return data

        self._locked_rmw(self.valves.USAGE_FILE, mutate)

    # ---- 月間トークン使用量カウンタ（ファイル保存） ----
    def _load_token_usage(self) -> dict:
        try:
            with open(self.valves.TOKEN_USAGE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _month_tokens_used(self, user_id: str) -> int:
        data = self._load_token_usage()
        month = date.today().strftime("%Y-%m")
        return int(data.get(month, {}).get(user_id, 0))

    def _add_tokens(self, user_id: str, n: int) -> None:
        if n <= 0:
            return
        month = date.today().strftime("%Y-%m")

        def mutate(data):
            data = {month: data.get(month, {})}  # 当月分だけ残して掃除
            data[month][user_id] = data[month].get(user_id, 0) + n
            return data

        self._locked_rmw(self.valves.TOKEN_USAGE_FILE, mutate)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.valves.LITELLM_API_KEY}",
            "Content-Type": "application/json",
        }

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __metadata__: Optional[dict] = None,
    ):
        async def status(desc: str, done: bool = False):
            if __event_emitter__:
                await __event_emitter__(
                    {"type": "status", "data": {"description": desc, "done": done}}
                )

        if not self.valves.LITELLM_API_KEY:
            return "⚠️ 管理者へ: LITELLM_API_KEY が未設定です（Valves で設定してください）。"

        user_id = (__user__ or {}).get("id") or (__user__ or {}).get("email") or "unknown"

        # WebUI経由のリクエストは必ず chat_id を持つ(main.py の is_new_chat 判定に基づく)。
        # 素のOpenAI互換API呼び出し(chat_id/parent_id無し)はここが空になるため、
        # litellm側の集計(end_user_id)を "<user_id>:api" として分離する。
        is_api_call = not (__metadata__ or {}).get("chat_id")
        litellm_user_id = f"{user_id}:api" if is_api_call else user_id

        # ---- 月間トークン上限チェック（API経由のみ対象、High/Low/Code合算、モデル選択より前に判定） ----
        # WebUI経由のチャットはこの上限の対象外(部員の通常利用を妨げないため)。
        if is_api_call and self.valves.MONTHLY_TOKEN_LIMIT > 0:
            if self._month_tokens_used(user_id) >= self.valves.MONTHLY_TOKEN_LIMIT:
                return (
                    f"今月の DCC AI API利用上限（{self.valves.MONTHLY_TOKEN_LIMIT:,} トークン）に達しました。"
                    "来月また使えます。"
                )

        # 選択モデル（"dccai.dccai-high-vision" のように prefix が付くので末尾で判定）
        raw_model = str(body.get("model", ""))
        model_suffix = raw_model.rsplit(".", 1)[-1].lower()
        
        is_high = False
        is_code = False
        if "code" in model_suffix:
            upstream = self.valves.CODE_UPSTREAM
            is_code = True
        elif "high" in model_suffix:
            upstream = self.valves.HIGH_UPSTREAM
            is_high = True
        else:
            upstream = self.valves.LOW_UPSTREAM

        concurrency_group = "code" if is_code else "default"
        concurrency_cap = self.valves.CODE_MAX_CONCURRENCY if is_code else self.valves.MAX_CONCURRENCY

        # ---- High の 1 日上限チェック ----
        if is_high and self.valves.HIGH_DAILY_LIMIT > 0:
            if self._high_used_today(user_id) >= self.valves.HIGH_DAILY_LIMIT:
                return (
                    f"本日の DCC AI High 利用上限（{self.valves.HIGH_DAILY_LIMIT} 回）に達しました。"
                    "明日また使えます。今は DCC AI Low をご利用ください。"
                )

        # ---- システムプロンプト付与 ----
        messages = list(body.get("messages", []))
        if self.valves.SYSTEM_PROMPT and not any(
            m.get("role") == "system" for m in messages
        ):
            messages = [{"role": "system", "content": self.valves.SYSTEM_PROMPT}] + messages

        # ---- 画像入力 ----
        # High/Lowはネイティブマルチモーダルモデルなので、画像を説明文へ変換せず
        # OpenAI互換のimage_url形式のまま上流へ渡す。画像非対応のCodeへ画像が来た
        # 場合だけ、VISION_UPSTREAM (High)へリクエスト全体を転送する。
        processed_messages = []
        code_has_image = False
        for msg in messages:
            content = msg.get("content", "")
            
            text_parts = []
            image_urls = []
            
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        if url:
                            image_urls.append(url)
                    elif isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        text_parts.append(item)
            elif isinstance(content, str):
                text_parts.append(content)
                
            combined_text = "\n".join(text_parts)
            
            # Open WebUIの <attached_files> XML形式からのファイル読み込み (ローカルボリュームから直接取得)
            if "<attached_files>" in combined_text:
                file_urls = re.findall(r'<file\s+[^>]*url="([^"]+)"', combined_text)
                for f_url in file_urls:
                    matches = glob.glob(f"/app/backend/data/uploads/*{f_url}*")
                    if matches:
                        try:
                            with open(matches[0], "rb") as f:
                                b64 = base64.b64encode(f.read()).decode("utf-8")
                                mime = mimetypes.guess_type(matches[0])[0] or "image/jpeg"
                                image_urls.append(f"data:{mime};base64,{b64}")
                        except Exception:
                            pass
                # LLMが混乱しないようXMLブロックを削除
                combined_text = re.sub(r'<attached_files>.*?</attached_files>', '', combined_text, flags=re.DOTALL)
            
            if image_urls:
                content_list = []
                if combined_text:
                    content_list.append({"type": "text", "text": combined_text})
                for url in image_urls:
                    content_list.append({
                        "type": "image_url",
                        "image_url": {"url": url},
                    })
                processed_messages.append({**msg, "content": content_list})
                if is_code:
                    code_has_image = True
            else:
                processed_messages.append({**msg, "content": combined_text})

        messages = processed_messages
        if code_has_image:
            upstream = self.valves.VISION_UPSTREAM

        payload = {
            "model": upstream,
            "messages": messages,
            "stream": body.get("stream", False),
            "user": litellm_user_id,  # LiteLLM 側の利用量集計・per-user 制御用(WebUI/APIを分離)
        }
        if payload["stream"]:
            # ストリーミング末尾にusageチャンクを含めてもらう(Open WebUI管理者ダッシュボードの
            # トークン集計は、この形式のusageチャンクをSSEから拾って記録しているため必須)
            payload["stream_options"] = {"include_usage": True}
        for k in ("temperature", "top_p", "max_tokens"):
            if body.get(k) is not None:
                payload[k] = body[k]

        # ---- 同時実行セマフォ＋順番待ちの見える化（④） ----
        # グループ(default=High/Low, code=Code)ごとに別セマフォ・別カウンタを使う
        state = _get_group_state(concurrency_group, concurrency_cap)
        sem = state["sem"]
        must_wait = state["active"] >= concurrency_cap
        if must_wait:
            state["waiting"] += 1
            ahead = state["active"] + state["waiting"] - 1
            await status(f"🕒 混雑しています。順番待ち中…（あなたの前に約 {ahead} 件）", False)
        await sem.acquire()
        if must_wait:
            state["waiting"] -= 1
            await status("✅ 順番が来ました。生成を開始します。", True)
        state["active"] += 1

        url = f"{self.valves.LITELLM_BASE_URL}/chat/completions"
        headers = self._headers()
        timeout = self.valves.REQUEST_TIMEOUT

        busy_msg = (
            "ただいま大変混み合っています🙏 少し時間をおいて再送するか、"
            "DCC AI Low をお試しください。"
        )
        wait_msg = "🕒 混雑中のため順番待ちしています…（自動で再試行します）"

        # High は「成功して生成が始まったとき」だけ 1 回分としてカウント
        def count_high():
            if is_high and self.valves.HIGH_DAILY_LIMIT > 0:
                self._incr_high(user_id)

        try:
            if payload["stream"]:
                async def event_stream():
                    waited = 0.0
                    started = False
                    captured_tokens = 0
                    try:
                        async with httpx.AsyncClient(timeout=timeout) as client:
                            while True:
                                try:
                                    async with client.stream(
                                        "POST", url, json=payload, headers=headers
                                    ) as r:
                                        if r.status_code == 429:
                                            await r.aread()
                                            if waited >= self.valves.MAX_QUEUE_WAIT:
                                                yield busy_msg
                                                return
                                            back = min(3.0 + waited * 0.4, 12.0)
                                            await status(wait_msg, False)
                                            await asyncio.sleep(back)
                                            waited += back
                                            continue
                                        if r.status_code != 200:
                                            err = await r.aread()
                                            yield f"Error {r.status_code}: {err.decode(errors='ignore')}"
                                            return
                                        if not started:
                                            started = True
                                            count_high()
                                            await status("✅ 生成を開始します。", True)
                                        async for line in r.aiter_lines():
                                            if line:
                                                if line.startswith("data:"):
                                                    data_str = line[5:].strip()
                                                    if data_str and data_str != "[DONE]":
                                                        try:
                                                            chunk = json.loads(data_str)
                                                            chunk_usage = chunk.get("usage")
                                                            if chunk_usage and chunk_usage.get("total_tokens"):
                                                                captured_tokens = chunk_usage["total_tokens"]
                                                        except Exception:
                                                            pass
                                                yield line + "\n"
                                        return
                                except (
                                    httpx.ConnectError,
                                    httpx.ReadError,
                                    httpx.RemoteProtocolError,
                                ):
                                    if waited >= self.valves.MAX_QUEUE_WAIT:
                                        yield busy_msg
                                        return
                                    await status(wait_msg, False)
                                    await asyncio.sleep(3.0)
                                    waited += 3.0
                                    continue
                    finally:
                        if is_api_call and captured_tokens:
                            self._add_tokens(user_id, captured_tokens)
                        state["active"] -= 1
                        sem.release()

                return event_stream()

            # 非ストリーミング
            try:
                waited = 0.0
                async with httpx.AsyncClient(timeout=timeout) as client:
                    while True:
                        r = await client.post(url, json=payload, headers=headers)
                        if r.status_code == 429:
                            if waited >= self.valves.MAX_QUEUE_WAIT:
                                return busy_msg
                            back = min(3.0 + waited * 0.4, 12.0)
                            await status(wait_msg, False)
                            await asyncio.sleep(back)
                            waited += back
                            continue
                        if r.status_code != 200:
                            return f"Error {r.status_code}: {r.text}"
                        resp_json = r.json()
                        usage = resp_json.get("usage") or {}
                        if is_api_call and usage.get("total_tokens"):
                            self._add_tokens(user_id, usage["total_tokens"])
                        count_high()
                        await status("✅ 生成しました。", True)
                        return resp_json
            finally:
                state["active"] -= 1
                sem.release()

        except Exception as e:
            # ストリーミング開始前の例外時はここでスロット解放
            if state["active"] > 0:
                state["active"] -= 1
                try:
                    sem.release()
                except Exception:
                    pass
            return f"Error: {e}"
