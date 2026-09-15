"""GET /api/session?phase=0&phone= — the session.update payload for the browser.

Single source of truth: built by api/_lib/session_config.py, with caller
memory injected from the cross-session callers table.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import tools  # noqa: E402
from _lib.http import Handler  # noqa: E402
from _lib.session_config import build_session_update, BUSINESS_NAME, BUSINESS_PHONE  # noqa: E402
from _lib.store import get_db  # noqa: E402


class handler(Handler):
    def do_GET(self):
        db = get_db()
        q = self.query()
        phase = int(q.get("phase", "0") or 0)
        phone = q.get("phone", "")
        ctx = tools.caller_context_text(db, phone) if phone else ""
        session = build_session_update(ctx, phase)
        self.send_json({"type": "session.update", "session": session,
                        "business": {"name": BUSINESS_NAME, "phone": BUSINESS_PHONE},
                        "caller_context": ctx})
