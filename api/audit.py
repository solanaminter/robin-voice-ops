"""GET /api/audit — recent audit entries + chain verification."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import audit as audit_log  # noqa: E402
from _lib.http import Handler  # noqa: E402
from _lib.store import get_db  # noqa: E402


class handler(Handler):
    def do_GET(self):
        db = get_db()
        q = self.query()
        self.send_json({"entries": audit_log.recent(db, int(q.get("limit", "50") or 50)),
                        "verification": audit_log.verify_chain(db)})
