#!/bin/bash
# =============================================================================
# dccai_pipe.py の更新内容を Open WebUI の SQLite データベースに反映するスクリプト
# =============================================================================
set -e

echo "===== [1] dccai_pipe.py をコンテナにコピー ====="
docker cp /opt/dccai/dccai_pipe.py open-webui:/tmp/dccai_pipe.py
echo "→ コピー完了"

echo ""
echo "===== [2] SQLite の function テーブルを更新 ====="
docker exec open-webui python3 -c "
import sqlite3
code = open('/tmp/dccai_pipe.py').read()
conn = sqlite3.connect('/app/backend/data/webui.db')
cursor = conn.cursor()

# 特異な文字やインデントを確認するため行数を数える
lines_count = len(code.splitlines())
print(f'読み込んだコードの行数: {lines_count} 行')

cursor.execute('UPDATE function SET content = ? WHERE id = ?', (code, 'dccai'))
conn.commit()
print(f'更新完了: {cursor.rowcount} 行更新されました')
conn.close()
"

echo ""
echo "===== [3] 一時ファイルの削除 ====="
docker exec open-webui rm -f /tmp/dccai_pipe.py
echo "→ クリーンアップ完了"

echo ""
echo "===== [4] open-webui の再起動（読み込みの反映） ====="
cd /opt/dccai
docker compose restart open-webui
echo "→ open-webui 再起動完了"

echo ""
echo "===== 完了 ====="
