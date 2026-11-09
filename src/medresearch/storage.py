from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .config import ResearchError


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Store:
    """Short-lived connections; writes serialize through SQLite transactions."""

    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "workspace.sqlite3"
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                name TEXT NOT NULL, pages TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                state TEXT NOT NULL, phase TEXT NOT NULL, revision INTEGER NOT NULL,
                preview TEXT NOT NULL, digest TEXT NOT NULL, expires REAL NOT NULL,
                provider TEXT NOT NULL, articles TEXT NOT NULL DEFAULT '[]',
                report TEXT, error TEXT, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS cache (
                case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                key TEXT NOT NULL, value TEXT NOT NULL, expires REAL NOT NULL,
                PRIMARY KEY(case_id, key)
            );
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                phase TEXT NOT NULL, digest TEXT NOT NULL, preview TEXT NOT NULL,
                approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def case(self, case_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if row is None:
            raise ResearchError("Case not found.")
        return dict(row)

    def create(self, title: str):
        case_id = uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO cases(id,title) VALUES (?,?)", (case_id, title))
        return self.case(case_id)

    def documents(self, case_id):
        self.case(case_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM documents WHERE case_id=? ORDER BY rowid", (case_id,)).fetchall()
        return [{**dict(r), "pages": json.loads(r["pages"]), "reviewed": bool(r["reviewed"])} for r in rows]

    @staticmethod
    def invalidate(db, case_id):
        if db.execute("SELECT 1 FROM jobs WHERE case_id=? AND state IN ('queued','running')", (case_id,)).fetchone():
            raise ResearchError("Wait for the active job before editing or deleting this case.")
        db.execute("UPDATE cases SET revision=revision+1 WHERE id=?", (case_id,))
        db.execute("UPDATE jobs SET state='failed',error='Case changed; prepare a new review.',report=NULL "
                   "WHERE case_id=? AND state IN ('awaiting_review','completed')", (case_id,))

    def add_document(self, case_id, name, pages):
        doc_id = uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM cases WHERE id=?", (case_id,)).fetchone() is None:
                raise ResearchError("Case not found.")
            self.invalidate(db, case_id)
            db.execute("INSERT INTO documents(id,case_id,name,pages) VALUES (?,?,?,?)",
                       (doc_id, case_id, name, encode(pages)))
        return doc_id

    def edit_document(self, case_id, doc_id, pages):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM documents WHERE case_id=? AND id=?", (case_id, doc_id)).fetchone()
            if row is None:
                raise ResearchError("Document not found.")
            existing = json.loads(row["pages"])
            if [p["number"] for p in pages] != [p["number"] for p in existing]:
                raise ResearchError("Preserve the existing page numbers and order.")
            self.invalidate(db, case_id)
            db.execute("UPDATE documents SET pages=?, reviewed=1 WHERE id=?", (encode(pages), doc_id))

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ResearchError("Job not found.")
        result = dict(row)
        for key in ("preview", "articles", "report"):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        return result

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='failed',error='Interrupted by service restart; prepare a new review.' "
                       "WHERE state IN ('queued','running')")

    def approvals(self, job_id):
        self.job(job_id)
        with self.connect() as db:
            rows = db.execute("SELECT phase,digest,preview,approved_at FROM approvals WHERE job_id=? ORDER BY id",
                              (job_id,)).fetchall()
        return [{**dict(r), "preview": json.loads(r["preview"])} for r in rows]

    def delete(self, case_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM cases WHERE id=?", (case_id,)).fetchone() is None:
                raise ResearchError("Case not found.")
            self.invalidate(db, case_id)
            db.execute("DELETE FROM cases WHERE id=?", (case_id,))
