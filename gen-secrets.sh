#!/usr/bin/env bash
# ランダムなシークレット値を生成して表示する（.env / open-webui.env に貼る用）
set -euo pipefail
echo "LITELLM_MASTER_KEY = sk-dcc-$(openssl rand -hex 24)"
echo "WEBUI_SECRET_KEY   = $(openssl rand -hex 32)"
echo "COOKIE_KEY(Worker) = $(openssl rand -hex 32)"
