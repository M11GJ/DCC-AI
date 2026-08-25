# DCC AI 仕様書(2026-08-25時点)

DCC(デジタルクリエイターズコミュニティ)部員専用AIチャットサービス。本ドキュメントは**別セッション/別AIへの引き継ぎ**を目的に、現在の構成を体系的にまとめた仕様書。時系列の作業ログではなく「今どうなっているか」を記述する。より詳細な経緯・トラブルシューティングの記録は`/opt/dccai/CLOUDFLARE-TUNNEL-INVESTIGATION.md`と`/opt/dccai/HANDOFF-2026-07-23.md`を参照。

**このドキュメントは2026-08-25に`n200`とLocal 80Bサーバー`ais1-ser2`へ実際にSSHし、稼働状態・モデル接続・GPU搭載・API制限を確認して更新したもの**。旧経緯は「11. 2026-08-21の再調査で判明した差分・未文書化事項」、最新状態は「12. 2026-08-25 Local 80B追加」に記載する。

---

## 1. 概要

| 項目 | 内容 |
|---|---|
| 名称 | DCC AI |
| 対象 | DCC部員限定(Discord経由でギルド在籍を確認) |
| 部員向け入口(DCC Hub) | https://app.shu-dcc.net/ (`/ai`から利用) |
| 直接URL(Open WebUI/API) | https://ai.shu-dcc.net |
| 公開URL(使用量ページ) | https://usage.shu-dcc.net |
| 公開URL(Responses API) | https://responses.shu-dcc.net |
| 提供モデル | High / Low / Code(DeepSeek V4 Flash Vision Exp) + WebUI限定Local 80B(Qwen3 Coder Next Abliterated 79.7B Q4_K_M) |
| ホスト | DCC本体:`ssh gunk@100.100.252.10`(n200) / Local推論:`ssh t2431030@100.68.87.103`(ais1-ser2、A40 48GB×2) |
| アプリ本体 | `/opt/dccai`(git管理、root所有、GitHub remote: `github-dccai:M11GJ/DCC-AI.git`) |

---

## 2. インフラ構成

### 2.1 ホスト履歴
1. omen17(2026-07-19構築時) → 2. desktop-1f999hf(dcc05さん私物WSL2機、2026-07-20) → 3. **n200(現行、2026-07-25頃)**

旧ホストのうち`desktop-1f999hf`(Tailscale IP `100.80.194.51`)は**今も現役**。`dccai_pipe.py`に旧画像キャプション用の`OLLAMA_BASE_URL`設定は残るが、High/Lowがネイティブマルチモーダル化したため通常経路では使用しない。omen17は生死未確認。

### 2.2 n200のコンテナ構成(2026-08-21確認、8コンテナ全て稼働中・再起動回数0)

```
open-webui        (:3000→8080, 127.0.0.1限定) チャットUI本体(v0.11.0)、custom build、mem_limit 1536m
litellm           (:4000, 127.0.0.1限定) モデルルーティング・spend記録、mem_limit 2048m
dccai-postgres    (内部:5432のみ)        litellmのspend/token永続化、mem_limit 256m
dccai-cloudflared (network_mode: host)   Cloudflare Tunnel、--protocol http2、mem_limit 256m
dccai-dashboard   (:3001, 127.0.0.1限定) LiteLLM Prometheusメトリクスの簡易ダッシュボード、mem_limit 128m
dccai-usage       (:3002, 127.0.0.1限定) 個人別使用量ページ(Discord OAuth)、mem_limit 256m
dccai-responses   (:3003, 127.0.0.1限定) Responses APIゲートウェイ、mem_limit 256m
searxng           (内部のみ)             Web検索、mem_limit 512m
```

ollamaはn200には無い(GPU非搭載のため)。Local 80BだけはTailscale内の`ais1-ser2:11434`にあるシステムOllamaへLiteLLMから接続する。

**リソース状況(2026-08-21実測)**: ホストRAM 15GB中11GB使用、空き816MB、swap 4GB中1.5GB使用中。**メモリは依然として逼迫気味**。open-webuiは1.33GB/1.5GB limit(約88%)、litellmは1013MB/2GB。ディスクは98GB中40GB使用(54GB空き、余裕あり)。

### 2.3 Local 80B推論ホスト(ais1-ser2)

- Ubuntu 22.04 / NVIDIA A40 48GB×2 / RAM 125GiB
- Ollama `0.32.15`。`OLLAMA_HOST=100.68.87.103:11434`でTailscale IPだけにbind
- Ollamaの`qwen3next`安全制限に合わせて単一推論。モデルごとの`num_ctx=65536`で64K contextを確保
- モデル:`huihui_ai/qwen3-coder-next-abliterated:latest`、digest `2a8a5c3c43a2...`、79.7B、Q4_K_M、text/tools対応、vision非対応
- 同サーバーのOpen WebUIもこのシステムOllamaを使用する。localhost側にあった同一モデル登録は削除済み

