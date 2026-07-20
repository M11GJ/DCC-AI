#!/usr/bin/env python3
"""
discord-knowledge-sync.py
Discord 全チャンネルのメッセージを Open WebUI Knowledge「DCC Discord」に同期する。
このスクリプトは open-webui コンテナ内で実行される（docker exec 経由）。

必要な環境変数:
  DISCORD_BOT_TOKEN       - Discord Bot のトークン（読み取り専用権限）
  DISCORD_GUILD_ID        - 対象サーバーの ID（デフォルト: 1304292402386964502）
  DISCORD_EXCLUDED_CHANNELS - 除外チャンネル名をカンマ区切り（デフォルト: A）
  DISCORD_INITIAL_FETCH_DAYS - 初回取得日数（デフォルト: 90）
"""
import os, json, sys, io, time, datetime, sqlite3, glob
from pathlib import Path
import httpx
from open_webui.utils.auth import create_token

# ============================================================
# 設定
# ============================================================
BOT_TOKEN         = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID          = os.environ.get("DISCORD_GUILD_ID", "1304292402386964502")
EXCLUDED_NAMES    = [n.strip() for n in os.environ.get("DISCORD_EXCLUDED_CHANNELS", "A").split(",") if n.strip()]
INITIAL_DAYS      = int(os.environ.get("DISCORD_INITIAL_FETCH_DAYS", "90"))
STATE_PATH        = "/app/backend/data/discord_sync_state.json"
LOG_PATH          = "/opt/dccai/scripts/discord-sync.log"
COLLECTION_NAME   = "DCC Discord"
DISCORD_API       = "https://discord.com/api/v10"
OWUI_BASE         = "http://localhost:8080"
CHUNK_CHARS       = 80_000   # 1ファイルあたりの最大文字数（Open WebUI の制限に合わせる）

