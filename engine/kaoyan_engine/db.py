"""SQLite 存储。

两类表：
- 可同步集合（COLLECTIONS）：每条记录都有 id / updated_at / deleted，桌面端、手机端、同步服务器之间按"最后修改为准"合并。
- 本地表：课件、检索片段、对话、工具日志，只存在电脑上。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

COLLECTIONS: dict[str, list[str]] = {
    "subjects": ["name", "kind", "skill_prefix", "color", "sort"],
    "courses": ["subject_id", "name", "teacher", "term", "description", "sort"],
    "lessons": ["course_id", "title", "sort", "status"],
    "lesson_summaries": ["lesson_id", "course_id", "session_id", "content", "key_points"],
    "cards": ["course_id", "lesson_id", "front", "back", "source", "fsrs", "due"],
    "tasks": ["date", "title", "kind", "done", "course_id", "minutes", "source", "sort", "note"],
    "timetable": [
        "course_name",
        "course_id",
        "weekday",
        "start_section",
        "end_section",
        "weeks",
        "location",
        "teacher",
        "start_time",
        "end_time",
    ],
    "checkins": ["date", "minutes", "note", "mood"],
    "focus_sessions": ["start", "minutes", "task_id", "course_id"],
    "mentor_notes": ["date", "content", "tone"],
    "profile": ["exam_date", "target", "tone", "term_start", "section_times", "nickname"],
}

JSON_FIELDS = {"key_points", "fsrs", "weeks", "section_times"}

LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id TEXT PRIMARY KEY, course_id TEXT, lesson_id TEXT, filename TEXT, path TEXT, kind TEXT,
    pages INTEGER DEFAULT 0, status TEXT, error TEXT, preview_pdf TEXT, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS pages (
    material_id TEXT, page INTEGER, text TEXT, PRIMARY KEY (material_id, page)
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, material_id TEXT, course_id TEXT, lesson_id TEXT, page INTEGER, text TEXT,
    hash_vec BLOB, emb BLOB, emb_model TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_course ON chunks(course_id);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, lesson_id TEXT, course_id TEXT, title TEXT, status TEXT, origin TEXT, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, tool_call_id TEXT,
    name TEXT, archived INTEGER DEFAULT 0, created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE TABLE IF NOT EXISTS tool_logs (
    id TEXT PRIMARY KEY, session_id TEXT, tool TEXT, args TEXT, level TEXT, status TEXT, result TEXT,
    snapshot_id TEXT, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return str(uuid.uuid4())


class UnknownCollection(KeyError):
    pass


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            for name, fields in COLLECTIONS.items():
                cols = ", ".join(f"{f}" for f in fields)
                self.conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, updated_at INTEGER NOT NULL, "
                    f"deleted INTEGER NOT NULL DEFAULT 0, seq INTEGER NOT NULL DEFAULT 0, {cols})"
                )
                existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({name})")}
                if "seq" not in existing:
                    self.conn.execute(f"ALTER TABLE {name} ADD COLUMN seq INTEGER NOT NULL DEFAULT 0")
                self.conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{name}_seq ON {name}(seq)")
                for f in fields:
                    if f not in existing:
                        self.conn.execute(f"ALTER TABLE {name} ADD COLUMN {f}")
            self.conn.executescript(LOCAL_SCHEMA)
            self.conn.commit()

    # ---------- 通用 SQL ----------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---------- 可同步集合 ----------
    @staticmethod
    def _fields(collection: str) -> list[str]:
        if collection not in COLLECTIONS:
            raise UnknownCollection(collection)
        return COLLECTIONS[collection]

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        for f in JSON_FIELDS & row.keys():
            if isinstance(row[f], str) and row[f]:
                try:
                    row[f] = json.loads(row[f])
                except json.JSONDecodeError:
                    pass
        row["deleted"] = bool(row.get("deleted"))
        return row

    @staticmethod
    def _encode(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return int(value)
        return value

    def _next_seq(self) -> int:
        row = self.conn.execute("SELECT value FROM sync_state WHERE key = 'seq'").fetchone()
        seq = int(row["value"]) + 1 if row else 1
        self.conn.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES ('seq', ?)", (str(seq),))
        return seq

    def current_seq(self) -> int:
        return int(self.get_state("seq", "0"))

    def upsert(self, collection: str, record: dict[str, Any], *, keep_timestamp: bool = False) -> dict[str, Any]:
        """写入一条记录。每次写入都分配一个本地递增的 seq，作为其它设备拉取变更的游标。"""
        fields = self._fields(collection)
        rec = {k: v for k, v in record.items() if k in fields or k in ("id", "updated_at", "deleted")}
        rec.setdefault("id", new_id())
        if not keep_timestamp or "updated_at" not in rec:
            rec["updated_at"] = now_ms()
        rec["deleted"] = int(bool(rec.get("deleted", False)))
        with self._lock:
            existing = self.get(collection, rec["id"], include_deleted=True)
            if existing:
                merged = {**existing, **rec}
            else:
                merged = {f: None for f in fields} | rec
            merged["seq"] = self._next_seq()
            cols = ["id", "updated_at", "deleted", "seq", *fields]
            values = [self._encode(merged.get(c)) for c in cols]
            placeholders = ", ".join("?" for _ in cols)
            self.execute(f"INSERT OR REPLACE INTO {collection} ({', '.join(cols)}) VALUES ({placeholders})", values)
        return self.get(collection, rec["id"], include_deleted=True) or {}

    def get(self, collection: str, record_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        self._fields(collection)
        row = self.one(f"SELECT * FROM {collection} WHERE id = ?", (record_id,))
        if not row or (row["deleted"] and not include_deleted):
            return None
        return self._decode(row)

    def list(
        self,
        collection: str,
        *,
        where: dict[str, Any] | None = None,
        since: int | None = None,
        include_deleted: bool = False,
        order: str = "updated_at",
    ) -> list[dict[str, Any]]:
        fields = self._fields(collection)
        clauses, params = [], []
        if not include_deleted:
            clauses.append("deleted = 0")
        if since is not None:
            clauses.append("updated_at > ?")
            params.append(since)
        for k, v in (where or {}).items():
            if k not in fields and k != "id":
                continue
            clauses.append(f"{k} = ?")
            params.append(self._encode(v))
        order_col = order if order in (*fields, "updated_at", "id") else "updated_at"
        sql = f"SELECT * FROM {collection}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {order_col}"
        return [self._decode(r) for r in self.query(sql, params)]

    def soft_delete(self, collection: str, record_id: str) -> None:
        if self.get(collection, record_id, include_deleted=True):
            self.upsert(collection, {"id": record_id, "deleted": True})

    def merge_remote(self, collection: str, records: list[dict[str, Any]]) -> int:
        """按 updated_at 合并远端记录，返回实际写入的条数（最后修改为准）。"""
        applied = 0
        for rec in records:
            if "id" not in rec or "updated_at" not in rec:
                continue
            local = self.get(collection, rec["id"], include_deleted=True)
            if local and int(local["updated_at"]) >= int(rec["updated_at"]):
                continue
            self.upsert(collection, rec, keep_timestamp=True)
            applied += 1
        return applied

    def changes_since(self, seq: int) -> dict[str, list[dict[str, Any]]]:
        """返回本地 seq 大于游标的所有记录（含已删除），用于同步。"""
        out: dict[str, list[dict[str, Any]]] = {}
        for c in COLLECTIONS:
            rows = [self._decode(r) for r in self.query(f"SELECT * FROM {c} WHERE seq > ? ORDER BY seq", (seq,))]
            if rows:
                out[c] = rows
        return out

    # ---------- 同步状态 ----------
    def get_state(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM sync_state WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)", (key, value))