### 2.4 Cloudflare Tunnel

トークン方式のリモート管理トンネル(Cloudflare Zero Trust dashboardで管理)。**DNS/ルーティングはCloudflare側で完結するため、ホストを跨ぐ移行でもトークンさえ同じなら設定変更不要**。現在3つのPublic Hostnameが同一トンネルに紐づく:

| ホスト名 | 転送先 |
|---|---|
| ai.shu-dcc.net | localhost:3000 (open-webui) |
| usage.shu-dcc.net | localhost:3002 (dccai-usage) |
| responses.shu-dcc.net | localhost:3003 (dccai-responses) |

Cloudflare API tokenは無く、Public Hostnameの追加はダッシュボードでの手動作業が必要。Tunnel tokenはGit管理外の`.env`に`CLOUDFLARE_TUNNEL_TOKEN`として保持し、Composeへ環境変数で渡す。アプリの公開ポートは全て127.0.0.1限定で、Cloudflare Tunnelだけが外部入口となる。「context canceled」エラーはクライアント側切断による正常系のノイズで実害は無い。

過去のGit履歴には旧Tunnel tokenが含まれるため、Cloudflare Zero Trust側でのtoken再発行後に`.env`を更新すること。現在の追跡ファイルと追跡対象の`.bak`ファイルからはtokenを除去済み。

---

## 3. モデル構成(litellm)

`litellm/config.yaml`で4系統を定義。既存の外部向けmodel_name(`dccai-high`/`dccai-low`/`dccai-code`)は固定し、新規Localだけ`dccai-local-80b`を追加する。

| 系統 | 1st (litellm model_name) | 実体 | フォールバック順 |
|---|---|---|---|
| High | `dccai-high` | `openai/deepseek-v4-flash-vision-exp`(DeepSeek公式APIへのOpenAI互換直通) / `reasoning_effort: max` | なし（同一API内リトライのみ） |
| Low | `dccai-low` | `openai/deepseek-v4-flash-vision-exp`(DeepSeek公式APIへのOpenAI互換直通) / `reasoning_effort: high` | なし（同一API内リトライのみ） |
| Code | `dccai-code` | `openai/deepseek-v4-flash-vision-exp`(DeepSeek公式APIへのOpenAI互換直通) / `reasoning_effort: max` | なし（同一API内リトライのみ） |
| Local 80B | `dccai-local-80b` | `ollama_chat/huihui_ai/qwen3-coder-next-abliterated:latest` / 64K context | なし。Open WebUI限定 |

DeepSeek V4系は既定でreasoning(思考過程)を出力するため、`max_tokens`が小さいと本文前に打ち切られる。

Vision Experimentalは、現行LiteLLMのDeepSeek専用adapter経由だと未知モデルとしてtext-only扱いされ画像部分が除去されるため、High/Low/Codeは`openai/` provider + `api_base: https://api.deepseek.com`で公式OpenAI互換APIへ中継する。`reasoning_effort`は`allowed_openai_params`、`thinking`は`extra_body`で明示的に通す。Local 80BはLiteLLM推奨の`ollama_chat/` providerで接続し、`num_ctx: 65536`と`keep_alive: 5m`を固定する。

Open WebUI上のモデルID(Pipe経由):`dccai.dccai-high-vision` / `dccai.dccai-low-vision` / `dccai.dccai-code` / `dccai.dccai-local-80b`。Local 80BはWebチャット画面限定で、外部APIからは拒否する。

Geminiキーは将来復活できるよう`.env`に`GEMINI_API_KEY_1`〜`_5`の**5本**を保持しているが、現在は`config.yaml`のモデル定義と`docker-compose.yml`の環境変数受け渡しをコメントアウトし、fallbackからも外している。`gemini-key-health.py`は残置しているが、自動管理マーカーを`GEMINI_DISABLED_*`へ変更してあるため構成を書き換えない。

---

## 4. Pipe(`dccai_pipe.py`)

Open WebUIのカスタムFunction(Pipe)。litellmへの中継に加え、以下のポリシーを一元的に適用する:

