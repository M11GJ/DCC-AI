#!/bin/bash
# Apply persistent Open WebUI RAG/model settings, keeping a recoverable DB copy.
set -euo pipefail

cd /opt/dccai

backup_dir="/opt/dccai/backups/manual-open-webui-rag-$(date +%Y%m%d_%H%M%S)"
mkdir -p "$backup_dir"
docker exec open-webui python3 -c 'import sqlite3; src=sqlite3.connect("/app/backend/data/webui.db"); dst=sqlite3.connect("/tmp/webui.db.backup"); src.backup(dst); dst.close(); src.close()'
docker cp open-webui:/tmp/webui.db.backup "$backup_dir/webui.db"
docker exec open-webui rm -f /tmp/webui.db.backup

docker cp /opt/dccai/scripts/configure_open_webui_rag.py open-webui:/tmp/configure_open_webui_rag.py
docker exec open-webui python3 /tmp/configure_open_webui_rag.py
docker exec open-webui rm -f /tmp/configure_open_webui_rag.py
docker compose restart open-webui

echo "Open WebUI RAG settings applied. Backup: $backup_dir/webui.db"
