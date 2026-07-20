#!/bin/bash
# =============================================================================
# DCC AI Discord Knowledge 同期ラッパースクリプト
# systemd の dccai-discord-sync.timer から 30 分ごとに呼ばれる
# =============================================================================
set -euo pipefail

LOG_FILE="/opt/dccai/scripts/discord-sync.log"
SYNC_PY="/opt/dccai/scripts/discord-knowledge-sync.py"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

log "=== discord-knowledge-sync.sh 開始 ==="

# .env から Discord Bot Token を取得（秘密情報なので直接読む）
if [ ! -f /opt/dccai/.env ]; then
    log "ERROR: /opt/dccai/.env が見つかりません"
    exit 1
fi

DISCORD_BOT_TOKEN=$(grep -E '^DISCORD_BOT_TOKEN=' /opt/dccai/.env | cut -d= -f2- | tr -d '"'"'" | head -1)
DISCORD_GUILD_ID=$(grep -E '^DISCORD_GUILD_ID=' /opt/dccai/.env | cut -d= -f2- | tr -d '"'"'" | head -1)
DISCORD_INCLUDED_CHANNELS=$(grep -E '^DISCORD_INCLUDED_CHANNELS=' /opt/dccai/.env | cut -d= -f2- | tr -d '"'"'" | head -1 || echo "")
DISCORD_INITIAL_FETCH_DAYS=$(grep -E '^DISCORD_INITIAL_FETCH_DAYS=' /opt/dccai/.env | cut -d= -f2- | tr -d '"'"'" | head -1)

if [ -z "${DISCORD_BOT_TOKEN}" ]; then
    log "ERROR: DISCORD_BOT_TOKEN が .env に設定されていません"
    log "  /opt/dccai/.env に DISCORD_BOT_TOKEN=<ボットトークン> を追加してください"
    exit 1
fi

# Python スクリプトをコンテナにコピーして実行
log "スクリプトをコンテナにコピー..."
docker cp "${SYNC_PY}" open-webui:/tmp/discord-knowledge-sync.py

log "同期開始..."
docker exec \
    -e PYTHONPATH=/app/backend \
    -e DISCORD_BOT_TOKEN="${DISCORD_BOT_TOKEN}" \
    -e DISCORD_GUILD_ID="${DISCORD_GUILD_ID:-1304292402386964502}" \
    -e DISCORD_INCLUDED_CHANNELS="${DISCORD_INCLUDED_CHANNELS:-}" \
    -e DISCORD_INITIAL_FETCH_DAYS="${DISCORD_INITIAL_FETCH_DAYS:-90}" \
    open-webui \
    python3 /tmp/discord-knowledge-sync.py

log "=== 完了 ==="