- **モデル選択**: High/Low/Code/Localの明示allowlistで内部litellmモデル名へ変換。未知IDをLowへ暗黙fallbackしない
- **同時実行制御**: グループ別セマフォ(`_STATE_BY_GROUP`)。High/Low=3、Code=3、Local 80B=1で別枠管理
- **WebUI限定運用**: Local 80BはAPIドキュメント・接続案内に掲載せず、Responses APIでは完全一致allowlistから除外する。Chat APIの通常呼び出し(`__metadata__.chat_id`なし)も上流へ送らず拒否するが、これは強固な認可境界ではなく非公開運用上のガードとする
- **High日次上限**: `HIGH_DAILY_LIMIT`(既定20回/日)、`USAGE_FILE`にflock付きread-modify-writeで記録
- **月間トークン上限**: `MONTHLY_TOKEN_LIMIT`(既定1000万トークン/月)。**API経由の呼び出しのみが対象、WebUIチャットは対象外**。`TOKEN_USAGE_FILE`(`/app/backend/data/dcc_ai_token_usage.json`)にflock付きで記録・判定
  - WebUI呼び出しとAPI呼び出しの区別は`__metadata__.chat_id`の有無で判定(`is_api_call`)
  - litellm側の集計用`user`フィールドも`<user_id>:api`(API)/`<user_id>`(WebUI)で分離
  - モデル更新等で枠だけをリセットする場合は`scripts/reset_monthly_token_limit.py`をOpen WebUIコンテナ内で実行する。Postgresの全利用記録は残し、リセット前カウンターをJSONL監査ログとスナップショットへ保存したうえで現在枠だけ0にする。使用量ページは「総記録」と「現在の制限枠」を分けて表示する
- **時刻基準**: High日次上限・API月間上限はどちらもJST(Asia/Tokyo)の暦日・暦月で判定
- **画像処理**: High/Low/Codeは画像を`image_url`形式のまま直接渡す。Local 80Bは`vision=false`で、画像が含まれる場合は上流へ送らず非対応を案内
- **API互換オプション**: `tools`/`tool_choice`/`response_format`/`stop`等の主要Chat Completions指定をLiteLLMへ転送する
- **ストリーミング**: `stream_options.include_usage`を要求し、SSE末尾のusageチャンクからトークン数を取得(Open WebUI管理者ダッシュボードのトークン表示・月間カウンタ更新の両方に必要)

### 4.1 Web検索・ナレッジ

- Web検索はOpen WebUI → SearXNG(`http://searxng:8080`)で実行し、取得結果を`<source resource-type="web_search">`形式のRAGコンテキストとしてPipeへ渡す
- 永続設定`config.rag.template`には元の質問を`{{QUERY}}`で必ず含める。Web検索ソースが存在する場合は「このリクエストでDCC AIの検索機能が実行済み」とモデルへ明示し、検索不能という定型的な断りを返させない
- RAGテンプレートとモデル接続設定の再適用は`bash /opt/dccai/scripts/apply_open_webui_rag.sh`。実体は`scripts/configure_open_webui_rag.py`で、実行前にOpen WebUI DBの手動バックアップを作成する
- DCC DiscordナレッジはHigh/Lowへ接続。CodeとLocal 80Bは`meta.knowledge=[]`として明示的に未接続

反映方法: `dccai_pipe.py`を編集後、`bash /opt/dccai/scripts/sync_pipe_to_db.sh`でOpen WebUIのSQLite(`function`テーブル)へ書き込み+open-webui再起動が必要(ファイルを置くだけでは反映されない)。

---

## 5. 外部API

DCC部員はOpen WebUIで発行したAPIキー(Settings > Account > API keys)を使って外部からアクセス可能。**Chat Completions APIとResponses APIは同じ月間トークン枠を共有する**。

### 5.1 Chat Completions API(Pipe経由)
- Endpoint: `https://ai.shu-dcc.net/api/chat/completions`(OpenAI SDK利用時は base_url に `https://ai.shu-dcc.net/api`)
- モデルID: `dccai.dccai-high-vision` / `dccai.dccai-low-vision` / `dccai.dccai-code`
- `dccai.dccai-local-80b`はWebUI限定のためChat Completions APIでは推論せず案内を返す
- 上記4節のPipeポリシーがすべて適用される

