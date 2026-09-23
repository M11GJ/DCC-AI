#!/usr/bin/env python3
"""Move current-month Jev usage out of the shared API counter exactly once."""

import argparse
import fcntl
import json
import os
import tempfile
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9), "JST")
DATA_DIR = Path(os.environ.get("WEBUI_DATA_DIR", "/app/backend/data"))
GENERAL_COUNTER = Path(
    os.environ.get("TOKEN_USAGE_FILE", str(DATA_DIR / "dcc_ai_token_usage.json"))
)
JEV_COUNTER = Path(
    os.environ.get("JEV_TOKEN_USAGE_FILE", str(DATA_DIR / "dcc_ai_jev_token_usage.json"))
)
JEV_USAGE_LOG = Path(
    os.environ.get("JEV_USAGE_FILE", str(DATA_DIR / "dcc_ai_jev_usage.jsonl"))
)
AUDIT_LOG = DATA_DIR / "dcc_ai_jev_limit_migrations.jsonl"
SNAPSHOT_DIR = DATA_DIR / "dcc_ai_jev_limit_migration_snapshots"


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def atomic_json_write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def jev_usage_for_month(month: str):
    totals = {}
    try:
        lines = JEV_USAGE_LOG.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return totals
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            timestamp = str(event.get("timestamp") or "").replace("Z", "+00:00")
            when = datetime.fromisoformat(timestamp)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when.astimezone(JST).strftime("%Y-%m") != month:
                continue
            end_user = str(event.get("end_user") or "")
            user_id = end_user[: -len(":api")] if end_user.endswith(":api") else end_user
            if not user_id:
                continue
            totals[user_id] = totals.get(user_id, 0) + int(event.get("total_tokens") or 0)
        except Exception:
            continue
    return totals


def already_applied(migration_id: str) -> bool:
    try:
        for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(line).get("migration_id") == migration_id:
                    return True
            except Exception:
                continue
    except FileNotFoundError:
        pass
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default=datetime.now(JST).strftime("%Y-%m"))
    args = parser.parse_args()
    month = args.month
    migration_id = f"jev-separate-limit-v1:{month}"

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SNAPSHOT_DIR, 0o700)

    lock_paths = sorted(
        {
            str(GENERAL_COUNTER) + ".lock",
            str(JEV_COUNTER) + ".lock",
            str(JEV_USAGE_LOG) + ".lock",
        }
    )
    with ExitStack() as stack:
        locks = [stack.enter_context(open(path, "a+")) for path in lock_paths]
        for lockf in locks:
            fcntl.flock(lockf, fcntl.LOCK_EX)

        if already_applied(migration_id):
            print(f"already_applied migration_id={migration_id}")
            return

        general = read_json(GENERAL_COUNTER)
        jev = read_json(JEV_COUNTER)
        general_month = {k: int(v or 0) for k, v in general.get(month, {}).items()}
        jev_month = {k: int(v or 0) for k, v in jev.get(month, {}).items()}
        logged_jev = jev_usage_for_month(month)

        transfer = {}
        for user_id, logged_total in logged_jev.items():
            amount = max(0, logged_total - jev_month.get(user_id, 0))
            if general_month.get(user_id, 0) < amount:
                raise RuntimeError(
                    f"shared counter is smaller than Jev usage for one user: "
                    f"shared={general_month.get(user_id, 0)} transfer={amount}"
                )
            transfer[user_id] = amount

        now = datetime.now(JST)
        snapshot = {
            "migration_id": migration_id,
            "created_at": now.isoformat(),
            "month": month,
            "general_before": general_month,
            "jev_before": jev_month,
            "jev_from_usage_log": logged_jev,
            "transfer": transfer,
        }
        snapshot_path = SNAPSHOT_DIR / f"{now.strftime('%Y%m%d_%H%M%S')}.json"
        atomic_json_write(snapshot_path, snapshot)

        for user_id, amount in transfer.items():
            general_month[user_id] = general_month.get(user_id, 0) - amount
            jev_month[user_id] = jev_month.get(user_id, 0) + amount
        general[month] = general_month
        jev[month] = jev_month
        atomic_json_write(GENERAL_COUNTER, general)
        atomic_json_write(JEV_COUNTER, jev)

        audit = {
            "migration_id": migration_id,
            "applied_at": now.isoformat(),
            "month": month,
            "transferred_total": sum(transfer.values()),
            "users": len([value for value in transfer.values() if value > 0]),
            "snapshot": str(snapshot_path),
        }
        with open(AUDIT_LOG, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(audit, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(AUDIT_LOG, 0o600)

    print(
        f"migrated month={month} users={audit['users']} "
        f"transferred_total={audit['transferred_total']} snapshot={snapshot_path}"
    )


if __name__ == "__main__":
    main()
