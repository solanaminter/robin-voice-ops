"""Database layer for Robin Voice Ops.

Three backends, chosen at runtime:
1. Postgres via DATABASE_URL (production / Vercel serverless, e.g. Neon).
2. SQLite via ROBIN_DB_PATH (local dev / self-hosted server).
3. In-memory store (demo fallback when neither is configured) — honestly
   labeled DEMO MODE so nobody mistakes it for production persistence.

Schema is shared across backends; DDL branches on backend type.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS callers (
    id TEXT PRIMARY KEY,
    phone TEXT UNIQUE,
    name TEXT,
    first_seen REAL,
    last_seen REAL,
    visit_count INTEGER DEFAULT 0,
    notes TEXT,
    prefs_json TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    caller_id TEXT,
    customer_name TEXT,
    phone TEXT,
    service TEXT,
    slot_start TEXT,
    slot_end TEXT,
    status TEXT DEFAULT 'pending',
    notes TEXT,
    created_at REAL,
    decided_at REAL,
    decided_by TEXT
);
CREATE TABLE IF NOT EXISTS service_requests (
    id TEXT PRIMARY KEY,
    caller_id TEXT,
    category TEXT,
    description TEXT,
    priority TEXT DEFAULT 'normal',
    status TEXT DEFAULT 'open',
    created_at REAL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    customer_name TEXT,
    phone TEXT,
    service TEXT,
    status TEXT,
    scheduled_for TEXT,
    technician TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS kb (
    id TEXT PRIMARY KEY,
    question TEXT,
    answer TEXT,
    keywords TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    kind TEXT,
    ref_id TEXT,
    action TEXT,
    payload_json TEXT,
    status TEXT DEFAULT 'pending',
    created_at REAL,
    decided_at REAL,
    decided_by TEXT,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    actor TEXT,
    action TEXT,
    details_json TEXT,
    prev_hash TEXT,
    hash TEXT
);
CREATE TABLE IF NOT EXISTS call_sessions (
    id TEXT PRIMARY KEY,
    caller_id TEXT,
    started_at REAL,
    ended_at REAL,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS slots (
    id TEXT PRIMARY KEY,
    service TEXT,
    slot_start TEXT,
    slot_end TEXT,
    taken INTEGER DEFAULT 0
);
"""

SCHEMA_PG = SCHEMA_SQLITE.replace(
    "seq INTEGER PRIMARY KEY AUTOINCREMENT,", "seq SERIAL PRIMARY KEY,"
).replace("taken INTEGER DEFAULT 0", "taken BOOLEAN DEFAULT FALSE")


