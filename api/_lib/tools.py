"""Tool implementations behind the voice agent's function tools.

Every function here is what actually runs when the agent calls a tool —
real database reads/writes, never stubs. Gated actions (booking, escalation)
create PENDING records and wait for human approval; nothing irreversible
happens on voice alone.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

from . import audit as audit_log
from . import approvals
from .db import DB, _now, _uid


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return raw.strip()


# ---- caller memory ----
def get_caller(db: DB, phone: str) -> Optional[dict[str, Any]]:
    phone = normalize_phone(phone)
    if db.mode == "memory":
        rs = db.mem_find("callers", phone=phone)
        return rs[0] if rs else None
    return db.one("SELECT * FROM callers WHERE phone=?", (phone,))


def upsert_caller(db: DB, phone: str, name: str = "", notes: str = "") -> dict[str, Any]:
    phone = normalize_phone(phone)
    existing = get_caller(db, phone)
    if existing:
        patch = {"last_seen": _now(), "visit_count": existing.get("visit_count", 0) + 1}
        if name and not existing.get("name"):
            patch["name"] = name
        if db.mode == "memory":
            db.mem_update("callers", {"id": existing["id"]}, patch)
        else:
            db.execute("UPDATE callers SET last_seen=?, visit_count=? WHERE id=?",
                       (patch["last_seen"], patch["visit_count"], existing["id"]))
        return get_caller(db, phone) or existing
    row = {"id": _uid("caller"), "phone": phone, "name": name, "first_seen": _now(),
           "last_seen": _now(), "visit_count": 1, "notes": notes, "prefs_json": "{}"}
    db.insert("callers", row)
    return row


def caller_context_text(db: DB, phone: str) -> str:
    """Injected into the agent's context so repeat callers are recognized."""
    c = get_caller(db, phone)
    if not c or (c.get("visit_count", 0) <= 1 and not c.get("notes")):
        return "New caller — no prior history. Greet warmly and ask how you can help."
    name = c.get("name") or "this caller"
    prefs = json.loads(c.get("prefs_json") or "{}")
    pref_txt = ", ".join(f"{k}: {v}" for k, v in prefs.items()) or "none recorded"
    return (f"RETURNING CALLER: {name} ({c['phone']}), {c.get('visit_count', 1)} prior contacts. "
            f"Notes: {c.get('notes') or 'none'}. Preferences: {pref_txt}. "
            f"Greet them by name and offer to pick up where things left off.")


# ---- availability & booking ----
def check_availability(db: DB, service: str, date: str = "", time_window: str = "") -> dict[str, Any]:
    if db.mode == "memory":
        slots = [s for s in db._mem["slots"]
                 if s["service"].lower() == service.lower() and not s["taken"]]
        if date:
            slots = [s for s in slots if s["slot_start"].startswith(date)]
        if time_window and time_window != "any":
            slots = [s for s in slots if time_window in s["id"]]
        return {"service": service, "open_slots": [
            {"slot_id": s["id"], "start": s["slot_start"], "end": s["slot_end"]} for s in slots[:6]]}
    q = "SELECT id, slot_start, slot_end FROM slots WHERE LOWER(service)=LOWER(?) AND NOT taken"
    params: list[Any] = [service]
    if date:
        q += " AND slot_start LIKE ?"; params.append(date + "%")
    if time_window and time_window != "any":
        q += " AND id LIKE ?"; params.append(f"%_{time_window}")
    rows = db.rows(q + " ORDER BY slot_start LIMIT 6", tuple(params))
    return {"service": service, "open_slots": [
        {"slot_id": r["id"], "start": r["slot_start"], "end": r["slot_end"]} for r in rows]}


def book_appointment(db: DB, service: str, slot_id: str, name: str,
                     phone: str, notes: str = "") -> dict[str, Any]:
    """Create a PENDING booking + approval request. Takes effect only on approval."""
    phone = normalize_phone(phone)
    caller = upsert_caller(db, phone, name)
    slot = (db.mem_find("slots", id=slot_id) or [None])[0] if db.mode == "memory" \
        else db.one("SELECT * FROM slots WHERE id=?", (slot_id,))
    if not slot or slot.get("taken"):
        return {"status": "error", "message": "That slot is no longer available. Please check availability again."}
    # Service is authoritative from the slot (LLM copies slot_id verbatim; never normalizes).
    service = service or slot.get("service", "General")
    appt = {"id": _uid("appt"), "caller_id": caller["id"], "customer_name": name, "phone": phone,
            "service": service, "slot_start": slot["slot_start"], "slot_end": slot["slot_end"],
            "status": "pending", "notes": notes, "created_at": _now(),
            "decided_at": None, "decided_by": None}
    db.insert("appointments", appt)
    ap = approvals.request_approval(db, kind="appointment", ref_id=appt["id"],
                                    action="book_appointment",
                                    payload={"slot_id": slot_id, "service": service,
                                             "customer": name, "phone": phone})
    audit_log.record(db, "voice-agent", "appointment.requested",
                     {"appointment_id": appt["id"], "slot": slot_id, "approval_id": ap["id"]})
    return {"status": "pending_approval", "appointment_id": appt["id"],
            "approval_id": ap["id"], "slot": f"{slot['slot_start']}–{slot['slot_end']}",
            "message": ("Booking request received and is now pending dispatcher approval. "
                        "The slot is held; nothing is confirmed until a human approves it.")}


