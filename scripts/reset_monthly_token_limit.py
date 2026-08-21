#!/usr/bin/env python3
"""Reset the active JST monthly API allowance while preserving all usage records."""

import argparse
import fcntl
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9), "JST")
COUNTER = Path(os.environ.get("TOKEN_USAGE_FILE", "/app/backend/data/dcc_ai_token_usage.json"))
RESET_LOG = Path(
    os.environ.get("TOKEN_RESET_LOG_FILE", "/app/backend/data/dcc_ai_token_usage_resets.jsonl")
)
SNAPSHOT_DIR = Path(
    os.environ.get(
        "TOKEN_RESET_SNAPSHOT_DIR", "/app/backend/data/dcc_ai_token_usage_reset_snapshots"
    )
)


def atomic_json_write(path: Path, value) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reason", default="モデル更新に伴う制限枠リセット")
    args = parser.parse_args()

    now = datetime.now(JST)
    month = now.strftime("%Y-%m")
    reset_at = now.isoformat()
    COUNTER.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SNAPSHOT_DIR, 0o700)

    with open(str(COUNTER) + ".lock", "a+") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            try:
                data = json.loads(COUNTER.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}
            previous_usage = {
                user_id: int(total) for user_id, total in data.get(month, {}).items()
            }
            snapshot = {
                "event": "monthly_token_limit_reset",
                "month": month,
                "reset_at": reset_at,
                "reason": args.reason,
                "previous_usage": previous_usage,
                "previous_total": sum(previous_usage.values()),
                "previous_users": len(previous_usage),
            }
            snapshot_path = SNAPSHOT_DIR / f"{now.strftime('%Y%m%d_%H%M%S')}.json"
            atomic_json_write(snapshot_path, snapshot)

            RESET_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(RESET_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(RESET_LOG, 0o600)

            data[month] = {}
            atomic_json_write(COUNTER, data)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)

    print(
        f"reset month={month} users={len(previous_usage)} "
        f"previous_total={sum(previous_usage.values())} snapshot={snapshot_path}"
    )


if __name__ == "__main__":
    main()

