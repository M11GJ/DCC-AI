# DCC AI 仕様書(2026-08-01時点)

DCC(デジタルクリエイターズコミュニティ)部員専用AIチャットサービス。本ドキュメントは現在の構成を体系的にまとめた仕様書であり、別セッション/別AIへの引き継ぎを目的とする。時系列の作業ログではなく「今どうなっているか」を記述する。より詳細な経緯・トラブルシューティングの記録は`/opt/dccai/CLOUDFLARE-TUNNEL-INVESTIGATION.md`と`/opt/dccai/HANDOFF-2026-07-23.md`を参照。

---

## 1. 概要

| 項目 | 内容 |
|---|---|
| 名称 | DCC AI |
| 対象 | DCC部員限定(Discord経由でギルド在籍を確認) |
| 公開URL(チャットUI) | https://ai.shu-dcc.net |
| 公開URL(使用量ページ) | https://usage.shu-dcc.net |
| 公開URL(Responses API) | https://responses.shu-dcc.net |
| 提供モデル | High(DeepSeek V4 Flash) / Low(DeepSeek V4 Pro) / Code(GLM-5.2) の3系統、各系統に自動フォールバック先あり |
| ホスト | `ssh gunk@100.100.252.10`(通称n200、Linux実機。sudoパスワードレス、sshd非稼働でTailscale SSHのみ) |
| アプリ本体 | `/opt/dccai`(git管理、root所有) |

---

## 2. インフラ構成

### 2.1 ホスト履歴
1. omen17(2026-07-19構築時) → 2. desktop-1f999hf(dcc05さん私物WSL2機、2026-07-20) → 3. **n200(現行、2026-07-25頃)**

旧ホストのうち`desktop-1f999hf`(Tailscale IP `100.80.194.51`)は**今も現役**で、GPU(RTX 4060)提供元として`dccai_pipe.py`の`OLLAMA_BASE_URL`が向いている(画像キャプションのフォールバック用)。omen17は生死未確認。

### 2.2 n200のコンテナ構成

```
open-webui        (:3000→8080)  チャットUI本体(v0.11.0)、custom build
litellm           (:4000, 127.0.0.1限定) モデルルーティング・spend記録
dccai-postgres    (:5432, 内部のみ)      litellmのspend/token永続化
dccai-cloudflared (network_mode: host)   Cloudflare Tunnel、--protocol http2
dccai-dashboard   (:3001)                LiteLLM Prometheusメトリクスの簡易ダッシュボード
dccai-usage       (:3002)                個人別使用量ページ(Discord OAuth)
dccai-responses   (:3003)                Responses APIゲートウェイ
searxng           (内部のみ)             Web検索
```

ollamaはn200には無い(GPU非搭載のため)。`docker-compose.yml`内にコメントアウトで経緯を記載。

### 2.3 Cloudflare Tunnel

トークン方式のリモート管理トンネル(Cloudflare Zero Trust dashboardで管理)。**DNS/ルーティングはCloudflare側で完結するため、ホストを跨ぐ移行でもトークンさえ同じなら設定変更不要**。現在3つのPublic Hostnameが同一トンネルに紐づく:

| ホスト名 | 転送先 |
|---|---|
| ai.shu-dcc.net | localhost:3000 (open-webui) |
| usage.shu-dcc.net | localhost:3002 (dccai-usage) |
| responses.shu-dcc.net | localhost:3003 (dccai-responses) |

Cloudflare API tokenは無く、Public Hostnameの追加はダッシュボードでの手動作業が必要(AI側から自動化不可)。

---

## 3. モデル構成(litellm)

`litellm/config.yaml`で3系統+フォールバックを定義。**外部向けmodel_name(`dccai-high`/`dccai-low`/`dccai-code`)は固定し、中身(実際に呼ぶモデル)だけを差し替える設計**。

| 系統 | 1st (litellm model_name) | 実体 | フォールバック順 |
|---|---|---|---|
| High | `dccai-high` | `deepseek/deepseek-v4-flash`(0731版、公式ベンチマークでv4-proより高性能) | `dccai-high-legacy`(Cerebras llama-3.3-70b → DeepSeek-V3 featherless) → `dccai-low` → `dccai-low-legacy` → `dccai-backup-low` |
| Low | `dccai-low` | `deepseek/deepseek-v4-pro` | `dccai-low-legacy`(Gemini 3.1-flash-lite ×4キー、`gemini-key-health.py`が自動管理) → `dccai-backup-low` |
| Code | `dccai-code` | `openai/zai-org/GLM-5.2`(Featherless) | `dccai-high` |

**注意**: High/Lowの「名前」と「実際のグレード」が逆転している(2026-08-01のベンチマーク結果を受けた変更)。`model_info.id`ラベルで実体を確認できる。