# ---- job status ----
def check_job_status(db: DB, phone: str = "", job_id: str = "") -> dict[str, Any]:
    if job_id:
        jobs = db.mem_find("jobs", id=job_id) if db.mode == "memory" \
            else ([db.one("SELECT * FROM jobs WHERE id=?", (job_id))] if db.one("SELECT * FROM jobs WHERE id=?", (job_id,)) else [])
        jobs = [j for j in jobs if j]
    else:
        phone = normalize_phone(phone)
        jobs = db.mem_find("jobs", phone=phone) if db.mode == "memory" \
            else db.rows("SELECT * FROM jobs WHERE phone=?", (phone,))
    if not jobs:
        return {"status": "not_found",
                "message": "I couldn't find a job for that. Could you double-check the phone number or job ID?"}
    j = jobs[0]
    audit_log.record(db, "voice-agent", "job_status.checked", {"job_id": j["id"]})
    return {"status": "found", "job_id": j["id"], "service": j["service"],
            "job_status": j["status"], "scheduled_for": j["scheduled_for"],
            "technician": j["technician"], "notes": j["notes"]}


# ---- FAQ / knowledge base ----
def lookup_faq(db: DB, query: str) -> dict[str, Any]:
    words = [w.lower() for w in re.findall(r"\w+", query or "") if len(w) > 2]
    scored: list[tuple[int, dict[str, Any]]] = []
    kbs = db._mem["kb"] if db.mode == "memory" else db.rows("SELECT * FROM kb")
    for kb in kbs:
        hay = f"{kb['question']} {kb['answer']} {kb['keywords']}".lower()
        score = sum(hay.count(w) for w in words)
        if score:
            scored.append((score, kb))
    scored.sort(key=lambda t: -t[0])
    top = [kb for _, kb in scored[:2]]
    if not top:
        return {"status": "no_match",
                "message": "I don't have a confident answer for that — I can take a message for the office or connect you with a dispatcher."}
    return {"status": "found", "answers": [
        {"question": kb["question"], "answer": kb["answer"]} for kb in top]}


# ---- service requests (non-irreversible: auto-approved, still audited) ----
def create_service_request(db: DB, category: str, description: str,
                           priority: str = "normal", phone: str = "", name: str = "") -> dict[str, Any]:
    phone = normalize_phone(phone)
    caller = upsert_caller(db, phone, name) if phone else None
    req = {"id": _uid("req"), "caller_id": caller["id"] if caller else None,
           "category": category, "description": description, "priority": priority,
           "status": "open", "created_at": _now()}
    db.insert("service_requests", req)
    audit_log.record(db, "voice-agent", "service_request.created",
                     {"request_id": req["id"], "category": category, "priority": priority})
    return {"status": "created", "request_id": req["id"],
            "message": f"Service request {req['id']} created with {priority} priority. The office will follow up."}


# ---- escalation (gated: hold mode, human accepts) ----
def escalate_to_human(db: DB, reason: str, callback_number: str, name: str = "") -> dict[str, Any]:
    phone = normalize_phone(callback_number)
    ticket = {"id": _uid("esc"), "caller_id": None, "category": "escalation",
              "description": f"{name}: {reason}".strip(": "), "priority": "urgent",
              "status": "awaiting_human", "created_at": _now()}
    db.insert("service_requests", ticket)
    ap = approvals.request_approval(db, kind="escalation", ref_id=ticket["id"],
                                    action="escalate_to_human",
                                    payload={"reason": reason, "callback_number": phone, "name": name})
    audit_log.record(db, "voice-agent", "escalation.requested",
                     {"ticket": ticket["id"], "approval_id": ap["id"], "callback": phone})
    return {"status": "pending_human", "ticket_id": ticket["id"], "approval_id": ap["id"],
            "message": ("I've flagged this as urgent for a human dispatcher. "
                        "They'll take over this call or call you right back.")}
