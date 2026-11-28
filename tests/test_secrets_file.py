"""File-backed secret store used on headless Linux (Docker)."""
import json

from meshlens import secrets


def test_file_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHLENS_HOME", str(tmp_path))
    monkeypatch.setenv("MESHLENS_SECRET_STORE", "file")
    assert secrets.get_secret("asus", "a@b:1") is None
    secrets.set_secret("asus", "a@b:1", "pw")
    assert secrets.has_secret("asus", "a@b:1")
    assert json.loads((tmp_path / "secrets.json").read_text()) == {"asus:a@b:1": "pw"}
    secrets.clear_secret("asus", "a@b:1")
    secrets.clear_secret("asus", "a@b:1")
    assert secrets.get_secret("asus", "a@b:1") is None
