import sqlite3

import pytest

from app.db import MIGRATIONS, connect, migrate


def test_migrate_creates_tables_and_is_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    v1 = migrate(conn)
    v2 = migrate(conn)
    assert v1 == v2 == len(MIGRATIONS)
    names = {r["name"] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"items", "item_authors", "authors", "settings", "fetch_log", "scan_log"} <= names


def test_items_status_check(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("insert into items(guid,type,title,raw_title,status,first_seen,last_seen,status_changed_at) values ('x','movie','t','t','bogus','a','a','a')")
