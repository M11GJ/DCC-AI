#!/usr/bin/env python3
"""Compare the JST monthly API token counter with LiteLLM spend logs."""

import json
import os
from datetime import datetime, timedelta, timezone

import psycopg2


JST = timezone(timedelta(hours=9), "JST")
COUNTER = os.environ.get("TOKEN_USAGE_FILE", "/webui-data/dcc_ai_token_usage.json")
RESET_LOG = os.environ.get("TOKEN_RESET_LOG_FILE", "/webui-data/dcc_ai_token_usage_resets.jsonl")


def main():
    now = datetime.now(JST)
    start = datetime(now.year, now.month, 1, tzinfo=JST)
    end = (
        datetime(now.year + 1, 1, 1, tzinfo=JST)
        if now.month == 12
        else datetime(now.year, now.month + 1, 1, tzinfo=JST)
    )
    with open(COUNTER, encoding="utf-8") as f:
        file_values = {k: int(v) for k, v in (json.load(f).get(now.strftime("%Y-%m"), {})).items()}

    reset_offsets = {}
    try:
        with open(RESET_LOG, encoding="utf-8") as f:
            for line in f:
                event = json.loads(line)
                if event.get("month") != now.strftime("%Y-%m"):
                    continue
                for user_id, total in event.get("previous_usage", {}).items():
                    reset_offsets[user_id] = reset_offsets.get(user_id, 0) + int(total)
    except FileNotFoundError:
        pass

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT end_user, COALESCE(sum(total_tokens), 0) FROM "LiteLLM_SpendLogs" '
                'WHERE end_user LIKE %s AND "startTime" >= %s AND "startTime" < %s GROUP BY end_user',
                ("%:api", start.astimezone(timezone.utc), end.astimezone(timezone.utc)),
            )
            db_values = {row[0][:-4]: int(row[1]) for row in cur.fetchall()}
    finally:
        conn.close()

    users = set(file_values) | set(reset_offsets) | set(db_values)
    accounted_values = {
        user: file_values.get(user, 0) + reset_offsets.get(user, 0) for user in users
    }
    diffs = {user: accounted_values.get(user, 0) - db_values.get(user, 0) for user in users}
    print(f"month={now.strftime('%Y-%m')} users={len(users)}")
    print(
        f"active_limit_total={sum(file_values.values())} "
        f"reset_offset_total={sum(reset_offsets.values())} db_total={sum(db_values.values())}"
    )
    print(f"mismatches={sum(value != 0 for value in diffs.values())} max_abs_diff={max(map(abs, diffs.values()), default=0)}")
    if any(diffs.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
