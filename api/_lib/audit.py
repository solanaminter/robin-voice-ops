"""Hash-chained audit log. Every consequential action lands here, each entry
cryptographically linked to the previous one so tampering is detectable."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .db import DB


def _canonical(details: dict[str, Any]) -> str:
    return json.dumps(details, sort_keys=True, separators=(",", ":"), default=str)


def record(db: DB, actor: str, action: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    details = details or {}
    if db.mode == "memory":
        prev = db._mem["audit"][-1]["hash"] if db._mem["audit"] else "GENESIS"
        ts = time.time()
        h = hashlib.sha256(f"{prev}|{ts}|{actor}|{action}|{_canonical(details)}".encode()).hexdigest()
        entry = {"ts": ts, "actor": actor, "action": action,
                 "details_json": _canonical(details), "prev_hash": prev, "hash": h}
        db.insert("audit", entry)
        return entry
    last = db.one("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1")
    prev = last["hash"] if last else "GENESIS"
    ts = time.time()
    h = hashlib.sha256(f"{prev}|{ts}|{actor}|{action}|{_canonical(details)}".encode()).hexdigest()
    entry = {"ts": ts, "actor": actor, "action": action,
             "details_json": _canonical(details), "prev_hash": prev, "hash": h}
    db.insert("audit", entry)
    return entry


def verify_chain(db: DB) -> dict[str, Any]:
    """Recompute the chain; return ok flag + entry count + first bad seq."""
    if db.mode == "memory":
        entries = sorted(db._mem["audit"], key=lambda r: r.get("seq", 0))
    else:
        entries = db.rows("SELECT seq, ts, actor, action, details_json, prev_hash, hash FROM audit ORDER BY seq")
    prev = "GENESIS"
    for e in entries:
        expect = hashlib.sha256(
            f"{prev}|{e['ts']}|{e['actor']}|{e['action']}|{e['details_json']}".encode()
        ).hexdigest()
        if e["prev_hash"] != prev or e["hash"] != expect:
            return {"ok": False, "entries": len(entries), "bad_seq": e.get("seq")}
        prev = e["hash"]
    return {"ok": True, "entries": len(entries), "bad_seq": None}


def recent(db: DB, limit: int = 50) -> list[dict[str, Any]]:
    if db.mode == "memory":
        entries = sorted(db._mem["audit"], key=lambda r: r.get("seq", 0), reverse=True)[:limit]
        return [dict(e, details=json.loads(e["details_json"])) for e in entries]
    rows = db.rows(
        "SELECT seq, ts, actor, action, details_json, prev_hash, hash FROM audit ORDER BY seq DESC LIMIT ?",
        (limit,),
    )
    return [dict(r, details=json.loads(r["details_json"])) for r in rows]
