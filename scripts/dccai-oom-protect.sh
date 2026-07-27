#!/bin/bash
# =============================================================================
# DCC AI: 重要プロセスを OOM Killer の標的から外す
# =============================================================================
# 経緯: 2026-07-24 15:47 UTC、ホスト全体のメモリ逼迫でOOM Killerが発動し
# Minecraft(java)が強制終了。同時にネットワークが不安定化してCloudflare
# トンネルが約34分切断され、DCC AIが実質ダウンした。
#
# 方針: 「自動復旧するものは犠牲になってよい、しないものは守る」
#   - tailscaled : 死ぬと遠隔操作の手段が全て失われ、復旧作業自体が不可能になる
#   - dockerd/containerd : 死ぬと全コンテナが道連れ
#   - Minecraft(java)  : 自動復旧の仕組みが無く手動起動が必要(dcc05さん所有)
#   - DCC AIのコンテナ : mem_limit と restart:unless-stopped があるため対象外
#                        (cgroup内で個別に処理され、勝手に再起動してくる)
#
# oom_score_adj は プロセスごとの設定でプロセス再起動時に消えるため、
# systemd timer で定期的に再適用する。
# =============================================================================
set -u

protect() {
    local score="$1" label="$2"
    shift 2
    local pids count=0
    pids=$("$@" 2>/dev/null || true)
    for pid in $pids; do
        [ -e "/proc/$pid/oom_score_adj" ] || continue
        if echo "$score" > "/proc/$pid/oom_score_adj" 2>/dev/null; then
            count=$((count + 1))
        fi
    done
    echo "  ${label}: ${count}件に oom_score_adj=${score} を適用"
}

echo "[$(date -Is)] DCC AI OOM保護を適用"

# 遠隔アクセスの生命線(このホストはTailscale SSH経由でしか入れない)
protect -900 "tailscaled" pgrep -x tailscaled

# 全コンテナの土台
protect -800 "dockerd"    pgrep -x dockerd
protect -800 "containerd" pgrep -x containerd

# Minecraft (自動復旧なし)
protect -400 "Minecraft(java)" pgrep -f "/opt/java/current/bin/java"
