# Cloudflare Worker（Discord → OIDC 変換）セットアップ

要件② "DCC 所属者だけ" を担う砦。**先にこれを上げて well-known URL を確定**させないと
`open-webui.env` の `OPENID_PROVIDER_URL` が埋まらない。

採用: `Erisa/discord-oidc-worker`

## ⚠️ このWorkerの実際の仕様（確認済み）

- ギルド/ロール判定は **Bot トークン（`DISCORD_TOKEN`）でサーバー側照会**する方式。
  → 所属チェックを効かせるには **Discord Bot が必要**。Bot を DCC サーバーに参加させておく。
- 設定ファイルは `config.json`。キーは以下（verbatim）:
  - `"clientId"`      … Discord アプリの Client ID
  - `"clientSecret"`  … Discord アプリの Client Secret
  - `"redirectURL"`   … OAuth コールバック URL
  - `"serversToCheckRolesFor"` … 判定対象ギルドIDの配列 ← ここに DCC のギルドID

## config.json（DCCのギルドIDを反映済みのテンプレート）

```json
{
  "clientId": "REPLACE_WITH_DISCORD_CLIENT_ID",
  "clientSecret": "REPLACE_WITH_DISCORD_CLIENT_SECRET",
  "redirectURL": "https://ai.shu-dcc.net/oauth/oidc/callback",
  "serversToCheckRolesFor": ["1304292402386964502"]
}
```

※ `redirectURL` は「OIDC クライアント（＝Open WebUI）のコールバック」を指す。
   Open WebUI の場合は `https://ai.shu-dcc.net/oauth/oidc/callback`。
   （Erisa版のREADMEは Cloudflare Access 例で `.../cdn-cgi/access/callback` になっている。
     Open WebUI に直結する場合は上記の Open WebUI 側コールバックに合わせる。ここは実機で要確認）

## デプロイ手順（あなたの Cloudflare / Discord アカウントが必要）

```bash
git clone https://github.com/Erisa/discord-oidc-worker
cd discord-oidc-worker
npm install

# Cloudflare にログイン（ブラウザ認証）
npx wrangler login

# KV ネームスペースを作成し、出た id を wrangler.toml に記入
npx wrangler kv namespace create "KV"

# シークレット投入
npx wrangler secret put DISCORD_TOKEN        # ← Discord Bot トークン
# （clientId/clientSecret は config.json に。実装によっては secret 版もあるので README 参照）

# config.json を上のテンプレートで用意（Client ID/Secret を実値に）

# デプロイ
npx wrangler deploy
```

デプロイ後に出る URL を控える:
- `https://<worker>.workers.dev/.well-known/openid-configuration`
  → これを `open-webui.env` の `OPENID_PROVIDER_URL` に貼る。
- 認証URLは `/authorize`（または所属チェック版 `/authorize/guilds`）。README に従う。

## Discord 側の必要作業（あなた）

1. Developer Portal → アプリ `DCCAI` → **OAuth2**
   - Client ID / Client Secret を取得
   - Redirects に `https://ai.shu-dcc.net/oauth/oidc/callback` を追加
   - （Workerが別コールバックを使う場合はそれも追加）
2. **Bot** タブ → Bot を作成 → **Token** を取得（`DISCORD_TOKEN` に使う）
3. その Bot を **DCC サーバーに招待**（メンバー/ロールを読めるようにするため）
   - 必要スコープ: `bot`（ロールで絞るなら該当権限）
