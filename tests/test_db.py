import sqlite3

import pytest

from app.db import MIGRATIONS, connect, migrate


def test_migrate_creates_tables_and_is_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        _check_migrate(conn)
    finally:
        conn.close()


def _check_migrate(conn):
    v1 = migrate(conn)
    v2 = migrate(conn)
    assert v1 == v2 == len(MIGRATIONS)
    names = {r["name"] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"items", "item_authors", "authors", "settings", "fetch_log", "scan_log"} <= names


def test_items_status_check(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("insert into items(guid,type,title,raw_title,status,first_seen,last_seen,status_changed_at) values ('x','movie','t','t','bogus','a','a','a')")


def test_migration_4_from_v3_database(tmp_path):
    conn = connect(tmp_path / "v3.db")
    try:
        conn.execute("BEGIN")
        for version in range(3):
            for stmt in MIGRATIONS[version].split(";"):
                if stmt.strip():
                    conn.execute(stmt)
        conn.execute("PRAGMA user_version = 3")
        conn.execute("COMMIT")
        conn.execute("insert into watchlist_guids(rating_key,guid,fetched_at) values ('r','g','t')")
        assert migrate(conn) == len(MIGRATIONS) >= 4
        row = conn.execute("select guid, watchlisted_at from watchlist_guids").fetchone()
        assert (row[0], row[1]) == ("g", None)
    finally:
        conn.close()


def test_migration_3_from_v2_database(tmp_path):
    conn = connect(tmp_path / "v2.db")
    try:
        conn.execute("BEGIN")
        for version in range(2):
            for stmt in MIGRATIONS[version].split(";"):
                if stmt.strip():
                    conn.execute(stmt)
        conn.execute("PRAGMA user_version = 2")
        conn.execute("COMMIT")
        conn.execute("insert into items(guid,type,title,raw_title,status,first_seen,last_seen,status_changed_at) values ('x','movie','t','t','review','a','a','a')")
        conn.execute("insert into fetch_log(started_at,finished_at,ok) values ('a','a',1)")
        assert migrate(conn) == len(MIGRATIONS) >= 3
        assert conn.execute("select source from items").fetchone()[0] == "feed"
        assert conn.execute("select source from fetch_log").fetchone()[0] == "feed"
        conn.execute("insert into watchlist_guids(rating_key,guid,fetched_at) values ('r','g','t')")
    finally:
        conn.close()
