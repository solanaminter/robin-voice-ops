"""Human-approval gates. Irreversible or revenue-impacting actions are created
in a PENDING state and only take effect when a human dispatcher approves them
in the dashboard. Nothing consequential happens on voice alone."""
from __future__ import annotations

import time
from typing import Any, Optional

from . import audit as audit_log
from .db import DB, _now, _uid

# Actions that REQUIRE human approval before they take effect.
GATED_ACTIONS = {"book_appointment", "escalate_to_human"}


def request_approval(db: DB, kind: str, ref_id: str, action: str,
                     payload: dict[str, Any], actor: str = "voice-agent") -> dict[str, Any]:
    ap = {"id": _uid("appr"), "kind": kind, "ref_id": ref_id, "action": action,
          "payload_json": __import__("json").dumps(payload, default=str),
          "status": "pending", "created_at": _now(), "decided_at": None,
          "decided_by": None, "reason": None}
    db.insert("approvals", ap)
    audit_log.record(db, actor, "approval.requested",
                     {"approval_id": ap["id"], "kind": kind, "ref_id": ref_id, "action": action})
    return ap


def decide(db: DB, approval_id: str, approved: bool, decided_by: str = "dispatcher",
           reason: str = "") -> Optional[dict[str, Any]]:
    ap = get(db, approval_id)
    if not ap or ap["status"] != "pending":
        return None
    status = "approved" if approved else "rejected"
    patch = {"status": status, "decided_at": _now(), "decided_by": decided_by, "reason": reason}
    if db.mode == "memory":
        db.mem_update("approvals", {"id": approval_id}, patch)
        ap = get(db, approval_id)
    else:
        db.execute("UPDATE approvals SET status=?, decided_at=?, decided_by=?, reason=? WHERE id=?",
                   (status, patch["decided_at"], decided_by, reason, approval_id))
        ap = get(db, approval_id)
    audit_log.record(db, decided_by, f"approval.{status}",
                     {"approval_id": approval_id, "kind": ap["kind"], "ref_id": ap["ref_id"]})
    # Apply the gated side effect only on approval.
    if approved:
        _apply(db, ap, decided_by)
    else:
        _revert(db, ap, decided_by)
    return ap


def get(db: DB, approval_id: str) -> Optional[dict[str, Any]]:
    if db.mode == "memory":
        rs = db.mem_find("approvals", id=approval_id)
        return rs[0] if rs else None
    return db.one("SELECT * FROM approvals WHERE id=?", (approval_id,))


def pending(db: DB) -> list[dict[str, Any]]:
    if db.mode == "memory":
        return [r for r in db._mem["approvals"] if r["status"] == "pending"]
    return db.rows("SELECT * FROM approvals WHERE status='pending' ORDER BY created_at")


def _apply(db: DB, ap: dict[str, Any], decided_by: str) -> None:
    """Take the gated effect live now that a human approved it."""
    import json
    payload = json.loads(ap["payload_json"])
    if ap["kind"] == "appointment":
        _set(db, "appointments", ap["ref_id"], {"status": "confirmed", "decided_at": _now(), "decided_by": decided_by})
        _set(db, "slots", payload.get("slot_id", ""), {"taken": True})
        audit_log.record(db, decided_by, "appointment.confirmed",
                         {"appointment_id": ap["ref_id"], "slot_id": payload.get("slot_id")})
    elif ap["kind"] == "escalation":
        audit_log.record(db, decided_by, "escalation.accepted",
                         {"ticket": ap["ref_id"], "callback": payload.get("callback_number")})


def _revert(db: DB, ap: dict[str, Any], decided_by: str) -> None:
    if ap["kind"] == "appointment":
        _set(db, "appointments", ap["ref_id"], {"status": "rejected", "decided_at": _now(), "decided_by": decided_by})
        audit_log.record(db, decided_by, "appointment.rejected", {"appointment_id": ap["ref_id"]})


def _set(db: DB, table: str, rid: str, patch: dict[str, Any]) -> None:
    if db.mode == "memory":
        db.mem_update(table, {"id": rid}, patch)
    else:
        sets = ", ".join(f"{k}=?" for k in patch)
        db.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*patch.values(), rid))
