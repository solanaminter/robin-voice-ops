"""GET /api/approvals — list pending approval requests (dispatcher dashboard).
POST /api/approvals — {approval_id, approved, decided_by, reason} -> decide."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import approvals, tools  # noqa: E402
from _lib.http import Handler  # noqa: E402
from _lib.store import get_db  # noqa: E402


class handler(Handler):
    def do_GET(self):
        db = get_db()
        q = self.query()
        if q.get("id"):
            ap = approvals.get(db, q["id"])
            if not ap:
                self.send_json({"error": "not found"}, 404)
                return
            self.send_json({"approval": ap, "tool_result": _tool_result_for(db, ap)})
            return
        items = approvals.pending(db)
        # Attach a human-readable summary for the dashboard.
        out = []
        for ap in items:
            import json
            payload = json.loads(ap.get("payload_json") or "{}")
            out.append({**ap, "payload": payload})
        self.send_json({"pending": out})

    def do_POST(self):
        db = get_db()
        body = self.read_json()
        ap_id = body.get("approval_id", "")
        if not ap_id:
            self.send_json({"error": "missing approval_id"}, 400)
            return
        ap = approvals.decide(db, ap_id, bool(body.get("approved")),
                              decided_by=body.get("decided_by", "dispatcher"),
                              reason=body.get("reason", ""))
        if not ap:
            self.send_json({"error": "approval not found or already decided"}, 404)
            return
        # Build the final tool.result payload the browser will deliver to the agent.
        final = _tool_result_for(db, ap)
        self.send_json({"approval": ap, "tool_result": final})


def _tool_result_for(db, ap):
    final = {"status": ap["status"]}
    if ap["kind"] == "appointment":
        if db.mode == "memory":
            appt = (db.mem_find("appointments", id=ap["ref_id"]) or [None])[0]
        else:
            appt = db.one("SELECT * FROM appointments WHERE id=?", (ap["ref_id"],))
        if appt:
            final.update({
                "appointment_id": appt["id"],
                "slot": f"{appt['slot_start']}–{appt['slot_end']}",
                "message": (
                    f"Confirmed! Your visit is booked for {appt['slot_start']} to {appt['slot_end']}. "
                    "A confirmation text is on its way."
                ) if ap["status"] == "approved" else
                "The dispatcher couldn't confirm that slot. I can check the next available times.",
            })
    elif ap["kind"] == "escalation":
        final.update({"message": ("A dispatcher has taken over — you're being connected now.")
                      if ap["status"] == "approved" else
                      "The dispatcher is tied up; I've logged your urgent request and they'll call you back within 15 minutes."})
    return final