### 5.2 Responses API(専用ゲートウェイ経由)
- Endpoint: `https://responses.shu-dcc.net/v1/responses`
- 実体は`scripts/responses_gateway.py`(python:3.11-slim + httpx、コンテナ`dccai-responses`)。**2026-08-03に大幅拡張済み**(313行追加、「ツール呼び出し互換層」節参照)。テスト`scripts/test_responses_gateway.py`も同時追加。
- **背景**: litellm自体は`/v1/responses`に標準対応済みだが、Open WebUI本体の`/responses`ルートは「Connection」モデル専用でPipeモデルには使えない。PipeはChat Completions専用のフックしか持たないため、Responses API対応は別サービスとして実装した
- 認証: Open WebUIの`api_key`テーブル(`webui.db`)を直接読んでBearerトークンを検証(Open WebUI自身と同じ検証方法)
- Pipeとの整合性: High/Low/Codeの明示allowlist・Code専用同時実行制限(3)・JST月間トークン上限判定はPipeと**同じロジック・同じファイル**を使う。Local 80Bと未知IDはHTTP 400で拒否する
- 画像入力はAPI公開中のHigh/Low/Codeへそのまま渡す。内部reasoning itemは最終回答・tool callを残して公開応答から除外する。stream指定時も安全化後に標準SSEへ組み直す
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

**データソース**: litellmの`/spend/logs` REST APIは`limit`/`end_user_id`フィルタが機能せず(既知の欠陥、原因未特定)、全件(数万行・90MB超)を返し9〜14秒かかっていたため、**同じdocker network上のPostgres(`LiteLLM_SpendLogs`テーブル)へ`psycopg2`で直接SQL問い合わせ**する方式に変更済み(0.1秒未満)。月次集計は**JST暦月**で統一(日別ランキングもJSTのため)。litellm自体のヘルスチェック(`/health/readiness`)は2026-08-21時点`{"status":"healthy","db":"connected"}`で正常。

**罠**: SQLに`LIKE '%...'`のようなリテラル`%`を含める場合、`psycopg2`のプレースホルダー解析と衝突するため`%%`にエスケープが必要。

---

## 8. 運用・バックアップ

- バックアップ: `/opt/dccai/scripts/backup.sh` + systemd timer(JST毎日03:00・7世代)。`umask 077`で作成し、既存世代もroot専用権限に統一。`OFFSITE_BACKUP_DIR`に別ディスク/NASのマウントポイントを指定すると同時コピーする。詳細は`/opt/dccai/BACKUP.md`
- `open-webui.env`の変更は`docker compose restart`では反映されず`up -d`(再作成)が必要
- Open WebUIのベースイメージは`Dockerfile`と`branding/Dockerfile`で`v0.11.0`に固定(2026-08-01に更新)。更新時は両方のタグを揃え、`docker compose build --pull open-webui && docker compose up -d open-webui`でカスタムブランドイメージを再ビルドする
- litellmの`config.yaml`はボリュームマウント(`:ro`)のため、内容変更後は`docker compose restart litellm`で明示的に再起動する必要がある(`up -d`だけでは変更なしと判定され再作成されないことがある)
- cloudflaredの断続的502問題(旧ホスト時代に発生)は新規トンネル作成で解消。現在は`--protocol http2`固定+watchdog(`dccai-cloudflared-watchdog.timer`、2分毎、**2026-08-21時点で稼働確認済み**)で保険をかけている

---

## 9. 既知の課題・重複設定

- **月間トークン上限値(1000万)が3箇所に分散している**: `dccai_pipe.py`のValve、`usage_page.py`の`MONTHLY_TOKEN_LIMIT`環境変数、`responses_gateway.py`の`MONTHLY_TOKEN_LIMIT`環境変数。**変更時は3箇所すべて揃えること**
- litellmの`/spend/logs`フィルタ不具合は未解決(Postgres直接問い合わせで回避しているだけ)
- Discord OAuth連携の`REQUIRED_GUILD_ID`等はusage_page.py側にも独自実装があり、Worker側の実装と重複している

---

## 10. アクセス情報

- 対象ホスト: `ssh gunk@100.100.252.10`(OpenSSH公開鍵認証をTailscaleインターフェース内だけで利用。パスワード認証・rootログインは禁止、UFWはtailscale0の22/tcpのみ許可)
- アプリ: `/opt/dccai`(git管理。`sudo git -c user.email=... -c user.name=...`で操作するか、先に`git config --global --add safe.directory /opt/dccai`)。GitHub remote `github-dccai:M11GJ/DCC-AI.git`あり(SSH鍵はn200上に設定済み、`git fetch`確認済み)
- 秘密情報: `/opt/dccai/.env`(git管理外、root専用)。DeepSeek/Gemini(×5本)/Cerebras/Featherless各APIキー、Cloudflare Tunnel token、Discord Bot/OAuthクレデンシャル、Postgresパスワード、litellm master keyなど
- litellm管理画面: `ssh -L 4000:localhost:4000 gunk@100.100.252.10` → `http://localhost:4000/ui`(127.0.0.1限定のため要ポートフォワード)
- Pipe更新の反映: `bash /opt/dccai/scripts/sync_pipe_to_db.sh`

