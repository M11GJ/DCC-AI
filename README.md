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
| `.env` | APIキー各種（秘密情報）。停止中のGeminiキーも将来の復活用に保持 |
| `litellm/config.yaml` | モデルルーティング（Geminiブロックは現在コメントアウト） |
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

# Geminiは現在一時停止中。gemini-key-health.pyは再有効化するまで実行しない
```

---

## 重要：触れてはいけないもの

1. `open-webui.env` の `ENABLE_OAUTH_ROLE_MANAGEMENT=false` — 変えるとロールがリセット
2. Geminiを復活させる場合は、`litellm/config.yaml`、`docker-compose.yml`、fallbacksを同時に戻してからヘルスチェックを再開する
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

---

## ホスト情報(2026-07-20 移行)

現在の本番ホスト: `desktop-1f999hf`(`ssh dcc05@desktop-1f999hf`、WSL2 Ubuntu 26.04、GPU: RTX 4060）

旧ホスト `omen17`(`ssh gunnk@100.90.136.25`、WSL2 Ubuntu-24.04）は**ロールバック用に停止状態のまま温存**中。`/opt/dccai` はそのまま残っており、`docker compose up -d` で即復旧可能。運用が安定したら削除を検討。

cloudflaredはトークン方式のリモート管理トンネルのため、ホストを跨いでもDNS変更は不要（起動しているホストへ自動的にルーティングされる）。**両ホストで同時に起動しないこと**(セッション不整合の原因になる)。

移行時、新ホストの`cloudflared`コンテナ名は既存の別コンテナと衝突したため `dccai-cloudflared` にリネームしている。
