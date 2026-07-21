#!/bin/bash
# =============================================================================
# dccai-cloudflared 監視・自動復旧スクリプト
# 実行: systemd timer dccai-cloudflared-watchdog.timer (5分毎)
# 公開URL経由でヘルスチェックし、失敗していたらコンテナを再起動する
# =============================================================================
set -uo pipefail

URL="https://ai.shu-dcc.net/api/config"
LOG_FILE="/opt/dccai/scripts/cloudflared-watchdog.log"
CONTAINER="dccai-cloudflared"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${URL}")

if [ "${CODE}" = "200" ]; then
  exit 0
fi

log "異常検知: ${URL} -> ${CODE}。${CONTAINER} を再起動します"
docker restart "${CONTAINER}" >> "${LOG_FILE}" 2>&1

sleep 8
CODE2=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${URL}")
log "再起動後: ${URL} -> ${CODE2}"
