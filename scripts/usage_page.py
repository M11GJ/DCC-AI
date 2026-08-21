#!/usr/bin/env python3
"""
DCC AI - Usage Page Server
Discordログイン(独自OAuth2、既存のDiscordアプリを再利用)で本人確認し、
自分のトークン消費量(WebUI分/API分の内訳)を表示する。
集計データはlitellmのREST /spend/logs(limit/end_user_idフィルタが効かず
毎回全件をmessages/response列込みで返すため極端に遅い)を経由せず、
同じdocker network上のPostgresへpsycopg2で直接問い合わせる。
"""
import base64
import hashlib
import hmac
import http.server
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http import cookies

import psycopg2
import psycopg2.extras

JST = timezone(timedelta(hours=9))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PORT = int(os.environ.get("PORT", "3002"))
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://usage.shu-dcc.net")
REQUIRED_GUILD_ID = os.environ.get("REQUIRED_GUILD_ID", "1304292402386964502")
SESSION_SECRET = os.environ.get("USAGE_SESSION_SECRET", "")
LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
WEBUI_DB_PATH = os.environ.get("WEBUI_DB_PATH", "/webui-data/webui.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
TOKEN_USAGE_FILE = os.environ.get("TOKEN_USAGE_FILE", "/webui-data/dcc_ai_token_usage.json")
TOKEN_RESET_LOG_FILE = os.environ.get(
    "TOKEN_RESET_LOG_FILE", "/webui-data/dcc_ai_token_usage_resets.jsonl"
)
# dccai_pipe.py の Valve MONTHLY_TOKEN_LIMIT と同じ値を手動で同期させること
# (Open WebUIの管理画面でValvesを変更した場合はこちらの環境変数も合わせて変更が必要)。
MONTHLY_TOKEN_LIMIT = int(os.environ.get("MONTHLY_TOKEN_LIMIT", "10000000"))
SESSION_TTL = 6 * 3600


def _pg_query(sql, params=None):
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()

COOKIE_NAME = "dccai_usage_session"


def _sign(value: str) -> str:
    mac = hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{mac}"


def _verify(signed: str):
    try:
        value, mac = signed.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    return value if hmac.compare_digest(mac, expected) else None


def make_session(discord_id: str) -> str:
    return _sign(f"{discord_id}:{int(time.time()) + SESSION_TTL}")


def read_session(cookie_header: str):
    if not cookie_header:
        return None
    jar = cookies.SimpleCookie()
    jar.load(cookie_header)
    morsel = jar.get(COOKIE_NAME)
    if not morsel:
        return None
    value = _verify(morsel.value)
    if not value:
        return None
    try:
        discord_id, exp = value.rsplit(":", 1)
        if int(exp) < time.time():
            return None
        return discord_id
    except ValueError:
        return None


# Discord APIはUser-Agent無しのリクエストを403で弾くため必須
USER_AGENT = "DccaiUsagePage (https://usage.shu-dcc.net, 1.0)"


def http_get_json(url, headers=None, timeout=10):
    base_headers = {"User-Agent": USER_AGENT}
    base_headers.update(headers or {})
    req = urllib.request.Request(url, headers=base_headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def http_post_form_json(url, data, headers=None):
    body = urllib.parse.urlencode(data).encode()
    base_headers = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT}
    base_headers.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=base_headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def find_openwebui_user(discord_id: str):
    conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, role FROM user WHERE json_extract(oauth, '$.oidc.sub') = ?",
            (discord_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def all_openwebui_users():
    """id -> (name, email) の辞書。管理者ページでの表示名解決に使う。"""
    conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, email FROM user")
        return {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    finally:
        conn.close()


def litellm_total_and_by_model(litellm_user_id: str):
    """litellmのend_user_idで直接集計する。WebUI分は<user_id>、API分は
    <user_id>:api として dccai_pipe.py 側で送信時に分離タグ付けされている
    (main.pyのchat_id有無で判定、詳細はdccai_pipe.py参照)。
    end_userにインデックスがあるPostgresへ直接問い合わせる(旧: litellmの
    /spend/logs?end_user_id=... はフィルタが効かず全件返るため使わない)。
    """
    try:
        rows = _pg_query(
            'SELECT model_group, model, total_tokens FROM "LiteLLM_SpendLogs" WHERE end_user = %s',
            (litellm_user_id,),
        )
    except Exception:
        return 0, {}
    total = 0
    by_model = {}
    for row in rows:
        tokens = row.get("total_tokens", 0) or 0
        total += tokens
        model = row.get("model_group") or row.get("model") or "unknown"
        by_model[model] = by_model.get(model, 0) + tokens
    return total, by_model


def _limit_bar_style(used: int, limit: int):
    """使用率(%)・残り(%)・バー色を計算する(70%以上で黄、95%以上で赤)。limit<=0なら無制限扱い。"""
    if limit <= 0:
        return 0, 100, "var(--bar-fill)"
    pct = min(100.0, round(used / limit * 100, 1))
    remaining_pct = max(0.0, round(100 - used / limit * 100, 1))
    if pct >= 95:
        color = "#d64545"
    elif pct >= 70:
        color = "#e0a020"
    else:
        color = "var(--bar-fill)"
    return pct, remaining_pct, color


def _rank_rows_html(items, me_uid):
    """items: [(label, total, uid), ...] を降順ソート済みで渡す。ランキング系ページで共用する。"""
    max_total = max((t for _, t, _ in items), default=1)
    return "".join(
        f'<div class="rank-row{" me" if uid == me_uid else ""}"><div class="rank-num">{i}</div>'
        f'<div class="rank-name" title="{label}">{label}</div>'
        f'<div class="rank-track"><div class="rank-fill" '
        f'style="width:{round(total / max_total * 100, 1)}%;"></div></div>'
        f'<div class="rank-total">{total:,}</div></div>'
        for i, (label, total, uid) in enumerate(items, start=1)
    ) or '<p class="muted">まだ利用がありません</p>'


def _jst_month_utc_range():
    """今月(JST暦月)の開始・終了をUTCのdatetimeで返す。管理者ページの日別ランキングは
    JST暦日で切っているため、月次側もJSTで揃えないと月初・月末付近で日別と月間の
    集計対象がズレる(JST深夜〜朝の利用が別の暦月に計上されてしまう)。"""
    now_jst = datetime.now(JST)
    start_jst = datetime(now_jst.year, now_jst.month, 1, tzinfo=JST)
    end_jst = (
        datetime(now_jst.year + 1, 1, 1, tzinfo=JST)
        if now_jst.month == 12
        else datetime(now_jst.year, now_jst.month + 1, 1, tzinfo=JST)
    )
    return start_jst.astimezone(timezone.utc), end_jst.astimezone(timezone.utc)


def api_tokens_this_month(user_id: str) -> int:
    """現在の制限枠で消費したAPIトークン数。
    Postgresの総記録ではなく、Pipe/Responsesが上限判定に使うカウンターを読む。"""
    try:
        with open(TOKEN_USAGE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        month = datetime.now(JST).strftime("%Y-%m")
        return int(data.get(month, {}).get(user_id, 0))
    except Exception:
        return 0


def api_tokens_this_month_all_users():
    """管理者用: 現在の制限枠で消費したAPIトークン数。"""
    try:
        with open(TOKEN_USAGE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        month = datetime.now(JST).strftime("%Y-%m")
        return {user_id: int(total) for user_id, total in data.get(month, {}).items()}
    except Exception:
        return {}


def token_limit_reset_note() -> str:
    """今月最後の制限枠リセットを画面表示用HTMLとして返す。"""
    month = datetime.now(JST).strftime("%Y-%m")
    try:
        with open(TOKEN_RESET_LOG_FILE, encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        event = next((e for e in reversed(events) if e.get("month") == month), None)
        if not event:
            return ""
        reset_at = datetime.fromisoformat(event["reset_at"]).astimezone(JST)
        reason = event.get("reason") or "管理者によるリセット"
        return (
            '<p class="muted" style="margin:6px 0 0;">'
            f'制限枠は {reset_at.strftime("%Y-%m-%d %H:%M JST")} にリセット済み'
            f'（{reason}）。リセット前の利用記録は総利用量に保持されています。</p>'
        )
    except Exception:
        return ""


def total_tokens_this_month_all_users():
    """月間消費ランキング用: 今月(JST暦月)のトークン使用量(WebUI+API合算)を
    ユーザー単位で集計する。"""
    start, end = _jst_month_utc_range()
    try:
        rows = _pg_query(
            'SELECT end_user, sum(total_tokens) AS total FROM "LiteLLM_SpendLogs" '
            'WHERE "startTime" >= %s AND "startTime" < %s GROUP BY end_user',
            (start, end),
        )
    except Exception:
        return {}
    per_user = {}
    for row in rows:
        eu = row.get("end_user") or ""
        if not eu:
            continue
        base = eu[: -len(":api")] if eu.endswith(":api") else eu
        per_user[base] = per_user.get(base, 0) + int(row.get("total") or 0)
    return per_user


# litellmの/spend/logsはlimit/end_user_idフィルタが効かず、messages/response列込みで
# 毎回全件(club規模の想定を超え数万件・90MB超)返してくるため使わない。Postgresへ
# 必要な列だけ直接問い合わせる(体感0.1秒程度、旧実装は9〜14秒)。管理者ページは1回の
# 表示で複数の集計関数を呼ぶため、それでも短TTLでプロセス内キャッシュして呼び出しを1回に潰す。
_SPEND_LOGS_CACHE = {"logs": None, "ts": 0.0}
_SPEND_LOGS_TTL_SEC = 60


def _fetch_all_spend_logs():
    now = time.time()
    if _SPEND_LOGS_CACHE["logs"] is not None and (now - _SPEND_LOGS_CACHE["ts"]) < _SPEND_LOGS_TTL_SEC:
        return _SPEND_LOGS_CACHE["logs"]
    logs = _pg_query(
        'SELECT end_user, model_group, model, total_tokens, "startTime" FROM "LiteLLM_SpendLogs"'
    )
    _SPEND_LOGS_CACHE["logs"] = logs
    _SPEND_LOGS_CACHE["ts"] = now
    return logs


def litellm_all_users_grouped():
    """管理者用: 全ログを end_user 単位(WebUI/APIタグを分離)で集計する。"""
    logs = _fetch_all_spend_logs()

    per_user = {}
    for entry in logs:
        end_user = entry.get("end_user")
        if not end_user:
            continue
        tokens = entry.get("total_tokens", 0) or 0
        if end_user.endswith(":api"):
            base_id, bucket = end_user[: -len(":api")], "api"
        else:
            base_id, bucket = end_user, "webui"
        row = per_user.setdefault(base_id, {"webui": 0, "api": 0})
        row[bucket] += tokens
    return per_user


# litellmのmodel_groupを表示用の3系統(High/Low/Code)にまとめる。
# フォールバック先(*-legacy, backup-low)は上位ティアに合算して表示する。
MODEL_TIERS = ["High", "Low", "Code"]
MODEL_TIER_MAP = {
    "dccai-high": "High",
    "dccai-high-legacy": "High",
    "dccai-low": "Low",
    "dccai-low-legacy": "Low",
    "dccai-backup-low": "Low",
    "dccai-code": "Code",
}


def litellm_all_users_by_model():
    """管理者用: 全ログをユーザー×モデルティアで集計する
    (グラフモード用。WebUI/APIの区別はここでは畳み込む)。"""
    logs = _fetch_all_spend_logs()

    per_user = {}
    for entry in logs:
        end_user = entry.get("end_user")
        if not end_user:
            continue
        base_id = end_user[: -len(":api")] if end_user.endswith(":api") else end_user
        tokens = entry.get("total_tokens", 0) or 0
        model_group = entry.get("model_group") or entry.get("model") or "unknown"
        tier = MODEL_TIER_MAP.get(model_group, "その他")
        row = per_user.setdefault(base_id, {})
        row[tier] = row.get(tier, 0) + tokens
    return per_user


def jst_today_str():
    return datetime.now(JST).strftime("%Y-%m-%d")


def jst_day_utc_range(date_str):
    """"YYYY-MM-DD"(JST基準の暦日)の開始・終了をUTCのdatetimeで返す。"""
    y, m, d = (int(x) for x in date_str.split("-"))
    start_jst = datetime(y, m, d, tzinfo=JST)
    end_jst = start_jst + timedelta(days=1)
    return start_jst.astimezone(timezone.utc), end_jst.astimezone(timezone.utc)


def shift_date_str(date_str, days):
    y, m, d = (int(x) for x in date_str.split("-"))
    return (datetime(y, m, d) + timedelta(days=days)).strftime("%Y-%m-%d")


def litellm_daily_ranking(date_str):
    """管理者用: 指定日(JST暦日)のトークン使用量をユーザー単位(WebUI+API合算)で集計する。"""
    start_utc, end_utc = jst_day_utc_range(date_str)
    logs = _fetch_all_spend_logs()

    per_user = {}
    for entry in logs:
        t = entry.get("startTime")
        if not t:
            continue
        # PostgresのtimestampはUTCで格納されているがtzinfo無しで返るため付与する
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if not (start_utc <= t < end_utc):
            continue
        end_user = entry.get("end_user")
        if not end_user:
            continue
        base_id = end_user[: -len(":api")] if end_user.endswith(":api") else end_user
        tokens = entry.get("total_tokens", 0) or 0
        per_user[base_id] = per_user.get(base_id, 0) + tokens
    return per_user


# 全ページ共通: テーマ変数(ライト/ダーク、OS設定がデフォルト・手動切替はlocalStorageに保存)
THEME_CSS_BLOCK = """
  :root {{
    --surface-1:      #fcfcfb;
    --page:           #f9f9f7;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #898781;
    --gridline:       #e1e0d9;
    --border:         rgba(11,11,11,0.10);
    --bar-fill:       #0b0b0b;
    --series-high:    #2a78d6;
    --series-low:     #eb6834;
    --series-code:    #1baf7a;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) {{
      --surface-1:      #1a1a19;
      --page:           #0d0d0d;
      --text-primary:   #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted:     #898781;
      --gridline:       #2c2c2a;
      --border:         rgba(255,255,255,0.10);
      --bar-fill:       #ffffff;
      --series-high:    #3987e5;
      --series-low:     #d95926;
      --series-code:    #199e70;
    }}
  }}
  :root[data-theme="dark"] {{
    --surface-1:      #1a1a19;
    --page:           #0d0d0d;
    --text-primary:   #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted:     #898781;
    --gridline:       #2c2c2a;
    --border:         rgba(255,255,255,0.10);
    --bar-fill:       #ffffff;
    --series-high:    #3987e5;
    --series-low:     #d95926;
    --series-code:    #199e70;
  }}
  body {{ background: var(--page); color: var(--text-primary); margin: 0;
          font-family: -apple-system, "Hiragino Sans", "Yu Gothic", sans-serif; }}
  .top-bar {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 4px; }}
  .top-bar-actions {{ display: flex; gap: 8px; flex: none; }}
  .pill-btn, .pill-btn:visited {{ background: none; border: 1px solid var(--border); border-radius: 6px;
                 padding: 6px 10px; font-size: 0.85rem; color: var(--text-primary); cursor: pointer;
                 text-decoration: none; white-space: nowrap; }}
  .pill-btn:hover {{ background: var(--gridline); }}
"""

THEME_TOGGLE_SCRIPT_BLOCK = """
<script>
(function() {{
  var root = document.documentElement;
  var KEY = 'dcc-usage-theme';
  var saved = localStorage.getItem(KEY);
  if (saved) root.setAttribute('data-theme', saved);
  document.getElementById('theme-toggle').addEventListener('click', function() {{
    var isDark = saved
      ? saved === 'dark'
      : matchMedia('(prefers-color-scheme: dark)').matches;
    var next = isDark ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    localStorage.setItem(KEY, next);
    saved = next;
  }});
}})();
</script>
"""

PAGE_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DCC AI - 使用量</title>
<style>""" + THEME_CSS_BLOCK + """
  .viz-root {{ max-width: 560px; margin: 0 auto; padding: 60px 20px; }}
  h1 {{ font-size: 1.3rem; margin: 0; }}
  .card {{ background: var(--surface-1); border: 1px solid var(--border);
           border-radius: 10px; padding: 20px; margin: 16px 0; }}
  .big {{ font-size: 2rem; font-weight: 700; }}
  .row {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--gridline); }}
  .row:last-child {{ border-bottom: none; }}
  .muted {{ color: var(--text-secondary); font-size: 0.85rem; }}
  code {{ background: rgba(127,127,127,0.15); padding: 1px 6px; border-radius: 4px; font-size: 0.85em; }}
  pre {{ margin: 10px 0 0; padding: 10px 12px; background: rgba(127,127,127,0.12);
         border-radius: 8px; font-size: 0.78rem; overflow-x: auto; white-space: pre; }}
  .limit-track {{ background: var(--gridline); border-radius: 4px; height: 20px; overflow: hidden; margin: 10px 0 8px; }}
  .limit-fill {{ height: 100%; border-radius: 4px 0 0 4px; transition: width .3s; }}
</style>
<div class="viz-root">
  <div class="top-bar">
    <h1>DCC AI 使用量 — {name}</h1>
    <div class="top-bar-actions">
      <a class="pill-btn" href="/ranking">🏆 ランキング</a>
      <button class="pill-btn" id="theme-toggle" type="button">🌓 表示切替</button>
    </div>
  </div>
  <div class="card">
    <div class="muted">合計トークン数(全期間、WebUI+API)</div>
    <div class="big">{total:,}</div>
  </div>
  <div class="card">
    <div class="row"><span>WebUI(チャット画面)利用</span><span>{webui:,}</span></div>
    <div class="row"><span>API利用</span><span>{api:,}</span></div>
  </div>
  <div class="card">
    <div class="muted">今月のAPI利用状況(月間上限: {monthly_limit:,} トークン)</div>
    <div class="limit-track"><div class="limit-fill" style="width:{limit_pct}%; background:{limit_color};"></div></div>
    <div class="row" style="border-bottom:none;"><span>{api_month:,} トークン使用</span><span>残り {limit_remaining_pct}%</span></div>
    <p class="muted" style="margin:6px 0 0;">この上限は<strong>API経由の利用のみ</strong>が対象です。WebUI(チャット画面)の利用は含まれません。</p>
    {reset_note}
  </div>
  <div class="card">
    <div class="muted" style="margin-bottom:8px">モデル別内訳(WebUI+API合算)</div>
    {model_rows}
  </div>
  <p class="muted">WebUI分・API分はそれぞれ独立に集計しています(2026-07-23以降のリクエストが対象。それ以前はWebUI/APIの区別なく合計にのみ反映されます)。</p>

  <div class="card">
    <div class="muted" style="margin-bottom:8px">🔌 API接続情報(Chat Completions)</div>
    <div>Endpoint: <code>https://ai.shu-dcc.net/api/chat/completions</code></div>
    <div style="margin-top:4px;">モデルID: <code>dccai.dccai-high-vision</code> (High) /
      <code>dccai.dccai-low-vision</code> (Low) /
      <code>dccai.dccai-code</code> (Code)</div>
    <pre>curl https://ai.shu-dcc.net/api/chat/completions \\
  -H "Authorization: Bearer sk-xxxxxxxx" \\
  -H "Content-Type: application/json" \\
  -d '{{"model": "dccai.dccai-low-vision", "messages": [{{"role": "user", "content": "hello"}}]}}'</pre>
    <p class="muted" style="margin-top:8px;">
      OpenAI公式SDKの場合は base_url に <code>https://ai.shu-dcc.net/api</code> を指定してください。
      APIキーは DCC AI の Settings &gt; Account &gt; API keys から発行できます。
    </p>
  </div>
  <div class="card">
    <div class="muted" style="margin-bottom:8px">🔌 API接続情報(Responses API)</div>
    <div>Endpoint: <code>https://responses.shu-dcc.net/v1/responses</code></div>
    <div style="margin-top:4px;">モデルID: <code>dccai.dccai-high-vision</code> (High) /
      <code>dccai.dccai-low-vision</code> (Low) /
      <code>dccai.dccai-code</code> (Code)</div>
    <pre>curl https://responses.shu-dcc.net/v1/responses \\
  -H "Authorization: Bearer sk-xxxxxxxx" \\
  -H "Content-Type: application/json" \\
  -d '{{"model": "dccai.dccai-low-vision", "input": "hello"}}'</pre>
    <p class="muted" style="margin-top:8px;">
      OpenAI公式SDKの場合は base_url に <code>https://responses.shu-dcc.net</code> を指定してください(<code>client.responses.create(...)</code>)。
      APIキーはChat Completions用と共通です。<strong>月間トークン上限もChat Completions APIと合算</strong>されます。
    </p>
  </div>
  {admin_link}
</div>
""" + THEME_TOGGLE_SCRIPT_BLOCK

ADMIN_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DCC AI - 使用量(管理者)</title>
<style>""" + THEME_CSS_BLOCK + """
  .viz-root {{ max-width: 760px; margin: 0 auto; padding: 60px 20px; }}
  h1 {{ font-size: 1.3rem; margin: 0; }}
  .muted {{ color: var(--text-secondary); font-size: 0.85rem; }}
  .card {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
           padding: 20px; margin: 16px 0; }}

  .date-nav {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; }}
  .date-nav a, .date-nav a:visited {{ color: var(--text-primary); text-decoration: none; font-size: 0.85rem;
                 border: 1px solid var(--border); border-radius: 6px; padding: 6px 12px; }}
  .date-nav a:hover {{ background: var(--gridline); }}
  .date-nav .date-label {{ font-weight: 600; font-size: 1rem; }}

  .rank-row {{ display: flex; align-items: center; gap: 10px; margin: 10px 0; }}
  .rank-num {{ width: 22px; flex: none; text-align: right; font-size: 0.8rem;
               color: var(--text-muted); font-variant-numeric: tabular-nums; }}
  .rank-name {{ width: 110px; flex: none; font-size: 0.85rem;
               overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .rank-track {{ flex: 1; background: var(--gridline); border-radius: 4px; height: 22px; overflow: hidden; }}
  .rank-fill {{ height: 100%; background: var(--bar-fill); border-radius: 0 4px 4px 0; }}
  .rank-total {{ width: 72px; flex: none; text-align: right; font-size: 0.82rem;
                color: var(--text-secondary); font-variant-numeric: tabular-nums; }}

  .legend {{ display: flex; gap: 16px; margin-bottom: 18px; font-size: 0.82rem; color: var(--text-secondary); }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .legend-swatch {{ width: 10px; height: 10px; border-radius: 2px; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin: 10px 0; }}
  .bar-name {{ width: 110px; flex: none; font-size: 0.82rem; color: var(--text-secondary);
               overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .bar-track {{ flex: 1; background: var(--gridline); border-radius: 4px; height: 22px; }}
  .bar-fill {{ display: flex; gap: 2px; height: 100%; border-radius: 0 4px 4px 0; overflow: hidden; }}
  .seg {{ height: 100%; }}
  .seg-high {{ background: var(--series-high); }}
  .seg-low {{ background: var(--series-low); }}
  .seg-code {{ background: var(--series-code); }}
  .bar-total {{ width: 72px; flex: none; text-align: right; font-size: 0.82rem;
                color: var(--text-secondary); font-variant-numeric: tabular-nums; }}

  .limit-row {{ display: flex; align-items: center; gap: 10px; margin: 10px 0; }}
  .limit-name {{ width: 110px; flex: none; font-size: 0.82rem; color: var(--text-secondary);
                 overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .limit-track {{ flex: 1; background: var(--gridline); border-radius: 4px; height: 22px; overflow: hidden; }}
  .limit-fill {{ height: 100%; border-radius: 0 4px 4px 0; }}
  .limit-pct {{ width: 96px; flex: none; text-align: right; font-size: 0.82rem;
                color: var(--text-secondary); font-variant-numeric: tabular-nums; }}

  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 0.88rem; }}
  th, td {{ text-align: right; padding: 8px 10px; border-bottom: 1px solid var(--gridline);
            font-variant-numeric: tabular-nums; }}
  th:first-child, td:first-child {{ text-align: left; font-variant-numeric: normal; }}
  th {{ color: var(--text-muted); font-weight: 600; font-size: 0.78rem; }}
</style>
<div class="viz-root">
  <div class="top-bar">
    <h1>DCC AI 使用量 — 全ユーザー(管理者)</h1>
    <div class="top-bar-actions">
      <a class="pill-btn" href="/ranking">🏆 ランキング</a>
      <button class="pill-btn" id="theme-toggle" type="button">🌓 表示切替</button>
    </div>
  </div>
  <p class="muted">2026-07-23以降の記録が対象です(それ以前はlitellmのトークン集計が未接続でした)。</p>

  <div class="card">
    <div class="date-nav">
      <a href="/admin?date={prev_date}">&larr; 前日</a>
      <span class="date-label">{date_label}</span>
      <a href="/admin?date={next_date}">翌日 &rarr;</a>
    </div>
    {rank_rows}
  </div>

  <div class="card">
    <div class="muted" style="margin-bottom:8px;">今月のAPI利用状況(月間上限: {monthly_limit:,} トークン/人。WebUIチャットは対象外)</div>
    {reset_note}
    {monthly_api_rows}
  </div>

  <div class="card">
    <div class="muted" style="margin-bottom:8px;">モデル別グラフ(全期間累計)</div>
    <div class="legend">
      <div class="legend-item"><span class="legend-swatch" style="background:var(--series-high)"></span>High</div>
      <div class="legend-item"><span class="legend-swatch" style="background:var(--series-low)"></span>Low</div>
      <div class="legend-item"><span class="legend-swatch" style="background:var(--series-code)"></span>Code</div>
    </div>
    {model_bar_rows}
  </div>

  <div class="card">
    <div class="muted" style="margin-bottom:8px;">モデル別(全期間累計)</div>
    <table>
      <tr><th>ユーザー</th><th>High</th><th>Low</th><th>Code</th><th>合計</th></tr>
      {model_table_rows}
    </table>
  </div>

  <div class="card">
    <div class="muted" style="margin-bottom:8px;">参考: WebUI(チャット画面) / API利用の内訳(全期間累計)</div>
    <table>
      <tr><th>ユーザー</th><th>WebUI</th><th>API</th><th>合計</th></tr>
      {webui_api_rows}
    </table>
  </div>
</div>
""" + THEME_TOGGLE_SCRIPT_BLOCK

RANKING_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DCC AI - ランキング</title>
<style>""" + THEME_CSS_BLOCK + """
  .viz-root {{ max-width: 560px; margin: 0 auto; padding: 60px 20px; }}
  h1 {{ font-size: 1.3rem; margin: 0; }}
  .muted {{ color: var(--text-secondary); font-size: 0.85rem; }}
  .card {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
           padding: 20px; margin: 16px 0; }}
  .rank-row {{ display: flex; align-items: center; gap: 10px; margin: 10px 0; }}
  .rank-row.me {{ font-weight: 700; }}
  .rank-num {{ width: 22px; flex: none; text-align: right; font-size: 0.8rem;
               color: var(--text-muted); font-variant-numeric: tabular-nums; }}
  .rank-name {{ width: 110px; flex: none; font-size: 0.85rem;
               overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .rank-track {{ flex: 1; background: var(--gridline); border-radius: 4px; height: 22px; overflow: hidden; }}
  .rank-fill {{ height: 100%; background: var(--bar-fill); border-radius: 0 4px 4px 0; }}
  .rank-total {{ width: 72px; flex: none; text-align: right; font-size: 0.82rem;
                color: var(--text-secondary); font-variant-numeric: tabular-nums; }}
</style>
<div class="viz-root">
  <div class="top-bar">
    <h1>🏆 トークン消費ランキング</h1>
    <div class="top-bar-actions">
      <a class="pill-btn" href="/">&larr; 自分のページ</a>
      <button class="pill-btn" id="theme-toggle" type="button">🌓 表示切替</button>
    </div>
  </div>

  <h2 style="font-size:1rem; margin:24px 0 0;">今月の消費ランキング</h2>
  <p class="muted">今月分、WebUI+API合算です。</p>
  <div class="card">
    {monthly_rank_rows}
  </div>

  <h2 style="font-size:1rem; margin:24px 0 0;">全体の合計ランキング</h2>
  <p class="muted">全期間累計、WebUI+API合算です。</p>
  <div class="card">
    {rank_rows}
  </div>
</div>
""" + THEME_TOGGLE_SCRIPT_BLOCK


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="text/html; charset=utf-8", extra_headers=None):
        payload = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self._route()
        except Exception as e:
            # Cloudflareのエッジは4xx/5xxをオリジンの本文ごと自前のエラーページに
            # 差し替えることがあるため、このページは常に200を返し本文で状態を伝える
            self._send(200, f"内部エラー: {e}")

    def _route(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/health":
            self._send(200, "ok", content_type="text/plain")
            return

        if path == "/login":
            params = urllib.parse.urlencode({
                "client_id": DISCORD_CLIENT_ID,
                "redirect_uri": f"{PUBLIC_BASE_URL}/callback",
                "response_type": "code",
                "scope": "identify guilds",
            })
            self._send(302, "", extra_headers={"Location": f"https://discord.com/oauth2/authorize?{params}"})
            return

        if path == "/callback":
            code = qs.get("code", [None])[0]
            if not code:
                self._send(200, "Bad request: missing code")
                return
            try:
                token_resp = http_post_form_json(
                    "https://discord.com/api/v10/oauth2/token",
                    {
                        "client_id": DISCORD_CLIENT_ID,
                        "client_secret": DISCORD_CLIENT_SECRET,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": f"{PUBLIC_BASE_URL}/callback",
                    },
                )
                access_token = token_resp["access_token"]
                me = http_get_json(
                    "https://discord.com/api/v10/users/@me",
                    {"Authorization": f"Bearer {access_token}"},
                )
                guilds_resp = http_get_json(
                    "https://discord.com/api/v10/users/@me/guilds",
                    {"Authorization": f"Bearer {access_token}"},
                )
                guild_ids = [g["id"] for g in guilds_resp]
            except Exception as e:
                self._send(200, f"Discordとの通信に失敗しました: {e}")
                return

            if REQUIRED_GUILD_ID and REQUIRED_GUILD_ID not in guild_ids:
                self._send(
                    200,
                    "<h2>DCC AI 使用量ページは DCC 部員専用です</h2>"
                    "<p>DCC の Discord サーバーに参加してから、もう一度お試しください。</p>",
                )
                return

            session = make_session(me["id"])
            cookie = (
                f"{COOKIE_NAME}={session}; Path=/; HttpOnly; Secure; "
                f"SameSite=Lax; Max-Age={SESSION_TTL}"
            )
            self._send(302, "", extra_headers={"Location": "/", "Set-Cookie": cookie})
            return

        # ここから先は要ログイン
        discord_id = read_session(self.headers.get("Cookie", ""))
        if not discord_id:
            self._send(302, "", extra_headers={"Location": "/login"})
            return

        row = find_openwebui_user(discord_id)
        if not row:
            self._send(
                200,
                "Open WebUI側にアカウントが見つかりませんでした。"
                "一度 <a href=\"https://ai.shu-dcc.net\">ai.shu-dcc.net</a> にログインしてから再度お試しください。",
            )
            return
        user_id, name, role = row

        if path == "/ranking":
            names = all_openwebui_users()

            def label_for(uid):
                display_name, email = names.get(uid, (None, None))
                return display_name or email or uid

            grouped = litellm_all_users_grouped()
            ranking_data = sorted(
                (
                    (label_for(uid), buckets["webui"] + buckets["api"], uid)
                    for uid, buckets in grouped.items()
                    if buckets["webui"] + buckets["api"] > 0
                ),
                key=lambda r: -r[1],
            )
            rank_rows = _rank_rows_html(ranking_data, user_id)

            monthly_totals = total_tokens_this_month_all_users()
            monthly_ranking_data = sorted(
                ((label_for(uid), total, uid) for uid, total in monthly_totals.items() if total > 0),
                key=lambda r: -r[1],
            )
            monthly_rank_rows = _rank_rows_html(monthly_ranking_data, user_id)

            self._send(200, RANKING_TEMPLATE.format(rank_rows=rank_rows, monthly_rank_rows=monthly_rank_rows))
            return

        if path == "/admin":
            if role != "admin":
                self._send(200, "このページは管理者専用です。")
                return
            names = all_openwebui_users()

            def label_for(uid):
                display_name, email = names.get(uid, (None, None))
                return display_name or email or uid

            # ---- 日別ランキング(白黒・単色バー、日付ページ送り) ----
            date_str = qs.get("date", [None])[0] or ""
            if not DATE_RE.match(date_str):
                date_str = jst_today_str()
            daily = litellm_daily_ranking(date_str)
            daily_rows_data = sorted(
                ((label_for(uid), total) for uid, total in daily.items() if total > 0),
                key=lambda r: -r[1],
            )
            max_daily = max((t for _, t in daily_rows_data), default=1)
            rank_rows = "".join(
                f'<div class="rank-row"><div class="rank-num">{i}</div>'
                f'<div class="rank-name" title="{label}">{label}</div>'
                f'<div class="rank-track"><div class="rank-fill" '
                f'style="width:{round(total / max_daily * 100, 1)}%;"></div></div>'
                f'<div class="rank-total">{total:,}</div></div>'
                for i, (label, total) in enumerate(daily_rows_data, start=1)
            ) or '<p class="muted">この日の利用はありません</p>'
            date_label = date_str
            prev_date = shift_date_str(date_str, -1)
            next_date = shift_date_str(date_str, 1)

            # ---- 今月のAPI利用状況(月間上限に対する消費率、ユーザー単位) ----
            monthly_api = api_tokens_this_month_all_users()
            monthly_api_data = sorted(
                ((label_for(uid), used) for uid, used in monthly_api.items() if used > 0),
                key=lambda r: -r[1],
            )

            def _limit_row(label, used):
                pct, _remaining_pct, color = _limit_bar_style(used, MONTHLY_TOKEN_LIMIT)
                return (
                    f'<div class="limit-row"><div class="limit-name" title="{label}">{label}</div>'
                    f'<div class="limit-track"><div class="limit-fill" style="width:{pct}%; background:{color};"></div></div>'
                    f'<div class="limit-pct">{used:,} ({pct}%)</div></div>'
                )

            monthly_api_rows = "".join(
                _limit_row(label, used) for label, used in monthly_api_data
            ) or '<p class="muted">今月のAPI利用はまだありません</p>'

            # ---- モデル別(全期間累計テーブル) ----
            by_model_per_user = litellm_all_users_by_model()
            model_rows_data = []
            for uid, tiers in by_model_per_user.items():
                total = sum(tiers.values())
                if total <= 0:
                    continue
                model_rows_data.append((label_for(uid), tiers, total))
            model_rows_data.sort(key=lambda r: -r[2])
            model_table_rows = "".join(
                f"<tr><td>{label}</td><td>{tiers.get('High', 0):,}</td>"
                f"<td>{tiers.get('Low', 0):,}</td><td>{tiers.get('Code', 0):,}</td><td>{total:,}</td></tr>"
                for label, tiers, total in model_rows_data
            ) or "<tr><td colspan=5>データなし</td></tr>"

            max_model_total = max((r[2] for r in model_rows_data), default=1)

            def _model_bar_row(label, tiers, total):
                pct = round(total / max_model_total * 100, 1)
                segs = "".join(
                    f'<div class="seg seg-{cls}" style="flex:{val} 0 0;" '
                    f'title="{name}: {val:,} トークン"></div>'
                    for cls, name, val in (
                        ("high", "High", tiers.get("High", 0)),
                        ("low", "Low", tiers.get("Low", 0)),
                        ("code", "Code", tiers.get("Code", 0)),
                    )
                    if val > 0
                )
                return (
                    f'<div class="bar-row"><div class="bar-name" title="{label}">{label}</div>'
                    f'<div class="bar-track"><div class="bar-fill" style="width:{pct}%;">{segs}</div></div>'
                    f'<div class="bar-total">{total:,}</div></div>'
                )

            model_bar_rows = "".join(
                _model_bar_row(label, tiers, total) for label, tiers, total in model_rows_data
            ) or '<p class="muted">データなし</p>'

            # ---- 参考: WebUI/API内訳(既存の集計をそのまま流用) ----
            grouped = litellm_all_users_grouped()
            webui_api_data = []
            for uid, buckets in grouped.items():
                webui_api_data.append((label_for(uid), buckets["webui"], buckets["api"]))
            webui_api_data.sort(key=lambda r: -(r[1] + r[2]))
            webui_api_rows = "".join(
                f"<tr><td>{label}</td><td>{webui:,}</td><td>{api:,}</td>"
                f"<td>{webui + api:,}</td></tr>"
                for label, webui, api in webui_api_data
            ) or "<tr><td colspan=4>データなし</td></tr>"

            self._send(200, ADMIN_TEMPLATE.format(
                date_label=date_label,
                prev_date=prev_date,
                next_date=next_date,
                rank_rows=rank_rows,
                monthly_limit=MONTHLY_TOKEN_LIMIT,
                monthly_api_rows=monthly_api_rows,
                reset_note=token_limit_reset_note(),
                model_bar_rows=model_bar_rows,
                model_table_rows=model_table_rows,
                webui_api_rows=webui_api_rows,
            ))
            return

        webui_total, webui_by_model = litellm_total_and_by_model(user_id)
        api_total, api_by_model = litellm_total_and_by_model(f"{user_id}:api")

        by_model = dict(webui_by_model)
        for m, t in api_by_model.items():
            by_model[m] = by_model.get(m, 0) + t

        model_rows = "".join(
            f'<div class="row"><span>{m}</span><span>{t:,}</span></div>'
            for m, t in sorted(by_model.items(), key=lambda x: -x[1])
        ) or '<div class="muted">データなし</div>'

        admin_link = (
            '<p class="muted"><a href="/admin">管理者用: 全ユーザーの使用量一覧</a></p>'
            if role == "admin" else ""
        )
        api_month = api_tokens_this_month(user_id)
        limit_pct, limit_remaining_pct, limit_color = _limit_bar_style(api_month, MONTHLY_TOKEN_LIMIT)
        body = PAGE_TEMPLATE.format(
            name=name or "you",
            total=webui_total + api_total,
            webui=webui_total,
            api=api_total,
            model_rows=model_rows,
            admin_link=admin_link,
            monthly_limit=MONTHLY_TOKEN_LIMIT,
            api_month=api_month,
            limit_pct=limit_pct,
            limit_remaining_pct=limit_remaining_pct,
            limit_color=limit_color,
            reset_note=token_limit_reset_note(),
        )
        self._send(200, body)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"dccai-usage listening on :{PORT}")
    server.serve_forever()
