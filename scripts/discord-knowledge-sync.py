#!/usr/bin/env python3
"""
discord-knowledge-sync.py (RAGチャンネル絞り込み+ニュース・フォーラム対応版)
Discord 指定チャンネルのメッセージを Open WebUI Knowledge「DCC Discord」に同期する。
このスクリプトは open-webui コンテナ内で実行される（docker exec 経由）。

必要な環境変数:
  DISCORD_BOT_TOKEN       - Discord Bot のトークン
  DISCORD_GUILD_ID        - 対象サーバーの ID（デフォルト: 1304292402386964502）
  DISCORD_INCLUDED_CHANNELS - 同期対象チャンネル名をカンマ区切り（ホワイトリスト）
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
INCLUDED_NAMES    = [n.strip() for n in os.environ.get("DISCORD_INCLUDED_CHANNELS", "").split(",") if n.strip()]
INITIAL_DAYS      = int(os.environ.get("DISCORD_INITIAL_FETCH_DAYS", "90"))
STATE_PATH        = "/app/backend/data/discord_sync_state.json"
LOG_PATH          = "/opt/dccai/scripts/discord-sync.log"
COLLECTION_NAME   = "DCC Discord"
DISCORD_API       = "https://discord.com/api/v10"
OWUI_BASE         = "http://localhost:8080"
CHUNK_CHARS       = 80_000   # 1ファイルあたりの最大文字数

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

def clean_name(name: str) -> str:
    """チャンネル名の比較用揺らぎ吸収（!や！を取り除く）"""
    return name.strip("#!！ \t").lower()

def is_channel_included(name: str) -> bool:
    """ホワイトリストに合致するかチェック（揺らぎ考慮）"""
    if not INCLUDED_NAMES:
        return True # 空なら全て対象（後方互換）
    c_name = clean_name(name)
    for inc in INCLUDED_NAMES:
        if clean_name(inc) == c_name:
            return True
    return False

def get_target_channels(client: httpx.Client) -> list[dict]:
    """同期対象のチャンネル（type=0:テキスト, 5:ニュース, 15:フォーラム）を取得"""
    channels = discord_get(client, f"/guilds/{GUILD_ID}/channels")
    result = []
    for ch in channels:
        ch_type = ch.get("type")
        if ch_type not in (0, 5, 15):
            continue
        ch_name = ch.get("name", "")
        if not is_channel_included(ch_name):
            continue
        result.append(ch)
    return result

def get_threads_for_forum(client: httpx.Client, forum_id: str) -> list[dict]:
    """フォーラム内のスレッド（アクティブ＋アーカイブ）を取得"""
    threads = []
    
    # 1. アクティブなスレッドを取得
    try:
        active_resp = discord_get(client, f"/guilds/{GUILD_ID}/threads/active")
        for th in active_resp.get("threads", []):
            if th.get("parent_id") == forum_id:
                threads.append(th)
    except Exception as e:
        log(f"    アクティブスレッドの取得失敗: {e}")
        
    # 2. 公開アーカイブスレッドを取得
    try:
        archived_resp = discord_get(client, f"/channels/{forum_id}/threads/archived/public")
        for th in archived_resp.get("threads", []):
            if th.get("parent_id") == forum_id:
                threads.append(th)
    except Exception as e:
        log(f"    アーカイブスレッドの取得失敗: {e}")
        
    # 重複排除
    seen_ids = set()
    unique_threads = []
    for th in threads:
        tid = th["id"]
        if tid not in seen_ids:
            seen_ids.add(tid)
            unique_threads.append(th)
            
    return unique_threads

def fetch_messages_since(client: httpx.Client, channel_id: str, since_iso: str) -> list[dict]:
    """since_iso 以降のメッセージを取得（古い順）"""
    since_dt = datetime.datetime.fromisoformat(since_iso)
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
                break
            raise
        if not batch:
            break
        batch.sort(key=lambda m: m["id"])
        messages.extend(batch)
        last_id = batch[-1]["id"]
        if len(batch) < 100:
            break
        time.sleep(0.5)
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
    r2 = client.post(f"{OWUI_BASE}/api/v1/knowledge/create", headers=headers,
                     json={"name": COLLECTION_NAME,
                           "description": "DCC Discord サーバーの指定チャンネルメッセージ（自動同期）"},
                     timeout=10)
    r2.raise_for_status()
    return r2.json()["id"]

def upload_and_add(client: httpx.Client, auth_headers: dict,
                   collection_id: str, filename: str, text: str) -> str | None:
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
                     json={"file_id": file_id},
                     headers=auth_headers,
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
    log(f"ホワイトリストチャンネル: {INCLUDED_NAMES if INCLUDED_NAMES else '全テキストチャンネル'}")

    state = load_state()
    since_iso = state.get("last_sync")
    if not since_iso:
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
        col_id = get_or_create_collection(client, owui_headers)
        log(f"Knowledge コレクション ID: {col_id}")

        channels = get_target_channels(client)
        log(f"同期対象チャンネル数: {len(channels)}")

        all_text_chunks = []
        current_chunk = []
        current_len = 0

        for ch in channels:
            ch_name = ch["name"]
            ch_id = ch["id"]
            ch_type = ch["type"]
            
            # チャンネルごとのテキスト
            ch_text = ""
            
            if ch_type in (0, 5):  # 通常テキスト or ニュース
                log(f"  #{ch_name} (通常/ニュース) を取得中...")
                try:
                    msgs = fetch_messages_since(client, ch_id, since_iso)
                except Exception as e:
                    log(f"    スキップ（エラー: {e}）")
                    continue
                if msgs:
                    log(f"    {len(msgs)} 件取得")
                    total_msgs += len(msgs)
                    ch_text += f"\n\n===== #{ch_name} =====\n"
                    for m in msgs:
                        ts = m.get("timestamp", "")[:19].replace("T", " ")
                        author = m.get("author", {}).get("username", "unknown")
                        content = m.get("content", "").replace("\n", " ")
                        if not content:
                            continue
                        ch_text += f"[{ts}] @{author}: {content}\n"
                else:
                    log("    新規メッセージなし")

            elif ch_type == 15:  # フォーラム
                log(f"  #{ch_name} (フォーラム) のスレッドをスキャン中...")
                threads = get_threads_for_forum(client, ch_id)
                log(f"    スレッド数: {len(threads)}")
                
                forum_text = ""
                for th in threads:
                    th_name = th["name"]
                    th_id = th["id"]
                    try:
                        th_msgs = fetch_messages_since(client, th_id, since_iso)
                    except Exception as e:
                        continue
                    if th_msgs:
                        log(f"      スレッド [{th_name}]: {len(th_msgs)} 件取得")
                        total_msgs += len(th_msgs)
                        forum_text += f"\n\n===== #{ch_name} ＞ スレッド: {th_name} =====\n"
                        for m in th_msgs:
                            ts = m.get("timestamp", "")[:19].replace("T", " ")
                            author = m.get("author", {}).get("username", "unknown")
                            content = m.get("content", "").replace("\n", " ")
                            if not content:
                                continue
                            forum_text += f"[{ts}] @{author}: {content}\n"
                ch_text = forum_text

            # チャンクサイズ管理
            if ch_text:
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
            header = (f"# DCC Discord メッセージログ (特定チャンネルのみ)\n"
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
    log(f"  次回は {sync_dt.isoformat()} 以降の差分を取得します")

if __name__ == "__main__":
    main()
