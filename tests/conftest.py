import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text():
    def _read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")
    return _read


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Isolated data directory and database for each test."""
    monkeypatch.setenv("MESHLENS_HOME", str(tmp_path))
    from meshlens.store import db
    db.init(tmp_path / "test.db")
    yield tmp_path
    db.close()
