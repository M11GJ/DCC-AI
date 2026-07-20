#!/bin/bash
# =============================================================================
# DCC AI バックアップスクリプト
# 実行: systemd timer dccai-backup.timer (毎日03:00) または手動
# =============================================================================
set -euo pipefail

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/opt/dccai/backups/${DATE}"
WIN_DIR="/mnt/c/Users/DCC05/dccai_backups/${DATE}"
LOG_FILE="/opt/dccai/scripts/backup.log"
KEEP_GENERATIONS=7

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

log "=== バックアップ開始: ${DATE} ==="
mkdir -p "${BACKUP_DIR}"
log "バックアップ先: ${BACKUP_DIR}"

# -------------------------------------------------------------------
# 2. SQLite バックアップ
#    Python スクリプトをファイルとしてコンテナに渡す（stdin競合を回避）
# -------------------------------------------------------------------
log "webui.db のバックアップ開始..."

# Python バックアップスクリプトを一時ファイルとして作成
PYFILE="/tmp/dccai_db_backup_${DATE}.py"
cat > "${PYFILE}" << 'PYEOF'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
try:
    src_c = sqlite3.connect(src)
    dst_c = sqlite3.connect(dst)
    src_c.backup(dst_c)
    dst_c.close()
    src_c.close()
    print(f'Backup OK: {dst}')
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
PYEOF

# コンテナにコピーして実行
docker cp "${PYFILE}" "open-webui:/tmp/dccai_db_backup.py"
docker exec open-webui python3 /tmp/dccai_db_backup.py \
    /app/backend/data/webui.db \
    "/app/backend/data/webui.db.bak_${DATE}"

# バックアップをホストに取り出す
docker cp "open-webui:/app/backend/data/webui.db.bak_${DATE}" \
    "${BACKUP_DIR}/webui.db"

# クリーンアップ
docker exec open-webui rm -f "/app/backend/data/webui.db.bak_${DATE}"
rm -f "${PYFILE}"

log "webui.db バックアップ完了: $(du -sh "${BACKUP_DIR}/webui.db" | cut -f1)"

# -------------------------------------------------------------------
# 3. 設定ファイル一式のバックアップ
# -------------------------------------------------------------------
log "設定ファイルのバックアップ開始..."
CONFIG_BACKUP="${BACKUP_DIR}/config"
mkdir -p "${CONFIG_BACKUP}"

for f in docker-compose.yml Dockerfile .env open-webui.env .env.example .gitignore README.md BACKUP.md; do
  [ -f "/opt/dccai/${f}" ] && cp "/opt/dccai/${f}" "${CONFIG_BACKUP}/" && log "  コピー: ${f}"
done
for d in litellm branding scripts searxng; do
  [ -d "/opt/dccai/${d}" ] && cp -r "/opt/dccai/${d}" "${CONFIG_BACKUP}/" && log "  コピー: ${d}/"
done
log "設定ファイルのバックアップ完了"

# -------------------------------------------------------------------
# 4. Windows 側にコピー（失敗しても警告を出して続行）
# -------------------------------------------------------------------
WIN_COPY_OK=false
if [ -d "/mnt/c/Users/DCC05" ]; then
  log "Windows側へのコピー開始: ${WIN_DIR}"
  if mkdir -p "${WIN_DIR}" && cp -r "${BACKUP_DIR}/." "${WIN_DIR}/"; then
    log "Windows側コピー完了"
    WIN_COPY_OK=true
  else
    log "WARNING: Windows側へのコピーに失敗（サーバ内バックアップは正常）"
  fi
else
  log "WARNING: /mnt/c/Users/DCC05 が見つかりません（Windows側コピースキップ）"
fi

# -------------------------------------------------------------------
# 5. 世代管理（直近7世代を保持）
# -------------------------------------------------------------------
log "世代管理: サーバ内（保持: ${KEEP_GENERATIONS}世代）"
OLD_SERVER=$(ls -dt /opt/dccai/backups/*/ 2>/dev/null | tail -n +"$((KEEP_GENERATIONS + 1))" || true)
if [ -n "${OLD_SERVER}" ]; then
  echo "${OLD_SERVER}" | xargs rm -rf
  log "  古いバックアップを削除しました"
else
  log "  削除対象なし"
fi

if [ "${WIN_COPY_OK}" = "true" ]; then
  OLD_WIN=$(ls -dt /mnt/c/Users/DCC05/dccai_backups/*/ 2>/dev/null | tail -n +"$((KEEP_GENERATIONS + 1))" || true)
  if [ -n "${OLD_WIN}" ]; then
    echo "${OLD_WIN}" | xargs rm -rf
    log "  Windows側の古いバックアップを削除しました"
  fi
fi

# -------------------------------------------------------------------
# 6. 完了サマリー
# -------------------------------------------------------------------
BACKUP_SIZE=$(du -sh "${BACKUP_DIR}" | cut -f1)
log "=== バックアップ完了 ==="
log "  保存先:    ${BACKUP_DIR} (${BACKUP_SIZE})"
log "  DB:        ${BACKUP_DIR}/webui.db"
log "  Windows:   ${WIN_COPY_OK}"
log "  [残存リスク] バックアップは単一マシン上のみ（物理障害に非対応）"
log "  [将来課題]  Mac rsync / 外付けディスク等への退避を検討してください"
