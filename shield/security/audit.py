"""Tamper-evident, hash-chained audit log.

Every access to data, every agent decision, every export and every feedback
submission appends an entry whose hash covers the previous entry's hash. Any
later modification or deletion breaks the chain, which ``verify_chain``
detects. In production the chain head is periodically anchored to an
external, in-country write-once store (e.g. the national archive or a
WORM bucket) so even a database administrator cannot rewrite history.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from ..db import get_conn, write_lock

GENESIS = "0" * 64


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(prev: str, ts: str, actor: str, role: str, action: str, resource: str, details: str) -> str:
    payload = json.dumps([prev, ts, actor, role, action, resource, details], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def record(actor: str, role: str, action: str, resource: str = "", details: dict | None = None,
           con: sqlite3.Connection | None = None) -> str:
    con = con or get_conn()
    det = json.dumps(details or {}, sort_keys=True, default=str)
    with write_lock():
        row = con.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        ts = _now()
        h = _digest(prev, ts, actor, role, action, resource, det)
        con.execute("INSERT INTO audit_log(ts, actor, role, action, resource, details, prev_hash, hash) "
                    "VALUES (?,?,?,?,?,?,?,?)", (ts, actor, role, action, resource, det, prev, h))
        con.commit()
    return h


def verify_chain(con: sqlite3.Connection | None = None) -> dict:
    con = con or get_conn()
    prev = GENESIS
    n = 0
    for r in con.execute("SELECT * FROM audit_log ORDER BY id"):
        n += 1
        expected = _digest(prev, r["ts"], r["actor"], r["role"], r["action"], r["resource"], r["details"])
        if r["prev_hash"] != prev or r["hash"] != expected:
            return {"valid": False, "entries": n, "broken_at": r["id"],
                    "message": f"Chain broken at entry {r['id']}: record was altered or removed."}
        prev = r["hash"]
    return {"valid": True, "entries": n, "head": prev, "message": "All entries verified."}