def log(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ============================================================
# Discord API ヘルパー
# ============================================================
def discord_get(client: httpx.Client, path: str, params: dict = None):
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    resp = client.get(f"{DISCORD_API}{path}", headers=headers,
                      params=params, timeout=30)
    if resp.status_code == 429:
        retry_after = float(resp.json().get("retry_after", 5))
        log(f"  レートリミット: {retry_after}s 待機...")
        time.sleep(retry_after + 0.5)
        resp = client.get(f"{DISCORD_API}{path}", headers=headers,
                          params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_text_channels(client: httpx.Client) -> list[dict]:
    """テキストチャンネル（type=0）のみ取得し、除外チャンネルを除く"""
    channels = discord_get(client, f"/guilds/{GUILD_ID}/channels")
    result = []
    for ch in channels:
        if ch.get("type") != 0:          # テキストチャンネルのみ
            continue
        if ch.get("name") in EXCLUDED_NAMES:
            log(f"  除外チャンネル: #{ch['name']}")
            continue
        result.append(ch)
    return result

def fetch_messages_since(client: httpx.Client, channel_id: str, since_iso: str) -> list[dict]:
    """since_iso 以降のメッセージを全件取得（古い順）"""
    since_dt = datetime.datetime.fromisoformat(since_iso)
    # Discord の Snowflake ID に変換（since 以降のメッセージを取得するため）
    # Discord epoch: 2015-01-01T00:00:00Z
    discord_epoch = 1420070400000
    ms = int(since_dt.timestamp() * 1000) - discord_epoch
    after_id = str(ms << 22) if ms > 0 else "0"

    messages = []
    last_id = after_id
    while True:
        params = {"limit": 100, "after": last_id}
        try:
            batch = discord_get(client, f"/channels/{channel_id}/messages", params)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                # アクセス権なし or チャンネルなし → スキップ
                break
            raise
        if not batch:
            break
        # 古い順に並べ替え
        batch.sort(key=lambda m: m["id"])
        messages.extend(batch)
        last_id = batch[-1]["id"]
        if len(batch) < 100:
            break
        time.sleep(0.5)  # レートリミット対策
    return messages

# ============================================================
# Open WebUI API ヘルパー
# ============================================================
def get_owui_token() -> str:
    db = glob.glob("/app/backend/data/webui.db")[0]
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    admin = c.execute("SELECT id FROM user WHERE role='admin' LIMIT 1").fetchone()
    return create_token(data={"id": admin["id"]})

def get_or_create_collection(client: httpx.Client, headers: dict) -> str:
    r = client.get(f"{OWUI_BASE}/api/v1/knowledge/", headers=headers, timeout=10)
    for item in r.json().get("items", []):
        if isinstance(item, dict) and item.get("name") == COLLECTION_NAME:
            return item["id"]
    # 作成
    r2 = client.post(f"{OWUI_BASE}/api/v1/knowledge/create", headers=headers,
                     json={"name": COLLECTION_NAME,
                           "description": "DCC Discord サーバーの全チャンネルメッセージ（自動同期）"},
                     timeout=10)
    r2.raise_for_status()
    return r2.json()["id"]

def upload_and_add(client: httpx.Client, auth_headers: dict,
                   collection_id: str, filename: str, text: str) -> str | None:
    """テキストをファイルとしてアップロードし Knowledge コレクションに追加"""
    content = text.encode("utf-8")
    files = {"file": (filename, io.BytesIO(content), "text/plain")}
    r = client.post(f"{OWUI_BASE}/api/v1/files/",
                    headers={"Authorization": auth_headers["Authorization"]},
                    files=files, timeout=60)
    if r.status_code != 200:
        log(f"  ファイルアップロード失敗: HTTP {r.status_code}")
        return None
    file_id = r.json()["id"]

    r2 = client.post(f"{OWUI_BASE}/api/v1/knowledge/{collection_id}/file/add",
                     headers=auth_headers,
                     json={"file_id": file_id},
                     timeout=120)
    if r2.status_code != 200:
        log(f"  Knowledge 追加失敗: HTTP {r2.status_code} {r2.text[:200]}")
        return None
    return file_id

# ============================================================
# 状態管理
# ============================================================
def load_state() -> dict:
    try:
        return json.loads(Path(STATE_PATH).read_text())
    except Exception:
        return {}

def save_state(state: dict):
    Path(STATE_PATH).write_text(json.dumps(state, ensure_ascii=False, indent=2))

# ============================================================
# メイン
# ============================================================
def main():
    if not BOT_TOKEN:
        log("ERROR: DISCORD_BOT_TOKEN が設定されていません")
        sys.exit(1)

    log("=== Discord → Knowledge 同期開始 ===")
    log(f"除外チャンネル: {EXCLUDED_NAMES}")

    state = load_state()
    since_iso = state.get("last_sync")
    if not since_iso:
        # 初回: INITIAL_DAYS 日前から
        since_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=INITIAL_DAYS)
        since_iso = since_dt.isoformat()
        log(f"初回同期: {since_iso} 以降のメッセージを取得")
    else:
        log(f"差分同期: {since_iso} 以降のメッセージを取得")

    owui_token = get_owui_token()
    owui_headers = {"Authorization": f"Bearer {owui_token}", "Content-Type": "application/json"}

    total_msgs = 0
    sync_dt = datetime.datetime.now(datetime.timezone.utc)

    with httpx.Client(timeout=30) as client:
        # Knowledge コレクション確認/作成
        col_id = get_or_create_collection(client, owui_headers)
        log(f"Knowledge コレクション ID: {col_id}")

        # チャンネル一覧取得
        channels = get_text_channels(client)
        log(f"同期対象チャンネル数: {len(channels)}")

        # チャンネルごとにメッセージ取得
        all_text_chunks = []
        current_chunk = []
        current_len = 0

        for ch in channels:
            ch_name = ch["name"]
            ch_id = ch["id"]
            log(f"  #{ch_name} を取得中...")
            try:
                msgs = fetch_messages_since(client, ch_id, since_iso)
            except Exception as e:
                log(f"    スキップ（エラー: {e}）")
                continue

            if not msgs:
                log(f"    新規メッセージなし")
                continue

            log(f"    {len(msgs)} 件取得")
            total_msgs += len(msgs)

            # テキスト整形
            ch_header = f"\n\n===== #{ch_name} =====\n"
            ch_text = ch_header
            for m in msgs:
                ts = m.get("timestamp", "")[:19].replace("T", " ")
                author = m.get("author", {}).get("username", "unknown")
                content = m.get("content", "").replace("\n", " ")
                if not content:
                    continue
                ch_text += f"[{ts}] @{author}: {content}\n"

            # チャンクサイズ管理
            if current_len + len(ch_text) > CHUNK_CHARS:
                all_text_chunks.append("".join(current_chunk))
                current_chunk = []
                current_len = 0
            current_chunk.append(ch_text)
            current_len += len(ch_text)

        if current_chunk:
            all_text_chunks.append("".join(current_chunk))

        # Knowledge にアップロード
        date_str = sync_dt.strftime("%Y%m%d_%H%M%S")
        uploaded = 0
        for i, chunk in enumerate(all_text_chunks):
            if not chunk.strip():
                continue
            fname = f"discord_{date_str}_part{i+1}.txt"
            header = (f"# DCC Discord メッセージログ\n"
                      f"# 取得日時: {sync_dt.isoformat()}\n"
                      f"# 取得範囲: {since_iso} 以降\n"
                      f"# パート: {i+1}/{len(all_text_chunks)}\n\n")
            fid = upload_and_add(client, owui_headers, col_id, fname, header + chunk)
            if fid:
                uploaded += 1
                log(f"  アップロード完了: {fname}")

    # 状態保存
    state["last_sync"] = sync_dt.isoformat()
    state["collection_id"] = col_id
    save_state(state)

    log(f"=== 同期完了 ===")
    log(f"  合計メッセージ: {total_msgs} 件")
    log(f"  アップロードファイル: {uploaded} 件")
    log(f"  次回は {since_iso} 以降の差分を取得します")

if __name__ == "__main__":
    main()
