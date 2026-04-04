"""SQLite event store for companion-capture."""

import sqlite3
import sys
from pathlib import Path
from typing import Optional


class CaptureStore:
    """Append-only SQLite store for companion captures."""

    def __init__(self, db_path: Path, debug: bool = False):
        self._db_path = db_path
        self._debug = debug
        self._conn: Optional[sqlite3.Connection] = None
        self._has_fts5: bool = False

    def open(self) -> "CaptureStore":
        """Open DB, create schema, enable WAL, detect FTS5."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS captures (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                project TEXT,
                session_id TEXT,
                raw_text TEXT NOT NULL,
                classification TEXT,
                files_in_context TEXT,
                schema_version INTEGER NOT NULL
            )
        """)

        self._has_fts5 = self._detect_fts5()

        if self._has_fts5:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS captures_fts
                USING fts5(raw_text, project,
                           content=captures, content_rowid=rowid)
            """)

        self._conn.commit()
        return self

    def _detect_fts5(self) -> bool:
        """Check FTS5 availability via test table creation."""
        try:
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_test USING fts5(x)"
            )
            self._conn.execute("DROP TABLE IF EXISTS _fts5_test")
            return True
        except sqlite3.OperationalError:
            return False

    def insert(
        self,
        *,
        id: str,
        timestamp: str,
        project: str,
        session_id: str,
        raw_text: str,
        classification: str,
        schema_version: int,
    ) -> None:
        """Insert a capture. Silent on failure (stderr if debug)."""
        if self._conn is None:
            return
        try:
            cursor = self._conn.execute(
                """INSERT OR IGNORE INTO captures
                   (id, timestamp, project, session_id, raw_text,
                    classification, files_in_context, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                (
                    id,
                    timestamp,
                    project,
                    session_id,
                    raw_text,
                    classification,
                    schema_version,
                ),
            )
            if self._has_fts5 and cursor.lastrowid:
                self._conn.execute(
                    """INSERT INTO captures_fts(rowid, raw_text, project)
                       VALUES (?, ?, ?)""",
                    (cursor.lastrowid, raw_text, project),
                )
            self._conn.commit()
        except (sqlite3.Error, OSError) as exc:
            if self._debug:
                print(
                    f"[companion-capture] SQLite write failed: {exc}",
                    file=sys.stderr,
                )
            try:
                self._conn.rollback()
            except sqlite3.Error:
                pass

    def search(
        self,
        query: str,
        *,
        project: str = None,
        classification: str = None,
        since: str = None,
        limit: int = 20,
    ) -> list:
        """Search captures. Uses FTS5 if available, LIKE fallback."""
        if self._conn is None:
            return []
        try:
            if self._has_fts5:
                sql = """
                    SELECT c.id, c.timestamp, c.project, c.session_id,
                           c.raw_text, c.classification, c.schema_version
                    FROM captures c
                    WHERE c.rowid IN (
                        SELECT rowid FROM captures_fts
                        WHERE captures_fts MATCH ?
                    )"""
                params = [query]
            else:
                sql = """
                    SELECT id, timestamp, project, session_id,
                           raw_text, classification, schema_version
                    FROM captures
                    WHERE raw_text LIKE ?"""
                params = [f"%{query}%"]

            if project:
                sql += " AND project = ?"
                params.append(project)
            if classification:
                sql += " AND classification = ?"
                params.append(classification)
            if since:
                sql += " AND timestamp >= ?"
                params.append(since)

            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            rows = self._conn.execute(sql, params).fetchall()
            return [
                {
                    "id": r[0],
                    "timestamp": r[1],
                    "project": r[2],
                    "session_id": r[3],
                    "raw_text": r[4],
                    "classification": r[5],
                    "schema_version": r[6],
                }
                for r in rows
            ]
        except (sqlite3.Error, OSError) as exc:
            if self._debug:
                print(
                    f"[companion-capture] SQLite search failed: {exc}",
                    file=sys.stderr,
                )
            return []

    def recent(
        self,
        *,
        project: str = None,
        limit: int = 20,
    ) -> list:
        """Return latest captures ordered by timestamp descending."""
        if self._conn is None:
            return []
        try:
            sql = """
                SELECT id, timestamp, project, session_id,
                       raw_text, classification, schema_version
                FROM captures
                WHERE 1=1"""
            params: list = []

            if project:
                sql += " AND project = ?"
                params.append(project)

            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            rows = self._conn.execute(sql, params).fetchall()
            return [
                {
                    "id": r[0],
                    "timestamp": r[1],
                    "project": r[2],
                    "session_id": r[3],
                    "raw_text": r[4],
                    "classification": r[5],
                    "schema_version": r[6],
                }
                for r in rows
            ]
        except (sqlite3.Error, OSError) as exc:
            if self._debug:
                print(
                    f"[companion-capture] SQLite recent failed: {exc}",
                    file=sys.stderr,
                )
            return []

    def stats(self) -> dict:
        """Return aggregate statistics about stored captures."""
        empty = {
            "total": 0,
            "by_project": {},
            "by_classification": {},
            "earliest": None,
            "latest": None,
        }
        if self._conn is None:
            return empty
        try:
            row = self._conn.execute(
                "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM captures"
            ).fetchone()
            total = row[0]
            if total == 0:
                return empty

            by_project = {}
            for r in self._conn.execute(
                "SELECT project, COUNT(*) FROM captures GROUP BY project"
            ).fetchall():
                by_project[r[0]] = r[1]

            by_classification = {}
            for r in self._conn.execute(
                "SELECT classification, COUNT(*) FROM captures GROUP BY classification"
            ).fetchall():
                by_classification[r[0]] = r[1]

            return {
                "total": total,
                "by_project": by_project,
                "by_classification": by_classification,
                "earliest": row[1],
                "latest": row[2],
            }
        except (sqlite3.Error, OSError) as exc:
            if self._debug:
                print(
                    f"[companion-capture] SQLite stats failed: {exc}",
                    file=sys.stderr,
                )
            return empty

    def delete_matching(self, pattern: str) -> int:
        """Delete captures whose raw_text matches a Python regex. Returns count."""
        import re as _re  # noqa

        if self._conn is None:
            return 0
        try:
            rows = self._conn.execute(
                "SELECT rowid, id, raw_text, project FROM captures"
            ).fetchall()
            to_delete = [
                (r[0], r[1], r[2], r[3]) for r in rows if _re.search(pattern, r[2])
            ]
            if not to_delete:
                return 0

            rowids = [r[0] for r in to_delete]
            placeholders = ",".join("?" * len(rowids))
            self._conn.execute(
                f"DELETE FROM captures WHERE rowid IN ({placeholders})",
                rowids,
            )

            if self._has_fts5:
                for rowid, _id, raw_text, project in to_delete:
                    self._conn.execute(
                        "INSERT INTO captures_fts(captures_fts, rowid, "
                        "raw_text, project) VALUES('delete', ?, ?, ?)",
                        (rowid, raw_text, project),
                    )

            self._conn.commit()
            return len(to_delete)
        except (sqlite3.Error, OSError) as exc:
            if self._debug:
                print(
                    f"[companion-capture] SQLite delete failed: {exc}",
                    file=sys.stderr,
                )
            try:
                self._conn.rollback()
            except sqlite3.Error:
                pass
            return 0

    @property
    def has_fts5(self) -> bool:
        return self._has_fts5

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def __enter__(self) -> "CaptureStore":
        return self.open()

    def __exit__(self, *args) -> None:
        self.close()
