"""Dashboard login (NFR-04): one admin password, PBKDF2-hashed, in-memory sessions, rate limiting."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets as pysecrets
import time
from pathlib import Path

ITERATIONS = 310_000
SESSION_SECONDS = 7 * 86400
MAX_FAILURES = 5          # per client address
LOCKOUT_SECONDS = 300


class Auth:
    def __init__(self, path: Path):
        self.path = path
        self.sessions_path = path.with_name("web_sessions.json")
        self.sessions: dict[str, float] = {}      # sha256(token) -> expiry; the tokens themselves are never stored
        self.failures: dict[str, list[float]] = {}
        try:
            now = time.time()
            self.sessions = {k: v for k, v in json.loads(self.sessions_path.read_text()).items() if v > now}
        except (OSError, ValueError):
            pass

    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _save_sessions(self) -> None:
        try:
            self.sessions_path.write_text(json.dumps(self.sessions))
        except OSError:
            pass

    # ------------------------------------------------------------ password

    @property
    def configured(self) -> bool:
        return self.path.exists()

    def set_password(self, password: str) -> None:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
        self.path.write_text(json.dumps({"salt": salt.hex(), "hash": digest.hex(), "iterations": ITERATIONS}))

    def check_password(self, password: str) -> bool:
        if not self.configured:
            return False
        d = json.loads(self.path.read_text())
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(d["salt"]), d["iterations"])
        return hmac.compare_digest(digest.hex(), d["hash"])

    # ------------------------------------------------------------ rate limit

    def locked(self, client: str) -> bool:
        now = time.time()
        recent = [t for t in self.failures.get(client, []) if now - t < LOCKOUT_SECONDS]
        self.failures[client] = recent
        return len(recent) >= MAX_FAILURES

    def failed(self, client: str) -> None:
        self.failures.setdefault(client, []).append(time.time())

    # ------------------------------------------------------------ sessions

    def new_session(self) -> str:
        token = pysecrets.token_urlsafe(32)
        self.sessions[self._h(token)] = time.time() + SESSION_SECONDS
        self._save_sessions()
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        exp = self.sessions.get(self._h(token))
        if exp is None or exp < time.time():
            if self.sessions.pop(self._h(token), None) is not None:
                self._save_sessions()
            return False
        return True

    def end(self, token: str | None) -> None:
        if token and self.sessions.pop(self._h(token), None) is not None:
            self._save_sessions()

    def end_all(self) -> None:
        """Log out every session (after a password change)."""
        self.sessions.clear()
        self._save_sessions()
