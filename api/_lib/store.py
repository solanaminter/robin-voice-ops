"""Shared DB singleton for serverless functions."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib.db import DB  # noqa: E402

_db = None


def get_db() -> DB:
    global _db
    if _db is None:
        _db = DB()
    return _db


def demo_mode(db: DB) -> bool:
    return db.mode == "memory"
