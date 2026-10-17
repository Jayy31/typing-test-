"""Optional Cloudflare R2 backup of the history store (off unless R2_BACKUP_ENABLED=true).

Uploads Parquet files that are new or changed, and a consistent copy of the DuckDB file
(taken while holding the writer lock). R2 is S3-compatible; credentials only from env.
"""

import logging
import shutil
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.history.store import connect, db_path

log = logging.getLogger(__name__)


def r2_client() -> Any:
    import boto3

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=f"https://{s.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=s.r2_access_key,
        aws_secret_access_key=s.r2_secret_key,
        region_name="auto",
    )


def backup(client: Any | None = None) -> int:
    s = get_settings()
    if not s.r2_backup_enabled:
        return 0
    if not (s.r2_account_id and s.r2_access_key and s.r2_secret_key and s.r2_bucket):
        raise RuntimeError(
            "R2 backup enabled but R2_ACCOUNT_ID / R2_ACCESS_KEY / R2_SECRET_KEY / R2_BUCKET missing"
        )
    client = client or r2_client()
    root = Path(s.history_data_dir)
    uploaded = 0
    with connect(write=True) as con:
        done = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT key, value FROM store_meta WHERE key LIKE 'r2:%'"
            ).fetchall()
        }
        for f in sorted((root / "parquet").rglob("*.parquet")):
            rel = f.relative_to(root).as_posix()
            stamp = f"{f.stat().st_size}:{int(f.stat().st_mtime)}"
            if done.get(f"r2:{rel}") == stamp:
                continue
            client.upload_file(str(f), s.r2_bucket, rel)
            con.execute("INSERT OR REPLACE INTO store_meta VALUES (?, ?)", [f"r2:{rel}", stamp])
            uploaded += 1
        con.execute("CHECKPOINT")
        snap = db_path().with_suffix(".backup.duckdb")
        shutil.copyfile(db_path(), snap)  # copy while we hold the only writer lock
    client.upload_file(str(snap), s.r2_bucket, "history.duckdb")
    snap.unlink(missing_ok=True)
    return uploaded + 1


def restore(client: Any | None = None) -> int:
    """New VM / lost volume: download every backed-up file into HISTORY_DATA_DIR. Run with the
    worker stopped (it overwrites history.duckdb)."""
    s = get_settings()
    client = client or r2_client()
    root = Path(s.history_data_dir)
    n = 0
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=s.r2_bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            target = db_path() if key == "history.duckdb" else root / key
            target.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(s.r2_bucket, key, str(target))
            n += 1
    return n


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["restore"]:
        print(f"restored {restore()} files")
    else:
        print(f"uploaded {backup()} files")
