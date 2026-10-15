"""Local authorization checks. Never infer consent from hook payload or model text."""
from pathlib import Path
from contextlib import closing
import hashlib
import sqlite3


def scope_hash(scope):
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()


def check_grant(state_dir, project, demand, task, digest, *, synthetic=False):
    """Read an existing recorded grant; no database creation or authorization."""
    if not isinstance(digest,str) or len(digest)!=64:
        raise ValueError("Supply the recorded scope SHA256; never infer authorization")
    database=Path(state_dir)/"copilot-ledger.sqlite3"
    with closing(sqlite3.connect(database.resolve(strict=True).as_uri()+"?mode=ro",uri=True,timeout=.1)) as conn:
        row=conn.execute("SELECT scope,synthetic_only FROM grants WHERE project=? AND demand=? AND task_id=?",(project,demand,task)).fetchone()
        if row is None:
            raise ValueError("No recorded human authorization for this project/demand/task")
        table=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='grant_revocations'").fetchone()
        if table and conn.execute("SELECT 1 FROM grant_revocations WHERE project=? AND demand=? AND task_id=?",(project,demand,task)).fetchone():
            raise ValueError("Authorization revoked; do not retry with another request")
        if scope_hash(row[0])!=digest:
            raise ValueError("Authorization scope differs from the requested scope")
        if row[1] and not synthetic:
            raise ValueError("Authorization permits synthetic content only")
    return True
