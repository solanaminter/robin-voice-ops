"""Offline unit tests for Robin Voice Ops core logic.

All tests run against the in-memory backend — no API keys, no network.
Run: .venv/bin/python -m pytest tests/ -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from _lib import approvals, audit as audit_log, tools
from _lib.db import DB
from _lib.session_config import build_session_update, PHASE_0_TOOLS, BOOK_TOOL


def fresh_db():
    for k in ("DATABASE_URL", "ROBIN_DB_PATH"):
        os.environ.pop(k, None)
    return DB()


def test_phone_normalization():
    assert tools.normalize_phone("(415) 555-0101") == "+14155550101"
    assert tools.normalize_phone("415 555 0101") == "+14155550101"
    assert tools.normalize_phone("+14155550101") == "+14155550101"


def test_availability_returns_slots():
    db = fresh_db()
    r = tools.check_availability(db, "Plumbing")
    assert r["open_slots"], "seed slots should exist"
    assert all("slot_id" in s for s in r["open_slots"])


def test_booking_is_gated_pending():
    db = fresh_db()
    slot = tools.check_availability(db, "Plumbing")["open_slots"][0]["slot_id"]
    r = tools.book_appointment(db, "Plumbing", slot, "Test User", "4155550199")
    assert r["status"] == "pending_approval"
    assert r["approval_id"]
    # Appointment exists but is NOT confirmed.
    appt = db.mem_find("appointments", id=r["appointment_id"])[0]
    assert appt["status"] == "pending"


def test_approval_approve_confirms_booking():
    db = fresh_db()
    slot = tools.check_availability(db, "Plumbing")["open_slots"][0]["slot_id"]
    r = tools.book_appointment(db, "Plumbing", slot, "Test User", "4155550199")
    ap = approvals.decide(db, r["approval_id"], True, decided_by="tester")
    assert ap["status"] == "approved"
    appt = db.mem_find("appointments", id=r["appointment_id"])[0]
    assert appt["status"] == "confirmed"
    assert not approvals.pending(db), "queue should be empty"


def test_approval_reject_releases_slot():
    db = fresh_db()
    slot = tools.check_availability(db, "Plumbing")["open_slots"][0]["slot_id"]
    r = tools.book_appointment(db, "Plumbing", slot, "Test User", "4155550199")
    approvals.decide(db, r["approval_id"], False, decided_by="tester", reason="double-booked")
    appt = db.mem_find("appointments", id=r["appointment_id"])[0]
    assert appt["status"] == "rejected"


def test_double_decide_is_rejected():
    db = fresh_db()
    slot = tools.check_availability(db, "Plumbing")["open_slots"][0]["slot_id"]
    r = tools.book_appointment(db, "Plumbing", slot, "Test User", "4155550199")
    approvals.decide(db, r["approval_id"], True, decided_by="tester")
    assert approvals.decide(db, r["approval_id"], False, decided_by="tester") is None


def test_audit_chain_verifies():
    db = fresh_db()
    slot = tools.check_availability(db, "Plumbing")["open_slots"][0]["slot_id"]
    r = tools.book_appointment(db, "Plumbing", slot, "Test User", "4155550199")
    approvals.decide(db, r["approval_id"], True, decided_by="tester")
    v = audit_log.verify_chain(db)
    assert v["ok"] and v["entries"] >= 4, v


def test_audit_chain_detects_tampering():
    db = fresh_db()
    audit_log.record(db, "tester", "test.action", {"x": 1})
    db._mem["audit"][-1]["details_json"] = '{"x": 2}'  # tamper
    assert not audit_log.verify_chain(db)["ok"]


def test_caller_memory_recognizes_returning():
    db = fresh_db()
    assert "New caller" in tools.caller_context_text(db, "4155550101")
    tools.upsert_caller(db, "4155550101", "Maria Lopez", notes="prefers mornings")
    tools.upsert_caller(db, "4155550101")
    ctx = tools.caller_context_text(db, "+14155550101")
    assert "RETURNING CALLER" in ctx and "Maria Lopez" in ctx


def test_job_status_lookup():
    db = fresh_db()
    r = tools.check_job_status(db, phone="4155550101")
    assert r["status"] == "found" and "en route" in r["job_status"]
    r2 = tools.check_job_status(db, phone="4155550999")
    assert r2["status"] == "not_found"


def test_faq_lookup():
    db = fresh_db()
    r = tools.lookup_faq(db, "what are your hours")
    assert r["status"] == "found" and "Monday" in r["answers"][0]["answer"]
    r2 = tools.lookup_faq(db, "zzz unknown topic qqq")
    assert r2["status"] == "no_match"


def test_escalation_is_gated():
    db = fresh_db()
    r = tools.escalate_to_human(db, "burst pipe", "4155550101", "Test User")
    assert r["status"] == "pending_human" and r["approval_id"]
    assert approvals.pending(db), "escalation must await a human"


def test_service_request_auto_approved_but_audited():
    db = fresh_db()
    r = tools.create_service_request(db, "Plumbing", "dripping faucet", "normal", "4155550101")
    assert r["status"] == "created"
    v = audit_log.verify_chain(db)
    assert v["ok"]


def test_session_config_phase_reveal():
    s0 = build_session_update("", phase=0)
    names0 = [t["name"] for t in s0["tools"]]
    assert "book_appointment" not in names0
    assert "check_availability" in names0
    s1 = build_session_update("", phase=1)
    assert "book_appointment" in [t["name"] for t in s1["tools"]]
    # Hold mode on the gated tools.
    byname = {t["name"]: t for t in s1["tools"]}
    assert byname["book_appointment"]["execution_mode"] == "hold"
    assert byname["escalate_to_human"]["execution_mode"] == "hold"
    assert byname["check_availability"]["execution_mode"] == "interactive"
    # Caller memory is injected into the prompt.
    s2 = build_session_update("RETURNING CALLER: Maria", phase=0)
    assert "RETURNING CALLER" in s2["system_prompt"]
    assert s0["input"]["keyterms"], "keyterms must be set"
    assert len(PHASE_0_TOOLS) <= 10, "keep tool set small for selection accuracy"