DeepSeek V4系は既定でreasoning(思考過程)を出力するため、`max_tokens`が小さいと本文前に打ち切られる。

Open WebUI上の公開モデルID(Pipe経由):`dccai.dccai-high-vision` / `dccai.dccai-low-vision` / `dccai.dccai-code`

---

## 4. Pipe(`dccai_pipe.py`)

Open WebUIのカスタムFunction(Pipe)。litellmへの中継に加え、以下のポリシーを一元的に適用する:

- **モデル選択**: 公開model_idの末尾サフィックス("high"/"low"/"code")で内部litellmモデル名へ変換
- **同時実行制御**: グループ別セマフォ(`_STATE_BY_GROUP`)。High/Lowは`MAX_CONCURRENCY`(既定3)、CodeはFeatherlessの同時接続制約が厳しいため`CODE_MAX_CONCURRENCY`(既定1)で別枠管理
- **High日次上限**: `HIGH_DAILY_LIMIT`(既定20回/日)、`USAGE_FILE`にflock付きread-modify-writeで記録
- **月間トークン上限**: `MONTHLY_TOKEN_LIMIT`(既定1000万トークン/月)。**API経由の呼び出しのみが対象、WebUIチャットは対象外**。`TOKEN_USAGE_FILE`(`/app/backend/data/dcc_ai_token_usage.json`)にflock付きで記録・判定
  - WebUI呼び出しとAPI呼び出しの区別は`__metadata__.chat_id`の有無で判定(`is_api_call`)
  - litellm側の集計用`user`フィールドも`<user_id>:api`(API)/`<user_id>`(WebUI)で分離
- **画像処理**: Highは画像をまずGemini(`VISION_UPSTREAM`=`dccai-low-legacy`)に投げて説明文化しテキストとして本モデルに渡す。失敗時はローカルOllama(`desktop-1f999hf`のGPU)にフォールバック。Lowは画像添付時のみ`VISION_UPSTREAM`に直接ネイティブマルチモーダルで送る(dccai-lowの実体がDeepSeek=画像非対応のため)
- **ストリーミング**: `stream_options.include_usage`を要求し、SSE末尾のusageチャンクからトークン数を取得(Open WebUI管理者ダッシュボードのトークン表示・月間カウンタ更新の両方に必要)

反映方法: `dccai_pipe.py`を編集後、`bash /opt/dccai/scripts/sync_pipe_to_db.sh`でOpen WebUIのSQLite(`function`テーブル)へ書き込み+open-webui再起動が必要(ファイルを置くだけでは反映されない)。

---

## 5. 外部API

DCC部員はOpen WebUIで発行したAPIキー(Settings > Account > API keys)を使って外部からアクセス可能。**Chat Completions APIとResponses APIは同じ月間トークン枠を共有する**。

### 5.1 Chat Completions API(Pipe経由)
- Endpoint: `https://ai.shu-dcc.net/api/chat/completions`(OpenAI SDK利用時は base_url に `https://ai.shu-dcc.net/api`)
- モデルID: `dccai.dccai-high-vision` / `dccai.dccai-low-vision` / `dccai.dccai-code`
- 上記4節のPipeポリシーがすべて適用される

### 5.2 Responses API(専用ゲートウェイ経由)
- Endpoint: `https://responses.shu-dcc.net/v1/responses`
- 実体は`scripts/responses_gateway.py`(新設サービス、python:3.11-slim + httpx、コンテナ`dccai-responses`)
- **背景**: litellm自体は`/v1/responses`に標準対応済みだが、Open WebUI本体の`/responses`ルートは「Connection」モデル専用でPipeモデルには使えない。PipeはChat Completions専用のフックしか持たないため、Responses API対応は別サービスとして実装した
- 認証: Open WebUIの`api_key`テーブル(`webui.db`)を直接読んでBearerトークンを検証(Open WebUI自身と同じ検証方法)
- Pipeとの整合性: モデルID解決・Code専用同時実行制限(1)・月間トークン上限判定はPipeと**同じロジック・同じファイル**(`TOKEN_USAGE_FILE`を`open-webui`名前付きボリューム経由で共有、flock方式も同一)を使うため、二重管理にならず正しく合算される
- ストリーミング対応(`response.completed`イベントのusageを検出)
- **ツール呼び出し互換層**: Responses API標準のfunction toolと、Codex系クライアントが送るChat Completions型のネスト形式をどちらも受理してLiteLLM向けに正規化する。DeepSeek V4のDSMLが`output_text`へ漏れた場合は公式DSML形式を解析し、`function_call`/`custom_tool_call`へ変換する
- ツール付きストリーミングはDSML文字列をクライアントへ先に流さないため、上流の完了応答をバッファしてから標準Responses SSEイベントを再生成する。そのため、ツールを含まない通常ストリームと異なり最初のイベントまで上流生成時間ぶん待つ

