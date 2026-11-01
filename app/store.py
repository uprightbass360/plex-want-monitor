from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from app.feed import FeedItem
from app.models import (
    Author, FetchLog, Item, ItemFilter, ScanLog, from_iso, to_iso,
)

_ORDER = {
    "newest": "pub_date DESC, first_seen DESC",
    "oldest": "pub_date ASC, first_seen ASC",
    "title": "title COLLATE NOCASE",
    "queue": "queue_order ASC NULLS LAST, first_seen ASC",
}


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _tx(self):
        return _Tx(self.conn)

    # items
    def upsert_feed_items(self, items: Sequence[FeedItem], now: datetime) -> tuple[list[int], list[int]]:
        ts = to_iso(now)
        new: list[int] = []
        seen: list[int] = []
        with self._tx():
            for it in items:
                row = self.conn.execute("SELECT id FROM items WHERE guid=?", (it.guid,)).fetchone()
                if row is None:
                    cur = self.conn.execute(
                        "INSERT INTO items (guid, type, title, raw_title, year, link, thumb, keywords,"
                        " rating, pub_date, first_seen, last_seen, in_feed, status, status_changed_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,'review',?)",
                        (it.guid, it.type, it.title, it.raw_title, it.year, it.link, it.thumb,
                         ",".join(it.keywords), it.rating, to_iso(it.pub_date), ts, ts, ts),
                    )
                    item_id = cur.lastrowid
                    new.append(item_id)
                else:
                    item_id = row["id"]
                    self.conn.execute(
                        "UPDATE items SET type=?, title=?, raw_title=?, year=?, link=?, thumb=?,"
                        " keywords=?, rating=?, pub_date=?, last_seen=?, in_feed=1, left_feed_at=NULL"
                        " WHERE id=?",
                        (it.type, it.title, it.raw_title, it.year, it.link, it.thumb,
                         ",".join(it.keywords), it.rating, to_iso(it.pub_date), ts, item_id),
                    )
                    seen.append(item_id)
                if it.author_hash:
                    self.conn.execute(
                        "INSERT INTO authors (hash, first_seen, last_seen) VALUES (?,?,?)"
                        " ON CONFLICT(hash) DO UPDATE SET last_seen=excluded.last_seen",
                        (it.author_hash, ts, ts),
                    )
                    self.conn.execute(
                        "INSERT INTO item_authors (item_id, author_hash, first_seen, last_seen)"
                        " VALUES (?,?,?,?) ON CONFLICT(item_id, author_hash)"
                        " DO UPDATE SET last_seen=excluded.last_seen",
                        (item_id, it.author_hash, ts, ts),
                    )
        return new, seen

    def mark_left_feed(self, present_guids: set[str], now: datetime) -> int:
        ts = to_iso(now)
        with self._tx():
            rows = self.conn.execute("SELECT id, guid FROM items WHERE in_feed=1").fetchall()
            gone = [r["id"] for r in rows if r["guid"] not in present_guids]
            for item_id in gone:
                self.conn.execute(
                    "UPDATE items SET in_feed=0, left_feed_at=? WHERE id=?", (ts, item_id)
                )
        return len(gone)

    def get_item(self, item_id: int) -> Item | None:
        r = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return Item.from_row(r) if r else None

    def get_item_by_guid(self, guid: str) -> Item | None:
        r = self.conn.execute("SELECT * FROM items WHERE guid=?", (guid,)).fetchone()
        return Item.from_row(r) if r else None

    def list_items(self, f: ItemFilter) -> list[Item]:
        where: list[str] = []
        params: list = []
        if f.statuses is not None:
            where.append(f"status IN ({','.join('?' * len(f.statuses))})")
            params.extend(f.statuses)
        if f.type:
            where.append("type=?")
            params.append(f.type)
        if f.q:
            where.append("title LIKE ? ESCAPE '\\'")
            esc = f.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{esc}%")
        if f.author:
            where.append("id IN (SELECT item_id FROM item_authors WHERE author_hash=?)")
            params.append(f.author)
        if f.in_feed is not None:
            where.append("in_feed=?")
            params.append(int(f.in_feed))
        order = _ORDER.get(f.sort)
        if order is None:
            raise ValueError(f"unknown sort {f.sort!r}")
        sql = "SELECT * FROM items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order}, id"
        return [Item.from_row(r) for r in self.conn.execute(sql, params)]

    def counts_by_status(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM items GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    # authors
    def authors_for(self, item_ids: Sequence[int]) -> dict[int, list[Author]]:
        out: dict[int, list[Author]] = {i: [] for i in item_ids}
        if not item_ids:
            return out
        rows = self.conn.execute(
            f"SELECT ia.item_id, a.hash, a.label, a.last_seen,"
            f" (SELECT COUNT(*) FROM item_authors x WHERE x.author_hash=a.hash) AS n"
            f" FROM item_authors ia JOIN authors a ON a.hash=ia.author_hash"
            f" WHERE ia.item_id IN ({','.join('?' * len(item_ids))}) ORDER BY a.hash",
            list(item_ids),
        )
        for r in rows:
            out[r["item_id"]].append(Author(r["hash"], r["label"], r["n"], from_iso(r["last_seen"])))
        return out

    def list_authors(self) -> list[Author]:
        rows = self.conn.execute(
            "SELECT a.hash, a.label, a.last_seen, COUNT(ia.item_id) AS n FROM authors a"
            " LEFT JOIN item_authors ia ON ia.author_hash=a.hash GROUP BY a.hash ORDER BY a.hash"
        )
        return [Author(r["hash"], r["label"], r["n"], from_iso(r["last_seen"])) for r in rows]

    def set_author_label(self, hash: str, label: str | None) -> None:
        self.conn.execute("UPDATE authors SET label=? WHERE hash=?", (label or None, hash))

    # status
    def _next_queue_order(self) -> int:
        return self.conn.execute("SELECT COALESCE(MAX(queue_order), 0) + 1 FROM items").fetchone()[0]

    def set_status(self, item_id: int, status: str, now: datetime, *, note: str | None = None) -> Item:
        with self._tx():
            item = self.get_item(item_id)
            if item is None:
                raise KeyError(item_id)
            if item.status == status:
                return item
            order = item.queue_order
            if status == "queued" and order is None:
                order = self._next_queue_order()
            self.conn.execute(
                "UPDATE items SET status=?, previous_status=?, status_changed_at=?, status_note=?,"
                " queue_order=? WHERE id=?",
                (status, item.status, to_iso(now), note, order, item_id),
            )
        return self.get_item(item_id)

    def skip(self, item_id: int, now: datetime) -> Item:
        with self._tx():
            self.conn.execute(
                "UPDATE items SET queue_order=? WHERE id=?", (self._next_queue_order(), item_id)
            )
        return self.get_item(item_id)

    # plex match
    def set_match(self, item_id: int, rating_key: str, library_key: str, library_name: str,
                  machine_id: str, method: str, now: datetime) -> None:
        self.conn.execute(
            "UPDATE items SET plex_rating_key=?, plex_library_key=?, plex_library_name=?,"
            " plex_machine_id=?, match_method=?, matched_at=? WHERE id=?",
            (rating_key, library_key, library_name, machine_id, method, to_iso(now), item_id),
        )

    def clear_match(self, item_id: int) -> None:
        self.conn.execute(
            "UPDATE items SET plex_rating_key=NULL, plex_library_key=NULL, plex_library_name=NULL,"
            " plex_machine_id=NULL, match_method=NULL, matched_at=NULL WHERE id=?",
            (item_id,),
        )

    def reject_rating_key(self, item_id: int, rating_key: str) -> None:
        with self._tx():
            item = self.get_item(item_id)
            if item is None:
                raise KeyError(item_id)
            if rating_key in item.rejected_rating_keys:
                return
            keys = [*item.rejected_rating_keys, rating_key]
            self.conn.execute(
                "UPDATE items SET rejected_rating_keys=? WHERE id=?", (json.dumps(keys), item_id)
            )

    # settings
    def get_setting(self, key: str, default=None):
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_setting(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # logs
    def log_fetch(self, log: FetchLog) -> int:
        cur = self.conn.execute(
            "INSERT INTO fetch_log (started_at, finished_at, ok, error, status_code, item_count,"
            " new_count, truncated) VALUES (?,?,?,?,?,?,?,?)",
            (to_iso(log.started_at), to_iso(log.finished_at), int(log.ok), log.error,
             log.status_code, log.item_count, log.new_count, int(log.truncated)),
        )
        return cur.lastrowid

    def last_fetch(self) -> FetchLog | None:
        r = self.conn.execute("SELECT * FROM fetch_log ORDER BY id DESC LIMIT 1").fetchone()
        if r is None:
            return None
        return FetchLog(
            id=r["id"], started_at=from_iso(r["started_at"]), finished_at=from_iso(r["finished_at"]),
            ok=bool(r["ok"]), error=r["error"], status_code=r["status_code"],
            item_count=r["item_count"], new_count=r["new_count"], truncated=bool(r["truncated"]),
        )

    def log_scan(self, log: ScanLog) -> int:
        cur = self.conn.execute(
            "INSERT INTO scan_log (started_at, finished_at, ok, error, machine_id, libraries_json,"
            " indexed, matched, reverted) VALUES (?,?,?,?,?,?,?,?,?)",
            (to_iso(log.started_at), to_iso(log.finished_at), int(log.ok), log.error,
             log.machine_id, json.dumps(log.libraries), log.indexed, log.matched, log.reverted),
        )
        return cur.lastrowid

    def last_scan(self) -> ScanLog | None:
        r = self.conn.execute("SELECT * FROM scan_log ORDER BY id DESC LIMIT 1").fetchone()
        if r is None:
            return None
        return ScanLog(
            id=r["id"], started_at=from_iso(r["started_at"]), finished_at=from_iso(r["finished_at"]),
            ok=bool(r["ok"]), error=r["error"], machine_id=r["machine_id"],
            libraries=json.loads(r["libraries_json"]), indexed=r["indexed"],
            matched=r["matched"], reverted=r["reverted"],
        )


class _Tx:
    """Transaction that nests: only the outermost block issues BEGIN/COMMIT."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.outer = False

    def __enter__(self):
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN")
            self.outer = True

    def __exit__(self, exc_type, exc, tb):
        if self.outer:
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
