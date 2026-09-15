"""POST /api/tool — single tool-execution endpoint used by the browser client.

The browser receives `tool.call` events from AssemblyAI and executes them by
POSTing {name, arguments} here. Gated tools (book_appointment, escalate_to_human)
return {status: "held", ...} — the browser must NOT send tool.result yet; it
polls /api/approvals until a human decides, then sends the final tool.result.

GET /api/tool?name=...&phase=0 returns the tool schemas for session.update.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import tools, approvals, audit as audit_log  # noqa: E402
from _lib.http import Handler  # noqa: E402
from _lib.session_config import PHASE_0_TOOLS, BOOK_TOOL  # noqa: E402
from _lib.store import get_db, demo_mode  # noqa: E402
from _lib import parse as _parse  # noqa: E402

GATED = {"book_appointment", "escalate_to_human"}


def _run(db, name, args):
    if name == "get_caller_profile":
        phone = _parse.parse_phone(args.get("phone", "")) or args.get("phone", "")
        c = tools.get_caller(db, phone)
        return {"status": "found" if c else "new_caller", "profile": c,
                "context": tools.caller_context_text(db, phone)}
    if name == "check_availability":
        # LLM copies caller speech verbatim into `request`; parse server-side.
        req = args.get("request", "") or " ".join(
            str(args.get(k, "")) for k in ("service", "date", "time_window"))
        service = _parse.parse_service(req)
        day = _parse.parse_date(req)
        window = _parse.parse_time_window(req)
        return tools.check_availability(db, service, day or "", window)
    if name == "check_job_status":
        ident = args.get("identifier", "") or args.get("phone", "") or args.get("job_id", "")
        phone = _parse.parse_phone(ident) or ""
        job_id = ident if ident.startswith("job_") else args.get("job_id", "")
        return tools.check_job_status(db, phone, job_id)
    if name == "lookup_faq":
        return tools.lookup_faq(db, args.get("query", ""))
    if name == "create_service_request":
        issue = args.get("issue", "") or args.get("description", "")
        category = _parse.parse_service(issue)
        return tools.create_service_request(db, category, issue, "normal",
                                            args.get("phone", ""), args.get("name", ""))
    if name == "book_appointment":
        phone_raw = args.get("phone", "")
        phone = _parse.parse_phone(phone_raw) or phone_raw
        return tools.book_appointment(db, args.get("service", ""), args.get("slot_id", ""),
                                      args.get("name", ""), phone,
                                      args.get("notes", ""))
    if name == "escalate_to_human":
        cb_raw = args.get("callback_number", "")
        return tools.escalate_to_human(db, args.get("reason", ""),
                                       _parse.parse_phone(cb_raw) or cb_raw,
                                       args.get("name", ""))
    if name == "respond_freely":
        return {"status": "ok", "message": "No action taken."}
    return {"status": "error", "message": f"Unknown tool: {name}"}


class handler(Handler):
    def do_GET(self):
        q = self.query()
        if q.get("schemas") == "1":
            phase = int(q.get("phase", "0") or 0)
            schemas = list(PHASE_0_TOOLS) + ([BOOK_TOOL] if phase >= 1 else [])
            self.send_json({"tools": schemas})
            return
        self.send_json({"error": "use ?schemas=1 to fetch tool schemas, or POST to execute"}, 400)

    def do_POST(self):
        db = get_db()
        body = self.read_json()
        name, args = body.get("name", ""), body.get("arguments", {}) or {}
        if not name:
            self.send_json({"error": "missing tool name"}, 400)
            return
        try:
            result = _run(db, name, args)
        except Exception as e:
            audit_log.record(db, "voice-agent", "tool.error", {"tool": name, "error": type(e).__name__})
            self.send_json({"status": "error", "message": "Tool failed; please try again."}, 500)
            return
        held = name in GATED and result.get("status", "").startswith("pending")
        self.send_json({"result": result, "held": held, "demo_mode": demo_mode(db),
                        "approval_id": result.get("approval_id")})
