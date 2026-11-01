from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS: list[str] = [
    """
CREATE TABLE items (
  id INTEGER PRIMARY KEY, guid TEXT NOT NULL UNIQUE,
  type TEXT NOT NULL CHECK (type IN ('movie','show')),
  title TEXT NOT NULL, raw_title TEXT NOT NULL, year INTEGER,
  link TEXT, thumb TEXT, keywords TEXT NOT NULL DEFAULT '', rating TEXT, pub_date TEXT,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  in_feed INTEGER NOT NULL DEFAULT 1, left_feed_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('review','queued','grabbed','library','ignored')),
  previous_status TEXT, status_changed_at TEXT NOT NULL, status_note TEXT,
  queue_order INTEGER,
  plex_rating_key TEXT, plex_library_key TEXT, plex_library_name TEXT, plex_machine_id TEXT,
  match_method TEXT CHECK (match_method IN ('guid','title')), matched_at TEXT,
  rejected_rating_keys TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE authors (hash TEXT PRIMARY KEY, label TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE item_authors (item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  author_hash TEXT NOT NULL REFERENCES authors(hash), first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  PRIMARY KEY (item_id, author_hash));
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE fetch_log (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
  ok INTEGER NOT NULL, error TEXT, status_code INTEGER, item_count INTEGER NOT NULL DEFAULT 0,
  new_count INTEGER NOT NULL DEFAULT 0, truncated INTEGER NOT NULL DEFAULT 0);
CREATE TABLE scan_log (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
  ok INTEGER NOT NULL, error TEXT, machine_id TEXT, libraries_json TEXT NOT NULL DEFAULT '[]',
  indexed INTEGER NOT NULL DEFAULT 0, matched INTEGER NOT NULL DEFAULT 0, reverted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX items_status ON items(status);
""",
]


def connect(path: Path) -> sqlite3.Connection:
    # isolation_level=None: autocommit; migrate() manages its own transaction.
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= len(MIGRATIONS):
        return current
    conn.execute("BEGIN")
    try:
        for version in range(current, len(MIGRATIONS)):
            for stmt in MIGRATIONS[version].split(";"):
                if stmt.strip():
                    conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {version + 1}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return len(MIGRATIONS)
