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
        self.sessions: dict[str, float] = {}
        self.failures: dict[str, list[float]] = {}

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
        self.sessions[token] = time.time() + SESSION_SECONDS
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        exp = self.sessions.get(token)
        if exp is None or exp < time.time():
            self.sessions.pop(token, None)
            return False
        return True

    def end(self, token: str | None) -> None:
        if token:
            self.sessions.pop(token, None)

    def end_all(self) -> None:
        """Log out every session (after a password change)."""
        self.sessions.clear()