---

## 6. 認証ゲート

- Discord OAuth → 独自Cloudflare Worker(`dccai-oidc-worker.gunn-kousuke.workers.dev`)でOIDCに変換
- Workerデプロイ元: Macの`/Users/kosukeguntani/Downloads/dccai/worker`(`npx wrangler deploy`)
- **ギルド限定判定はWorkerの`/callback`で行い、非所属にはトークン自体を発行しない**(Open WebUI側のロール管理は使わない設計。`ENABLE_OAUTH_ROLE_MANAGEMENT=true`にするとログインの度にadminがuserへ降格する既知の罠があるため`false`固定)

---

## 7. 使用量ページ(`usage_page.py`、dccai-usageコンテナ)

Discord OAuth(独自、Open WebUIとは別実装)でログインし、以下を提供:

- **個人ページ(`/`)**: 全期間合計・WebUI/API内訳・モデル別内訳・**今月のAPI利用状況(月間上限に対する%バー、70%で黄・95%で赤)**
- **ランキング(`/ranking`)**: 今月の消費ランキング(WebUI+API合算)、全体の合計ランキング
- **管理者ページ(`/admin`、role=admin限定)**: 日別ランキング(JST暦日)、**今月のAPI利用状況を全ユーザー分バー表示**、モデル別グラフ・テーブル、WebUI/API内訳テーブル

**データソース**: litellmの`/spend/logs` REST APIは`limit`/`end_user_id`フィルタが機能せず(既知の欠陥、原因未特定)、全件(数万行・90MB超)を返し9〜14秒かかっていたため、**同じdocker network上のPostgres(`LiteLLM_SpendLogs`テーブル)へ`psycopg2`で直接SQL問い合わせ**する方式に変更済み(0.1秒未満)。月次集計は**JST暦月**で統一(日別ランキングもJSTのため)。

**罠**: SQLに`LIKE '%...'`のようなリテラル`%`を含める場合、`psycopg2`のプレースホルダー解析と衝突するため`%%`にエスケープが必要。

---

## 8. 運用・バックアップ

- バックアップ: `/opt/dccai/scripts/backup.sh` + systemd timer(毎日03:00・7世代)。詳細は`/opt/dccai/BACKUP.md`
- `open-webui.env`の変更は`docker compose restart`では反映されず`up -d`(再作成)が必要
- Open WebUIのベースイメージは`Dockerfile`と`branding/Dockerfile`で`v0.11.0`に固定。更新時は両方のタグを揃え、`docker compose build --pull open-webui && docker compose up -d open-webui`でカスタムブランドイメージを再ビルドする
- litellmの`config.yaml`はボリュームマウント(`:ro`)のため、内容変更後は`docker compose restart litellm`で明示的に再起動する必要がある(`up -d`だけでは変更なしと判定され再作成されないことがある)
- cloudflaredの断続的502問題(旧ホスト時代に発生)は新規トンネル作成で解消。現在は`--protocol http2`固定+watchdog(`dccai-cloudflared-watchdog.timer`、2分毎)で保険をかけている

---

## 9. 既知の課題・重複設定

- **月間トークン上限値(1000万)が3箇所に分散している**: `dccai_pipe.py`のValve、`usage_page.py`の`MONTHLY_TOKEN_LIMIT`環境変数、`responses_gateway.py`の`MONTHLY_TOKEN_LIMIT`環境変数。**変更時は3箇所すべて揃えること**
- litellmの`/spend/logs`フィルタ不具合は未解決(Postgres直接問い合わせで回避しているだけ)
- Discord OAuth連携の`REQUIRED_GUILD_ID`等はusage_page.py側にも独自実装があり、Worker側の実装と重複している

---

## 10. アクセス情報

- 対象ホスト: `ssh gunk@100.100.252.10`(パスワードレスsudo、sshd非稼働のためTailscale SSHのみ。**セッションによって追加のブラウザ認証を求められることがある**)
- アプリ: `/opt/dccai`(git管理。`sudo git -c user.email=... -c user.name=...`で操作するか、先に`git config --global --add safe.directory /opt/dccai`)
- 秘密情報: `/opt/dccai/.env`(git管理外、平文)。DeepSeek/Gemini/Cerebras/Featherless各APIキー、Discord Bot/OAuthクレデンシャル、Postgresパスワード、litellm master keyなど
- litellm管理画面: `ssh -L 4000:localhost:4000 gunk@100.100.252.10` → `http://localhost:4000/ui`(127.0.0.1限定のため要ポートフォワード)
- Pipe更新の反映: `bash /opt/dccai/scripts/sync_pipe_to_db.sh`