def _now() -> float:
    return time.time()


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class DB:
    """Minimal backend-agnostic wrapper. Not an ORM — just enough."""

    def __init__(self) -> None:
        self.url = os.environ.get("DATABASE_URL", "").strip()
        self.sqlite_path = os.environ.get("ROBIN_DB_PATH", "").strip()
        self.mode = "memory"
        self._lock = threading.Lock()
        self._mem: dict[str, list[dict[str, Any]]] = {}
        if self.url:
            import psycopg  # type: ignore

            self._pg = psycopg
            self.mode = "postgres"
            self._init_pg()
        elif self.sqlite_path:
            self.mode = "sqlite"
            os.makedirs(os.path.dirname(os.path.abspath(self.sqlite_path)), exist_ok=True)
            self._init_sqlite()
        else:
            for t in ("callers appointments service_requests jobs kb approvals audit call_sessions slots".split()):
                self._mem[t] = []
            self._mem_seq = 0
            self._seed_memory()

    # ---- init ----
    def _init_pg(self) -> None:
        conn = self._pg.connect(self.url)
        with conn.cursor() as cur:
            for stmt in SCHEMA_PG.strip().split(";"):
                if stmt.strip():
                    cur.execute(stmt)
        conn.commit()
        conn.close()
        if not self._any("kb"):
            self._seed()

    def _init_sqlite(self) -> None:
        conn = sqlite3.connect(self.sqlite_path)
        conn.executescript(SCHEMA_SQLITE)
        conn.commit()
        cur = conn.execute("SELECT COUNT(*) FROM kb")
        if cur.fetchone()[0] == 0:
            self._seed_sqlite(conn)
        conn.close()

    # ---- generic helpers ----
    def _connect(self):
        if self.mode == "postgres":
            return self._pg.connect(self.url, row_factory=self._pg.rows.dict_row)
        return sqlite3.connect(self.sqlite_path)

    @staticmethod
    def _q(sql: str, mode: str) -> str:
        return sql.replace("?", "%s") if mode == "postgres" else sql

    def _any(self, table: str) -> bool:
        if self.mode == "memory":
            return len(self._mem[table]) > 0
        conn = self._connect()
        try:
            cur = conn.execute(self._q(f"SELECT 1 FROM {table} LIMIT 1", self.mode))
            return cur.fetchone() is not None
        finally:
            conn.close()

    def insert(self, table: str, row: dict[str, Any]) -> None:
        with self._lock:
            if self.mode == "memory":
                if table == "audit":
                    self._mem_seq += 1
                    row = dict(row, seq=self._mem_seq)
                self._mem[table].append(dict(row))
                return
            cols = ", ".join(row.keys())
            ph = ", ".join(["?"] * len(row))
            conn = self._connect()
            try:
                conn.execute(self._q(f"INSERT INTO {table} ({cols}) VALUES ({ph})", self.mode), list(row.values()))
                conn.commit()
            finally:
                conn.close()

    def rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            if self.mode == "memory":
                raise RuntimeError("memory backend does not support raw SQL")
            conn = self._connect()
            try:
                if self.mode == "sqlite":
                    conn.row_factory = sqlite3.Row
                cur = conn.execute(self._q(sql, self.mode), params)
                return [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()

    def one(self, sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
        rs = self.rows(sql, params)
        return rs[0] if rs else None

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            if self.mode == "memory":
                raise RuntimeError("memory backend does not support raw SQL")
            conn = self._connect()
            try:
                cur = conn.execute(self._q(sql, self.mode), params)
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    # ---- memory backend query helpers (demo mode) ----
    def mem_find(self, table: str, **kw: Any) -> list[dict[str, Any]]:
        return [r for r in self._mem[table] if all(r.get(k) == v for k, v in kw.items())]

    def mem_update(self, table: str, where: dict[str, Any], patch: dict[str, Any]) -> int:
        n = 0
        for r in self._mem[table]:
            if all(r.get(k) == v for k, v in where.items()):
                r.update(patch)
                n += 1
        return n

    # ---- seed data: fictional demo business ----
    def _seed_rows(self) -> dict[str, list[dict[str, Any]]]:
        t = _now()
        return {
            "kb": [
                {"id": "kb_hours", "question": "What are your business hours?",
                 "answer": "Robin Home Services is open Monday to Friday, 8am to 6pm, and Saturday 9am to 2pm. We are closed Sundays. Emergency service is available 24/7 for burst pipes and total heating loss.",
                 "keywords": "hours open closed weekend sunday emergency"},
                {"id": "kb_pricing", "question": "How much does a visit cost?",
                 "answer": "Diagnostic visits are $89, waived if you go ahead with the repair. Typical repairs run $150 to $450. We always confirm the price before starting work — no surprises.",
                 "keywords": "price cost charge fee diagnostic quote estimate"},
                {"id": "kb_service_area", "question": "Which areas do you serve?",
                 "answer": "We serve the greater Springfield metro area, roughly a 25-mile radius including Riverside, Oakdale, and Fairview.",
                 "keywords": "area serve location where zip coverage"},
                {"id": "kb_water_heater", "question": "How long does a water heater install take?",
                 "answer": "A standard tank water heater replacement takes about 2 to 3 hours. Tankless installs take most of a day because of venting and gas line work.",
                 "keywords": "water heater install replacement tankless how long"},
                {"id": "kb_warranty", "question": "Do you warranty your work?",
                 "answer": "Yes — 12 months parts and labor on every repair, and 5 years on full system replacements like furnaces and water heaters.",
                 "keywords": "warranty guarantee parts labor"},
            ],
            "jobs": [
                {"id": "job_7f3a21", "customer_name": "Maria Lopez", "phone": "+14155550101",
                 "service": "Water heater replacement", "status": "Technician en route",
                 "scheduled_for": "2026-09-15 afternoon", "technician": "Devon Park",
                 "notes": "50-gal tank, garage install."},
                {"id": "job_9b1c44", "customer_name": "James Chen", "phone": "+14155550102",
                 "service": "AC tune-up", "status": "Completed",
                 "scheduled_for": "2026-09-12 morning", "technician": "Priya Nair", "notes": ""},
                {"id": "job_2d8e90", "customer_name": "Aisha Bello", "phone": "+14155550103",
                 "service": "Circuit breaker panel upgrade", "status": "Scheduled",
                 "scheduled_for": "2026-09-17 morning", "technician": "Tom Okafor", "notes": "200A panel."},
            ],
            "slots": [
                {"id": f"slot_{d}_{w}", "service": svc,
                 "slot_start": f"2026-09-{d} {s}", "slot_end": f"2026-09-{d} {e}", "taken": 0}
                for d, svc, w, s, e in [
                    (16, "Plumbing", "morning", "08:00", "12:00"), (16, "HVAC", "afternoon", "12:00", "16:00"),
                    (17, "Electrical", "morning", "08:00", "12:00"), (17, "Plumbing", "afternoon", "12:00", "16:00"),
                    (18, "HVAC", "morning", "08:00", "12:00"), (18, "General", "afternoon", "12:00", "16:00"),
                ]
            ],
        }

    def _seed(self) -> None:
        for table, rows in self._seed_rows().items():
            for r in rows:
                self.insert(table, r)

    def _seed_sqlite(self, conn) -> None:
        for table, rows in self._seed_rows().items():
            for r in rows:
                cols = ", ".join(r.keys())
                ph = ", ".join(["?"] * len(r))
                conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})", list(r.values()))
        conn.commit()

    def _seed_memory(self) -> None:
        for table, rows in self._seed_rows().items():
            self._mem[table].extend([dict(r) for r in rows])
