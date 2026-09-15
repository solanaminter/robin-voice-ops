"""GET /api/dashboard — appointments, service requests, jobs for the ops dashboard."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib.http import Handler  # noqa: E402
from _lib.store import get_db  # noqa: E402


def _all(db, table):
    if db.mode == "memory":
        return sorted(db._mem[table], key=lambda r: r.get("created_at", 0), reverse=True)[:50]
    return db.rows(f"SELECT * FROM {table} ORDER BY created_at DESC LIMIT 50")


class handler(Handler):
    def do_GET(self):
        db = get_db()
        self.send_json({
            "appointments": _all(db, "appointments"),
            "service_requests": _all(db, "service_requests"),
            "jobs": _all(db, "jobs") if db.mode == "memory" else db.rows("SELECT * FROM jobs"),
        })
