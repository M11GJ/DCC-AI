# cloudflared 断続的502 再調査依頼書 (2026-07-21)

## 対象読者への前提
このドキュメントは別セッション/別AIが引き継いで調査するための自己完結資料です。この会話の文脈は参照できない前提で書いています。

## 症状
DCC AI(公開URL `https://ai.shu-dcc.net`)が、cloudflaredトンネル経由で断続的に**502 Bad Gateway**を返す。

- 症状が出ている間、**バックエンド(`http://localhost:3000`、Open WebUI)は常に200**で健全
- 症状が出ている間、**cloudflaredコンテナのログは完全に静か**(エラーもreconnectログも一切出ない)
- `docker restart dccai-cloudflared` すると4本の接続すべてが正常に再登録され、Cloudflareの`CONNECTIVITY PRE-CHECKS`もすべてPASSで「Environment is healthy」と出るにもかかわらず、**その直後から公開URLアクセスが502のまま**というケースが複数回発生した
- 502のレスポンスは `error code: 502`(text/plain, 16バイト)というCloudflare Tunnel特有のエラー形式で、`server: cloudflare`ヘッダと`cf-ray`が付与されている(＝Cloudflareのエッジまでは到達している。origin未到達エラーではなくトンネル自体のエラー)

## 環境
- ホスト: `ssh dcc05@desktop-1f999hf`(dcc05さん private機、WSL2 Ubuntu 26.04, Tailscale接続あり)
- DCC AIは `/opt/dccai`(git管理下)、`docker-compose.yml`にサービス定義
- cloudflaredはトークン方式のリモート管理トンネル(Cloudflare Zero Trust dashboardで作成、`docker-compose.yml`の`cloudflared`サービスに`tunnel --no-autoupdate --protocol http2 run --token <TOKEN>`で起動)
- コンテナ名は `dccai-cloudflared`(dcc05さん自身の別の`cloudflared`という名前のコンテナと衝突するためリネーム済み。**同一ホストに無関係な別トンネルのcloudflaredコンテナがもう1つ動いている**点は注意)
- トンネルID: `1868bcf9-22b4-4fe7-9bf6-315266f2cc73`(このトンネルは元々omen17という別ホストで作成され、2026-07-20にdesktop-1f999hfへ移設。**移設後、この2日間で30回以上再接続を繰り返している**=接続が不安定な履歴が長い)

## これまでの経緯(2026-07-20〜21)
1. 2026-07-20: omen17からdesktop-1f999hfへ本番移行。移行直後は正常動作を確認
2. 直後に502が発生 → 原因はホスト(dcc05さんの私物PC)がスリープしていたこと。dcc05さんがスリープを無効化して解消
3. スリープ対策後も502が断続的に再発。**この時点でスリープは無関係と判明**(uptimeが連続していたため)
4. QUIC(既定プロトコル)がWSL2の仮想ネットワークと相性が悪いと推測し、`--protocol http2`に固定 → 一時的に改善したように見えたが**再発**
5. 監視・自動再起動の仕組み(systemd timer、当初5分毎→2分毎)を導入。ただし**1回のヘルスチェックだと4本のうち生きている接続に偶然当たり見逃すことがある**と判明→6回試行し3回以上失敗で異常判定する方式に改良
6. それでも改善せず、**再起動しても2〜3分しか持たずまた502に戻る**状態が継続
7. 「頻繁な再起動自体がCloudflare側のフラッピング検知を誘発しているのでは」という仮説を検証するため、**watchdogを完全停止し3分間何も触らず放置** → **改善せず、ずっと502のまま**。この仮説は否定された
8. 完全にクリーンな再起動(新規コネクタID、4接続すべて登録成功、precheckすべてPASS)の直後でも502が続くケースを確認。**cloudflared側は「健全」と認識しているのに実際には機能していない**

