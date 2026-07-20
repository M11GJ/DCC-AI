"""
title: DCC AI
author: DCC
version: 1.0.0
license: MIT
description: DCC部員向け DCC AI High/Low。順番待ちUI・High日次上限つきで LiteLLM 経由 featherless に中継。
requirements: httpx
"""
import asyncio
import json
import os
import re
import base64
import glob
from datetime import date
from typing import Optional, Callable, Awaitable, Any

import httpx
from pydantic import BaseModel, Field

# --- プロセス内で共有する同時実行の状態（順番待ち表示のため自前カウント） ---
_STATE = {"sem": None, "cap": None, "active": 0, "waiting": 0}


def _get_sem(cap: int) -> asyncio.Semaphore:
    if _STATE["sem"] is None or _STATE["cap"] != cap:
        _STATE["sem"] = asyncio.Semaphore(cap)
        _STATE["cap"] = cap
    return _STATE["sem"]


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
        MAX_CONCURRENCY: int = Field(
            default=3,
            description="同時に生成する上限。featherless の同時接続数(4)より少し下げて枠に余裕を持たせる",
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
        SYSTEM_PROMPT: str = Field(
            default=(
                "あなたは DCC（デジタルクリエイターズコミュニティ）の部員を支援する日本語アシスタント DCC AI です。"
                "DCC の部長は郡谷 考祐（こうすけ）、副部長は碇 隼匠（しゅんた）です。"
                "自身の正体や内部のAIモデルについて聞かれた場合は、必ず「私は DCC AI です」と回答してください。"
                "丁寧かつ簡潔に、正確に回答してください。"
            ),
            description="共通システムプロンプト（空で無効）",
        )
        REQUEST_TIMEOUT: int = Field(default=300, description="API タイムアウト秒")
        OLLAMA_BASE_URL: str = Field(
            default="http://ollama:11434",
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

    def _incr_high(self, user_id: str) -> None:
        data = self._load_usage()
        today = date.today().isoformat()
        data = {today: data.get(today, {})}  # 当日分だけ残して掃除
        data[today][user_id] = data[today].get(user_id, 0) + 1
        self._save_usage(data)

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
    ):
        async def status(desc: str, done: bool = False):
            if __event_emitter__:
                await __event_emitter__(
                    {"type": "status", "data": {"description": desc, "done": done}}
                )

        if not self.valves.LITELLM_API_KEY:
            return "⚠️ 管理者へ: LITELLM_API_KEY が未設定です（Valves で設定してください）。"

        user_id = (__user__ or {}).get("id") or (__user__ or {}).get("email") or "unknown"

        # 選択モデル（"dccai.dccai-high-vision" のように prefix が付くので末尾で判定）
        raw_model = str(body.get("model", ""))
        model_suffix = raw_model.rsplit(".", 1)[-1].lower()
        
        is_high = False
        if "high" in model_suffix:
            upstream = self.valves.HIGH_UPSTREAM
            is_high = True
        else:
            upstream = self.valves.LOW_UPSTREAM

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

        # ---- 画像インターセプト (ローカルOllamaでテキスト化) ----
        processed_messages = []
        for msg in messages:
            content = msg.get("content", "")
            
            text_parts = []
            images_to_process = []
            
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        if url.startswith("data:image"):
                            base64_data = url.split(",", 1)[-1]
                            images_to_process.append(base64_data)
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
                                images_to_process.append(b64)
                        except Exception:
                            pass
                # LLMが混乱しないようXMLブロックを削除
                combined_text = re.sub(r'<attached_files>.*?</attached_files>', '', combined_text, flags=re.DOTALL)
            
            if images_to_process:
                if is_high:
                    await status("👁️ 画像を処理しています...", False)
                    vision_text = ""
                    try:
                        async with httpx.AsyncClient(timeout=120) as client:
                            # 1. まず Gemini (Low_UPSTREAM) に投げて画像解析を試みる
                            gemini_content = [{"type": "text", "text": self.valves.VISION_PROMPT}]
                            for b64 in images_to_process:
                                gemini_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
                            
                            gemini_payload = {
                                "model": self.valves.LOW_UPSTREAM,
                                "messages": [{"role": "user", "content": gemini_content}],
                                "stream": False
                            }
                            resp = await client.post(
                                f"{self.valves.LITELLM_BASE_URL}/chat/completions",
                                json=gemini_payload,
                                headers=self._headers()
                            )
                            if resp.status_code == 200:
                                vision_text = resp.json()["choices"][0]["message"]["content"]
                            else:
                                raise Exception(f"Gemini API returned {resp.status_code}")
                    except Exception as e:
                        # 2. 失敗したらローカルOllamaへ予備（フォールバック）として投げる
                        await status("⚠️ 予備の画像処理システムへ切り替えています...", False)
                        try:
                            async with httpx.AsyncClient(timeout=120) as client:
                                ollama_payload = {
                                    "model": self.valves.VISION_MODEL,
                                    "prompt": self.valves.VISION_PROMPT,
                                    "images": images_to_process,
                                    "stream": False
                                }
                                resp = await client.post(f"{self.valves.OLLAMA_BASE_URL}/api/generate", json=ollama_payload)
                                if resp.status_code == 200:
                                    vision_text = resp.json().get("response", "")
                                else:
                                    return f"🔧 【ローカルOllama エラー】\n画像のテキスト化中にエラーが返されました。\nステータス: {resp.status_code}\n内容: {resp.text}"
                        except Exception as e2:
                            return f"🔧 【画像処理エラー】\nGeminiおよびローカルOllamaの両方で画像解析に失敗しました。\n詳細: {e2}"
                    
                    combined_text += f"\n\n[添付画像の説明: {vision_text}]\n"
                    processed_messages.append({**msg, "content": combined_text})
                else:
                    # Low (Gemini) はマルチモーダルネイティブ対応なのでOllamaを使わずそのまま送る
                    content_list = [{"type": "text", "text": combined_text}]
                    for b64 in images_to_process:
                        content_list.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                        })
                    processed_messages.append({**msg, "content": content_list})
            else:
                processed_messages.append({**msg, "content": combined_text})
        
        messages = processed_messages

        payload = {
            "model": upstream,
            "messages": messages,
            "stream": body.get("stream", False),
            "user": user_id,  # LiteLLM 側の利用量集計・per-user 制御用
        }
        for k in ("temperature", "top_p", "max_tokens"):
            if body.get(k) is not None:
                payload[k] = body[k]

        # ---- 同時実行セマフォ＋順番待ちの見える化（④） ----
        sem = _get_sem(self.valves.MAX_CONCURRENCY)
        must_wait = _STATE["active"] >= self.valves.MAX_CONCURRENCY
        if must_wait:
            _STATE["waiting"] += 1
            ahead = _STATE["active"] + _STATE["waiting"] - 1
            await status(f"🕒 混雑しています。順番待ち中…（あなたの前に約 {ahead} 件）", False)
        await sem.acquire()
        if must_wait:
            _STATE["waiting"] -= 1
            await status("✅ 順番が来ました。生成を開始します。", True)
        _STATE["active"] += 1

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
                        _STATE["active"] -= 1
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
                        count_high()
                        await status("✅ 生成しました。", True)
                        return r.json()
            finally:
                _STATE["active"] -= 1
                sem.release()

        except Exception as e:
            # ストリーミング開始前の例外時はここでスロット解放
            if _STATE["active"] > 0:
                _STATE["active"] -= 1
                try:
                    sem.release()
                except Exception:
                    pass
            return f"Error: {e}"
