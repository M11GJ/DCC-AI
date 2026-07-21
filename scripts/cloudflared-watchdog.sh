#!/bin/bash
# =============================================================================
# dccai-cloudflared 監視・自動復旧スクリプト
# 実行: systemd timer dccai-cloudflared-watchdog.timer (5分毎)
# 公開URL経由でヘルスチェックし、失敗していたらコンテナを再起動する
#
# cloudflaredはトンネル内に複数コネクション(レプリカ)を張るため、
# 一部だけが死んでいる状態だと1回のチェックでは見逃す(生きている
# コネクションに偶然当たる)。そのため複数回試行し、過半数失敗で
# 異常とみなす。
# =============================================================================
set -uo pipefail

URL="https://ai.shu-dcc.net/api/config"
LOG_FILE="/opt/dccai/scripts/cloudflared-watchdog.log"
CONTAINER="dccai-cloudflared"
ATTEMPTS=6
FAIL_THRESHOLD=3   # ATTEMPTS中これ以上失敗したら異常とみなす

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

fail_count=0
codes=()
for i in $(seq 1 "${ATTEMPTS}"); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "${URL}")
  codes+=("${CODE}")
  if [ "${CODE}" != "200" ]; then
    fail_count=$((fail_count + 1))
  fi
  sleep 1
done

if [ "${fail_count}" -lt "${FAIL_THRESHOLD}" ]; then
  exit 0
fi

log "異常検知: ${ATTEMPTS}回中${fail_count}回失敗 (${codes[*]})。${CONTAINER} を再起動します"
docker restart "${CONTAINER}" >> "${LOG_FILE}" 2>&1

sleep 8
CODE2=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${URL}")
log "再起動後: ${URL} -> ${CODE2}"
