# DCCAI

DCC 部員向け AI チャット。Open WebUI（UI）＋ LiteLLM（ゲートウェイ）＋ featherless.ai（推論）＋ Discord ログイン。

## ファイル一覧

```
dccai/
├─ docker-compose.yml     # Open WebUI + LiteLLM を起動
├─ .env.example           # → cp して .env に。featherless / litellm の鍵
├─ open-webui.env         # Discord ログイン等の設定（要編集）
├─ litellm/config.yaml    # high/low・フォールバック・RPM
├─ dccai_pipe.py          # 管理画面 → Functions に貼り付ける Pipe
├─ gen-secrets.sh         # ランダム鍵を生成
└─ .gitignore
```

## 事前に決める / 用意する値

| 値 | 反映先 | 取得元 |
|----|--------|--------|
| 公開 URL（例 `https://chat.dcc.example.com`） | `open-webui.env` の `WEBUI_URL` / `OPENID_REDIRECT_URI` | 自分のドメイン + HTTPS |
| Worker サブドメイン | `open-webui.env` の `OPENID_PROVIDER_URL` | STEP2 でデプロイ後に確定 |
| Discord Client ID / Secret | `open-webui.env` | Discord Developer Portal |
| Discord ギルド ID | Worker 側の許可ギルド設定 | DCC サーバー |
| featherless API キー | `.env` の `FEATHERLESS_API_KEY` | featherless.ai/account/api-keys |
| featherless プランの同時接続数 | Pipe の `MAX_CONCURRENCY`（$25=4 / $100=8） | 契約プラン |

## デプロイ手順（サーバー上）

先に **Cloudflare Worker（Discord→OIDC 変換）** を上げて well-known URL を確定させる。
（Worker は別リポジトリ: `Erisa/discord-oidc-worker` 等。指示書 STEP1–2 参照。許可ギルドに DCC のギルド ID を設定）

```bash
# 1) 鍵を生成して控える
bash gen-secrets.sh

# 2) .env を用意
cp .env.example .env
#   → FEATHERLESS_API_KEY と LITELLM_MASTER_KEY を実値に

# 3) open-webui.env を編集
#   → WEBUI_URL / WEBUI_SECRET_KEY / OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET
#     / OPENID_PROVIDER_URL / OPENID_REDIRECT_URI を実値に
#   ※ LITELLM_MASTER_KEY と WEBUI_SECRET_KEY は gen-secrets.sh の出力を使う

# 4) 起動
docker compose up -d
docker compose logs -f open-webui

# 5) 公開 URL にアクセス → 最初にログインした Discord アカウントが管理者になる
#    （運用担当のアカウントで最初にログインすること）

# 6) 管理者パネル → Functions → ＋ で dccai_pipe.py の中身を丸ごと貼り付けて保存
#    Valves を設定:
#      LITELLM_BASE_URL = http://litellm:4000/v1
#      LITELLM_API_KEY  = .env の LITELLM_MASTER_KEY と同じ値
#      MAX_CONCURRENCY  = featherless プランの同時接続数（Premium なら 4）
#      HIGH_DAILY_LIMIT = 20
#    モデルセレクタに DCCAI High / DCCAI Low が出れば成功。
```

前段に HTTPS リバースプロキシ（Caddy / Nginx + Let's Encrypt、または Cloudflare Tunnel）を置き、
公開 URL → `localhost:3000` に流す。

## 動作確認

- [ ] Worker の `/.well-known/openid-configuration` が JSON を返す
- [ ] 公開 URL を開くと Discord 同意画面へ自動転送される
- [ ] DCC ギルド所属アカウントでログインできる／非所属では入れない
- [ ] モデルセレクタに DCCAI High / Low が出る
- [ ] Low / High で応答が返る（ストリーミング表示）
- [ ] `MAX_CONCURRENCY=1` にして 2 件同時送信 → 片方に「順番待ち中…」
- [ ] `HIGH_DAILY_LIMIT=1` にして High を 2 回 → 2 回目に上限メッセージ
- [ ] `docker compose logs litellm` に利用が記録される

## モデル変更

`litellm/config.yaml` の `model:` を featherless.ai/models の ID に差し替えて
`docker compose restart litellm`。**デプロイ前に ID の存在を必ず確認**すること。