---

## 11. 2026-08-21の再調査で判明した差分・未文書化事項

このドキュメント作成にあたりn200へ実際にSSHし、`docker ps`/`docker stats`/`git log`/`git status`/`systemctl list-timers`/`config.yaml`/`docker-compose.yml`/各スクリプトの中身を直接確認した。以下は旧版(2026-08-01)からの実質的な変化、または旧版に記載が無かった発見。

### 11.1 自動化ジョブの現状

`/opt/dccai/scripts`には以下2つのスクリプトが残っているが、n200上には対応するsystemdタイマーが登録されていない:

1. **`discord-knowledge-sync.py`**(+ラッパー`discord-knowledge-sync.sh`): Discordの指定チャンネルのメッセージをOpen WebUIのKnowledge「DCC Discord」に同期し、RAGとしてモデルに参照させる機能。スクリプト内コメントでは「systemdの`dccai-discord-sync.timer`から30分ごとに呼ばれる」想定だが、そのタイマー自体が現ホストに存在しない。**つまりDiscordのナレッジは2026-07-20時点の内容で1ヶ月以上更新されていない可能性が高い**
2. **`gemini-key-health.py`**: Geminiプールの一時廃止に伴い、意図的に停止状態を維持する。キーとスクリプトは将来の復活用に残すが、`config.yaml`の定義、Composeのキー受け渡し、fallback参照は無効化済み。

Discordナレッジ同期の扱いは別途ユーザー指示待ち。Geminiヘルスチェックは再登録しない。

### 11.2 Tailscale・DockerのOOM保護

`dccai-oom-protect.timer`を5分ごとに実行し、tailscaled/dockerd/containerdへOOM優先度を再適用する。サービス再起動時にも再設定されるようsystemd drop-inを併用する。

### 11.3 直近1ヶ月の運用実績(安定稼働)

- 全8コンテナとも`RestartCount=0`(クラッシュ再起動なし)
- litellm `/health/readiness`は`healthy`、Postgres接続も正常
- 日次バックアップは本日(2026-08-21)分まで正常完了(457MB、7世代ローテーション動作中)
- cloudflared watchdog(2分毎)も稼働中で直近の発火ログに異常なし
- ログに残る502相当のエラー("context canceled")はクライアント切断由来の想定内ノイズで、旧ホスト時代のような断続的な実障害ではない

### 11.4 軽微なドリフト(設定値の変化)

- `litellm`の`mem_limit`が旧メモリ記載の`1536m`から**`2048m`に増量**されていた(いつ・誰が変更したか不明、コミット履歴からは特定できず。おそらくメモリ不足での実地対応)
- `.env`のGeminiキーは**5本**(`GEMINI_API_KEY_1`〜`_5`)保持しているが、モデル定義・環境変数受け渡し・fallbackはいずれも一時停止中(11.1参照)
- 直近の未文書化コミット3件: `c6a4060`(SPEC.md新規作成)、`66e8af0`(Open WebUI v0.11.0へアップグレード)、`0c1d77c`(Responses APIのツール呼び出し正規化+DSML復元、テストファイル追加)

### 11.5 git remoteとの乖離

`/opt/dccai`はGitHub(`github-dccai:M11GJ/DCC-AI.git`)をoriginに持つ。2026-08-21のモデル・JST・API・セキュリティ修正を含めてpushし、引き継ぎ元をGitHubへ統一する。保守作業後は`git status --short --branch`でahead/behindを必ず確認する。

---

## 12. 2026-08-25 Local 80B追加

- **表示名/ID**: `DCC AI Local 80B` / `dccai.dccai-local-80b`
- **公開範囲**: Open WebUIのチャット画面のみ。Chat Completions APIはPipeで案内を返し、Responses APIはHTTP 400で拒否
- **上流**: `ais1-ser2`のシステムOllama(`100.68.87.103:11434`)。Tailscale内からのみ到達可能
- **モデル**: Qwen3 Coder Next Abliterated 79.7B Q4_K_M、64K context、text/tools対応、vision非対応
- **並列性**: OllamaとPipeの両方を単一推論に統一。2件目以降はPipeで順番待ちにし、Ollamaへ同時送信しない
- **ナレッジ**: DCC Discordは未接続(`knowledge=[]`)。Open WebUIのWeb検索とツールは利用可能
- **既存契約**: High/Low/CodeのモデルID、DeepSeek上流、API月間枠は変更しない

---

*本ドキュメントは2026-08-25、Codexがn200とais1-ser2へ直接SSHし、Local 80Bのモデル実体・64K GPU搭載・単一推論・DCC経路を検証して更新した。*
