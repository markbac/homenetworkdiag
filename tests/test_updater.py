import hashlib
import json

import pytest

from meshlens import updater
from meshlens.web.auth import Auth

SETUP = b"MZ fake installer"
GOOD_SUMS = f"{hashlib.sha256(SETUP).hexdigest()}  MeshLens-0.3.0-setup.exe\n{'0' * 64}  MeshLens-0.3.0-portable.zip\n"
BASE = "https://github.com/markbac/homenetworkdiag/releases/download/v0.3.0/"
RELEASE = {"tag_name": "v0.3.0", "name": "MeshLens 0.3.0", "body": "Fixes", "html_url": "https://github.com/x",
           "assets": [{"name": "MeshLens-0.3.0-setup.exe", "browser_download_url": BASE + "MeshLens-0.3.0-setup.exe"},
                      {"name": "SHA256SUMS.txt", "browser_download_url": BASE + "SHA256SUMS.txt"}]}


def fetcher(files):
    def fetch(url, timeout=20):
        for key, data in files.items():
            if url.endswith(key):
                return data
        raise AssertionError(url)
    return fetch


def test_versions():
    assert updater.is_newer("v0.3.0", "0.2.1") and updater.is_newer("0.2.10", "0.2.9")
    assert not updater.is_newer("0.2.1", "0.2.1") and not updater.is_newer("0.2.0", "0.2.1")
    assert updater.is_newer("0.3.0", "0.3.0-dev.4")          # final release beats the pre-release of it
    assert updater.parse_version("1.2") == ((1, 2, 0), False)


def test_check_finds_newer_release():
    info = updater.check(fetcher({"/releases/latest": json.dumps(RELEASE).encode()}), current="0.2.1")
    assert info["available"] and info["latest"] == "0.3.0" and info["installer"] == "MeshLens-0.3.0-setup.exe"
    assert info["sums_url"].endswith("SHA256SUMS.txt") and info["notes"] == "Fixes"
    same = updater.check(fetcher({"/releases/latest": json.dumps(RELEASE).encode()}), current="0.3.0")
    assert not same["available"]


def test_download_verifies_checksum(tmp_path):
    info = updater.check(fetcher({"/releases/latest": json.dumps(RELEASE).encode()}), current="0.2.1")
    files = {"SHA256SUMS.txt": GOOD_SUMS.encode(), "setup.exe": SETUP}
    path = updater.download(info, tmp_path / "updates", fetcher(files))
    assert path.read_bytes() == SETUP and path.name == "MeshLens-0.3.0-setup.exe"
    with pytest.raises(ValueError, match="checksum"):
        updater.download(info, tmp_path / "u2", fetcher({**files, "setup.exe": b"tampered"}))
    with pytest.raises(ValueError, match="not listed"):
        updater.download(info, tmp_path / "u3", fetcher({**files, "SHA256SUMS.txt": b"abc  other.exe\n"}))


def test_download_refuses_other_hosts_and_missing_sums(tmp_path):
    info = {"installer": "MeshLens-0.3.0-setup.exe", "installer_url": "https://evil.example/MeshLens-0.3.0-setup.exe",
            "sums_url": BASE + "SHA256SUMS.txt"}
    with pytest.raises(ValueError, match="GitHub"):
        updater.download(info, tmp_path, fetcher({}))
    info = {"installer": "MeshLens-0.3.0-setup.exe", "installer_url": BASE + "MeshLens-0.3.0-setup.exe", "sums_url": None}
    with pytest.raises(ValueError, match="SHA256SUMS"):
        updater.download(info, tmp_path, fetcher({}))


def test_logins_survive_restart_and_tokens_are_not_stored(tmp_path):
    a = Auth(tmp_path / "web_auth.json")
    a.set_password("a-long-password")
    token = a.new_session()
    assert token not in (tmp_path / "web_sessions.json").read_text()
    b = Auth(tmp_path / "web_auth.json")          # MeshLens restarted (e.g. after an update)
    assert b.valid(token)
    b.end_all()
    assert not Auth(tmp_path / "web_auth.json").valid(token)
