# DCC AI バックアップ・復旧手順

## バックアップの概要

| 項目 | 内容 |
|---|---|
| 実行タイミング | 毎日 03:00（systemd timer: `dccai-backup.timer`） |
| 保存先（主） | `/opt/dccai/backups/<日時>/` |
| 保存先（副） | `/mnt/c/Users/DCC05/dccai_backups/<日時>/` |
| 世代管理 | 直近7世代を保持（古いものは自動削除） |
| バックアップ内容 | `webui.db`（SQLite整合バックアップ）＋設定ファイル一式 |

> ⚠️ **残存リスク**: バックアップは単一マシン上にのみ存在します。
> ディスク故障・PC盗難・OS障害には対抗できません。
> 将来的に Mac rsync / クラウドストレージ等への退避を検討してください。

---

## 手動バックアップ

```bash
bash /opt/dccai/scripts/backup.sh
```

---

## 状態確認

```bash
# バックアップ一覧
ls -lt /opt/dccai/backups/

# 最新バックアップのログ
tail -30 /opt/dccai/scripts/backup.log

# timer の状態
systemctl status dccai-backup.timer
```

---

## 復旧手順

### 1. open-webui のデータ（チャット履歴・ユーザー・Pipe設定）を復旧

```bash
# 復旧したいバックアップのパスを確認
ls /opt/dccai/backups/

# 例: 20260720_130259 のバックアップから復旧
RESTORE_DATE="20260720_130259"
BACKUP_DB="/opt/dccai/backups/${RESTORE_DATE}/webui.db"

# 整合性確認（まずここで確認してから続行）
python3 -c "
import sqlite3
c = sqlite3.connect('${BACKUP_DB}')
tables = [r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
print('テーブル一覧:', tables)
users = c.execute('SELECT count(*) FROM user').fetchone()[0]
print('ユーザー数:', users)
c.close()
"

# open-webui を停止
cd /opt/dccai && docker compose stop open-webui

# 既存 DB を退避
docker exec open-webui cp /app/backend/data/webui.db /app/backend/data/webui.db.pre-restore 2>/dev/null || \
  docker cp \
    /opt/dccai/backups/$(ls -t /opt/dccai/backups/ | head -1)/webui.db \
    /tmp/webui.db.pre-restore

# バックアップ DB をコンテナにコピー
docker cp "${BACKUP_DB}" open-webui:/app/backend/data/webui.db

# open-webui を再起動
cd /opt/dccai && docker compose start open-webui

# 動作確認
curl -s -o /dev/null -w "%{http_code}\n" https://ai.shu-dcc.net/health
```

### 2. 設定ファイルを復旧

```bash
RESTORE_DATE="20260720_130259"
CONFIG_DIR="/opt/dccai/backups/${RESTORE_DATE}/config"

# 確認してから手動で必要なファイルをコピー
ls "${CONFIG_DIR}"
# cp "${CONFIG_DIR}/docker-compose.yml" /opt/dccai/docker-compose.yml
# cp "${CONFIG_DIR}/open-webui.env" /opt/dccai/open-webui.env
# など
```

---

## 復旧後の確認チェックリスト

- [ ] `curl https://ai.shu-dcc.net/health` → 200
- [ ] `docker compose ps` → 全コンテナ running
- [ ] 管理者アカウントでログイン可能
- [ ] 一般ユーザー視点チェック（HANDOFF.md §7）→ モデル2件表示
