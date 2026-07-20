# DCC AI 運用ディレクトリ

**このディレクトリ `/opt/dccai` が唯一の source of truth（正）です。**

設定変更・スクリプト編集はすべてここで行ってください。

---

## ディレクトリ構成

| パス | 内容 |
|---|---|
| `docker-compose.yml` | コンテナ定義（open-webui / litellm / cloudflared / ollama） |
| `Dockerfile` | ブランディング＋env.pyパッチ入りカスタムビルド |
| `open-webui.env` | Open WebUI 設定（認証含む・秘密情報あり） |
| `.env` | APIキー各種（秘密情報）。Geminiキーの増減はここ |
| `litellm/config.yaml` | モデルルーティング（マーカー内は自動管理） |
| `scripts/` | 自動運用スクリプト |
| `branding/` | ロゴ・ファビコン・ログイン画面の注意書き |
| `backups/` | 自動バックアップ（git管理外） |
| `searxng/` | SearXNG 設定（Web検索機能） |

---

## よく使うコマンド

```bash
# コンテナ状態確認
cd /opt/dccai && docker compose ps

# コンテナ再起動
cd /opt/dccai && docker compose restart

# open-webui のみ再ビルド・再起動
cd /opt/dccai && docker compose up -d --build open-webui

# ログ確認
cd /opt/dccai && docker compose logs -f open-webui

# バックアップ手動実行
bash /opt/dccai/scripts/backup.sh

# Geminiキー死活確認
python3 /opt/dccai/scripts/gemini-key-health.py
```

---

## 重要：触れてはいけないもの

1. `open-webui.env` の `ENABLE_OAUTH_ROLE_MANAGEMENT=false` — 変えるとロールがリセット
2. `litellm/config.yaml` の `GEMINI_LOW_START〜END` マーカー内 — スクリプトが自動管理
3. Pipeのモデルid変更時は `model` テーブルと `access_grant` を必ず更新

詳細は HANDOFF.md §3「地雷」を参照。

---

## 旧作業用フォルダについて

`C:\Users\gunnk\dccai_update_readonly_20260720` は 2026-07-20 にアーカイブ化した参照専用フォルダです。
2週間の運用確認後に削除予定。**編集しないこと。**

---

## バックアップについて

- 自動バックアップ: systemd timer `dccai-backup.timer`（1日1回）
- 保存先: `/opt/dccai/backups/`（直近7世代）
- 復旧手順: `/opt/dccai/BACKUP.md` を参照
