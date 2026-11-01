from pathlib import Path
from datetime import UTC, datetime

import pytest

from app.db import connect, migrate


@pytest.fixture
def now():
    return datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def fixture_bytes():
    return (Path(__file__).parent / "fixtures" / "friends-feed.xml").read_bytes()
