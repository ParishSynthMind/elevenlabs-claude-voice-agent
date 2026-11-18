from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CallLog:
    """Every call, tool call, lead and webhook delivery in SQLite.

    One row per conversation, one per tool event, one per captured lead, and one per webhook
    delivery so ElevenLabs' retries (up to 5, same payload) are handled once."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(
            """CREATE TABLE IF NOT EXISTS calls (
                call_id TEXT PRIMARY KEY, agent_id TEXT, caller TEXT, channel TEXT, started_at TEXT,
                duration_seconds REAL, credits INTEGER, status TEXT, termination_reason TEXT,
                call_successful TEXT, analysis_summary TEXT, transcript TEXT, outcome TEXT,
                handover INTEGER DEFAULT 0, summary_emailed INTEGER DEFAULT 0, day TEXT, updated_utc TEXT);
               CREATE TABLE IF NOT EXISTS webhook_events (
                call_id TEXT, event TEXT, at_utc TEXT, PRIMARY KEY (call_id, event));
               CREATE TABLE IF NOT EXISTS tool_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT, tool TEXT, arguments TEXT,
                result TEXT, at_utc TEXT);
               CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT, name TEXT, phone TEXT, email TEXT,
                interest TEXT, consent_to_contact INTEGER, at_utc TEXT,
                UNIQUE (call_id, phone, email));"""
        )
        self._db.commit()

    def _upsert(self, call_id: str, **fields: Any) -> None:
        fields["updated_utc"] = _now()
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{k}=COALESCE(excluded.{k}, calls.{k})" for k in fields)
        with self._lock:
            self._db.execute(
                f"INSERT INTO calls (call_id, {cols}) VALUES (?, {marks}) ON CONFLICT(call_id) DO UPDATE SET {updates}",
                [call_id, *fields.values()],
            )
            self._db.commit()

    def tool_event(self, call_id: Optional[str], tool: str, arguments: Dict[str, Any], result: str) -> None:
        with self._lock:
            if call_id:
                self._db.execute("INSERT OR IGNORE INTO calls (call_id, updated_utc) VALUES (?, ?)", (call_id, _now()))
            self._db.execute(
                "INSERT INTO tool_events (call_id, tool, arguments, result, at_utc) VALUES (?,?,?,?,?)",
                (call_id, tool, json.dumps(arguments), result, _now()),
            )
            self._db.commit()

    def add_lead(self, call_id: Optional[str], name: str, phone: str, email: str, interest: str,
                 consent: bool) -> bool:
        """True if stored, False if the same contact was already captured on this call."""
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO leads (call_id, name, phone, email, interest, consent_to_contact, at_utc) "
                "VALUES (?,?,?,?,?,?,?)", (call_id or "", name, phone, email, interest, int(consent), _now()))
            self._db.commit()
            return cur.rowcount == 1

    def leads(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._db.execute("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def first_delivery(self, call_id: str, event: str) -> bool:
        """True the first time (call_id, event) is seen, False for every retry of it."""
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO webhook_events VALUES (?,?,?)", (call_id, event, _now()))
            self._db.commit()
            return cur.rowcount == 1

    def forget_delivery(self, call_id: str, event: str) -> None:
        """Processing failed after we claimed the delivery: let ElevenLabs' retry try again."""
        with self._lock:
            self._db.execute("DELETE FROM webhook_events WHERE call_id=? AND event=?", (call_id, event))
            self._db.commit()

    def get(self, call_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._db.execute("SELECT * FROM calls WHERE call_id=?", (call_id,))
            row = cur.fetchone()
            return dict(zip([c[0] for c in cur.description], row, strict=True)) if row else None

    def mark(self, call_id: str, **fields: Any) -> None:
        self._upsert(call_id, **fields)

    def credits_on(self, day: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(SUM(credits), 0) FROM calls WHERE day=?", (day,)).fetchone()
            return int(row[0] or 0)

    def calls_on(self, day: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM calls WHERE day=?", (day,)).fetchone()
            return int(row[0] or 0)

    def calls(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._db.execute("SELECT * FROM calls ORDER BY COALESCE(started_at, updated_utc) DESC LIMIT ?",
                                   (limit,))
            cols = [c[0] for c in cur.description]
            calls = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
            for c in calls:
                ev = self._db.execute(
                    "SELECT tool, arguments, result, at_utc FROM tool_events WHERE call_id=? ORDER BY id",
                    (c["call_id"],))
                c["tool_events"] = [dict(zip(["tool", "arguments", "result", "at_utc"], r, strict=True)) for r in ev.fetchall()]
        return calls
