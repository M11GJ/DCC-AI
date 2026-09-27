#!/bin/bash
# =============================================================================
# DCC AI バックアップスクリプト
# 実行: systemd timer dccai-backup.timer (毎日03:00) または手動
# =============================================================================
set -euo pipefail
umask 077

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/opt/dccai/backups/${DATE}"
LOG_FILE="/opt/dccai/scripts/backup.log"
KEEP_GENERATIONS=7
# 別ディスク/NASをマウントしたパスを指定する。未設定時はローカルのみ。
OFFSITE_BACKUP_DIR="${OFFSITE_BACKUP_DIR:-}"

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
PYFILE=$(mktemp "/tmp/dccai_db_backup_${DATE}.XXXXXX.py")
trap 'rm -f "${PYFILE}"' EXIT
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
trap - EXIT

log "webui.db バックアップ完了: $(du -sh "${BACKUP_DIR}/webui.db" | cut -f1)"

# 利用制限カウンターとリセット監査記録も同じ世代へ保存する。
for usage_file in \
  dcc_ai_high_usage.json \
  dcc_ai_token_usage.json \
  dcc_ai_jev_token_usage.json \
  dcc_ai_token_usage_resets.jsonl \
  dcc_ai_jev_usage.jsonl \
  dcc_ai_jev_limit_migrations.jsonl; do
  if docker exec open-webui test -f "/app/backend/data/${usage_file}"; then
    docker cp "open-webui:/app/backend/data/${usage_file}" "${BACKUP_DIR}/${usage_file}"
  fi
done
if docker exec open-webui test -d "/app/backend/data/dcc_ai_token_usage_reset_snapshots"; then
  docker cp "open-webui:/app/backend/data/dcc_ai_token_usage_reset_snapshots" \
    "${BACKUP_DIR}/dcc_ai_token_usage_reset_snapshots"
fi
if docker exec open-webui test -d "/app/backend/data/dcc_ai_jev_limit_migration_snapshots"; then
  docker cp "open-webui:/app/backend/data/dcc_ai_jev_limit_migration_snapshots" \
    "${BACKUP_DIR}/dcc_ai_jev_limit_migration_snapshots"
fi
chmod -R go-rwx "${BACKUP_DIR}"

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
chmod -R go-rwx "${BACKUP_DIR}"

# -------------------------------------------------------------------
# 4. 別ディスク/NASへコピー（設定されている場合）
# -------------------------------------------------------------------
OFFSITE_COPY_OK=false
if [ -n "${OFFSITE_BACKUP_DIR}" ]; then
  if mountpoint -q "${OFFSITE_BACKUP_DIR}"; then
    OFFSITE_DEST="${OFFSITE_BACKUP_DIR%/}/${DATE}"
    log "オフサイトコピー開始: ${OFFSITE_DEST}"
    if mkdir -p "${OFFSITE_DEST}" && cp -a "${BACKUP_DIR}/." "${OFFSITE_DEST}/"; then
      chmod -R go-rwx "${OFFSITE_DEST}"
      log "オフサイトコピー完了"
      OFFSITE_COPY_OK=true
    else
      log "WARNING: オフサイトコピーに失敗（サーバ内バックアップは正常）"
    fi
  else
    log "WARNING: OFFSITE_BACKUP_DIRはマウントポイントではありません: ${OFFSITE_BACKUP_DIR}"
  fi
else
  log "WARNING: OFFSITE_BACKUP_DIR未設定（別ディスク/NASへのコピーなし）"
fi

# -------------------------------------------------------------------
# 5. 世代管理（直近7世代を保持）
# -------------------------------------------------------------------
log "世代管理: サーバ内（保持: ${KEEP_GENERATIONS}世代）"
mapfile -t OLD_SERVER < <(find /opt/dccai/backups -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | tail -n +"$((KEEP_GENERATIONS + 1))" | cut -d' ' -f2-)
for old_dir in "${OLD_SERVER[@]}"; do
  case "${old_dir}" in
    /opt/dccai/backups/*) rm -rf -- "${old_dir}" ;;
    *) log "WARNING: 想定外の削除対象を拒否: ${old_dir}" ;;
  esac
done
[ "${#OLD_SERVER[@]}" -gt 0 ] && log "  古いバックアップを削除しました" || log "  削除対象なし"

# -------------------------------------------------------------------
# 6. 完了サマリー
# -------------------------------------------------------------------
BACKUP_SIZE=$(du -sh "${BACKUP_DIR}" | cut -f1)
log "=== バックアップ完了 ==="
log "  保存先:    ${BACKUP_DIR} (${BACKUP_SIZE})"
log "  DB:        ${BACKUP_DIR}/webui.db"
log "  オフサイト: ${OFFSITE_COPY_OK}"