## 切り分け済み(原因ではないと確認できたもの)
- ローカルbackend(Open WebUI, `localhost:3000`)の不調 → 常に200、原因ではない
- 生のネットワーク品質(1.1.1.1および実際に使われているCloudflareエッジIPへのping) → 0%ロス、12ms前後で安定。原因ではない
- conntrackテーブル枯渇 → 97/262144で余裕あり。原因ではない
- NICのエラー/ドロップカウンタ(`ip -s link`) → すべてゼロ。原因ではない
- dmesgのメモリ関連イベント(`drop_caches`)→ 発生頻度が数時間おきで、502の再現間隔(数分)と一致せず無関係と推定
- 再起動の頻度自体(フラッピング説) → 何もせず放置しても回復しなかったため否定
- WSL/PCスリープ → 初回のみが原因、その後はスリープしていないのに再発するため無関係

## 未検証・次に疑うべきこと
1. **Cloudflare Zero Trust dashboard側でのこのトンネルの状態確認**(最有力候補)。トンネルがomen17→desktop-1f999hfと移設され、かつ2日間で30回以上再接続を繰り返した履歴があるため、Cloudflare側で何らかの異常検知・制限がかかっている可能性がある。ダッシュボードの「Tunnels」画面でこのトンネル(ID `1868bcf9-22b4-4fe7-9bf6-315266f2cc73`)のヘルス状態、および対象ホスト名`ai.shu-dcc.net`のPublic Hostname設定(origin設定が二重になっていないか、複数のトンネルにこのホスト名が紐づいていないか)を確認すること
2. **DNSレコードの確認**: `ai.shu-dcc.net`のDNSレコードが正しくこのトンネル向けのCNAME(オレンジ雲プロキシ有効)になっているか。過去の移設作業で意図せず変更された可能性
3. **新規トンネルの作成**: 古いトンネルオブジェクトの内部状態が壊れている可能性を疑い、Cloudflare dashboardで完全に新しいトンネルを作成し直し、新しいトークンで`docker-compose.yml`を更新して切り替える。ユーザーはこの方針を希望している。**この会話時点では新トンネルは未作成**
4. Cloudflareの障害情報(cloudflarestatus.com)に該当地域(kix03/04/05/06=大阪リージョン)の異常が出ていないか確認
5. cloudflaredのバージョン(`2026.7.2`)に既知の不具合がないか確認

## 現状の運用状態(このドキュメント作成時点)
- 公開URL: 502(不安定な状態が継続中)
- ローカルbackend: 200(健全)
- 自動監視: `/opt/dccai/scripts/cloudflared-watchdog.sh` + `dccai-cloudflared-watchdog.timer`(systemd, 2分毎)が有効。異常検知で`docker restart dccai-cloudflared`を自動実行するが、**この再起動では根本解決しない**ことが判明済み
- ロールバック手段: 旧ホストomen17(`ssh gunnk@100.90.136.25`、`wsl -d Ubuntu-24.04 -u root --`で直接rootに入れる)に`/opt/dccai`一式が停止状態のまま温存されている。そちらは移行前は同じトンネルトークンで安定稼働していた実績がある。`cd /opt/dccai && docker compose up -d`で復旧可能(ただし新ホスト側の`dccai-cloudflared`を先に止めてから起動すること。同一トークンの同時起動はNG)

## アクセス情報
- 対象ホスト: `ssh dcc05@desktop-1f999hf`(パスワードレスsudo)
- 旧ホスト(ロールバック用): `ssh gunnk@100.90.136.25` → `wsl -d Ubuntu-24.04 -u root -- <コマンド>`で直接root実行(`-u gunk`+sudoの組み合わせはパスワード待ちでハングするので避けること)
- DCC AIディレクトリ: 両ホストとも `/opt/dccai`(git管理、root所有。`git config --global --add safe.directory /opt/dccai`が必要な場合あり)
- cloudflaredのトークンは `/opt/dccai/docker-compose.yml` の `cloudflared` サービス定義内に平文で記載(このファイル自体がgit管理下にあるので `git log -p -- docker-compose.yml` で変更履歴も追える)
