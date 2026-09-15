"""GET /api/memory?phone= — caller profile + context text.
POST /api/memory — {phone, name?, notes?} identify/upsert the caller."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import tools  # noqa: E402
from _lib.http import Handler  # noqa: E402
from _lib.store import get_db  # noqa: E402


class handler(Handler):
    def do_GET(self):
        db = get_db()
        phone = self.query().get("phone", "")
        c = tools.get_caller(db, phone)
        self.send_json({"profile": c, "context": tools.caller_context_text(db, phone)})

    def do_POST(self):
        db = get_db()
        body = self.read_json()
        c = tools.upsert_caller(db, body.get("phone", ""), body.get("name", ""), body.get("notes", ""))
        self.send_json({"profile": c, "context": tools.caller_context_text(db, body.get("phone", ""))})
