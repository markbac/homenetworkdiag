"""Speed test with bufferbloat (loaded latency) measurement (TST-02, TST-03, ISP-05).

Three phases, the same method as the ESP32 panel: idle latency, then download with several parallel
streams while pinging, then upload while pinging. Bufferbloat is how much latency rises under load.
Default server: Cloudflare's speed test endpoints (``/__down?bytes=N`` and ``/__up``). No account and
nothing about the network is sent; only the test traffic itself.
"""
from __future__ import annotations

import asyncio
import os
import statistics
import threading
import time
from typing import Any, Callable

import aiohttp

from . import icmp

STREAMS = 4
CHUNK = 64 * 1024
DOWN_BYTES = 25_000_000
UP_BLOCK = os.urandom(CHUNK)
WARMUP_S = 1.0


def grade(idle_ms: float | None, loaded_ms: list[float | None]) -> str | None:
    """Bufferbloat grade from the rise in median latency under load (common A+..F scale)."""
    vals = [v for v in loaded_ms if v is not None]
    if idle_ms is None or not vals:
        return None
    rise = max(vals) - idle_ms
    for limit, g in ((5, "A+"), (30, "A"), (60, "B"), (200, "C"), (400, "D")):
        if rise < limit:
            return g
    return "F"


class _Pinger:
    """Pings in a background thread while a phase runs."""

    def __init__(self, target: str, pinger: Callable, every: float = 0.2):
        self.target, self.pinger, self.every = target, pinger, every
        self.rtts: list[float | None] = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            r = self.pinger(self.target, 1000)
            self.rtts.append(r.rtt_ms if r.ok else None)
            self._stop.wait(self.every)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=3)

    def median(self) -> float | None:
        ok = [r for r in self.rtts if r is not None]
        return round(statistics.median(ok), 1) if ok else None


async def _download(session: aiohttp.ClientSession, server: str, seconds: float) -> float:
    counted = 0
    start = time.monotonic()
    deadline = start + seconds

    async def worker():
        nonlocal counted
        while time.monotonic() < deadline:
            async with session.get(f"{server}/__down", params={"bytes": str(DOWN_BYTES)}) as r:
                r.raise_for_status()
                async for chunk in r.content.iter_chunked(CHUNK):
                    if time.monotonic() - start >= WARMUP_S:
                        counted += len(chunk)
                    if time.monotonic() >= deadline:
                        return

    await asyncio.gather(*(worker() for _ in range(STREAMS)))
    return counted * 8 / max(0.1, time.monotonic() - start - WARMUP_S) / 1e6


async def _upload(session: aiohttp.ClientSession, server: str, seconds: float) -> float:
    counted = 0
    start = time.monotonic()
    deadline = start + seconds

    async def body():
        nonlocal counted
        sent = 0
        while time.monotonic() < deadline and sent < DOWN_BYTES:
            yield UP_BLOCK
            sent += len(UP_BLOCK)
            if time.monotonic() - start >= WARMUP_S:
                counted += len(UP_BLOCK)

    async def worker():
        while time.monotonic() < deadline:
            async with session.post(f"{server}/__up", data=body(),
                                    headers={"Content-Type": "application/octet-stream"}) as r:
                await r.read()

    await asyncio.gather(*(worker() for _ in range(STREAMS)))
    return counted * 8 / max(0.1, time.monotonic() - start - WARMUP_S) / 1e6


def run(server: str, seconds: float = 10, ping_target: str = "1.1.1.1", pinger: Callable = icmp.ping,
        ssl: Any = None) -> dict[str, Any]:
    """Run the three phases; blocking. Returns Mbit/s, latencies (ms) and the bufferbloat grade."""
    server = server.rstrip("/")
    res: dict[str, Any] = {"server": server, "down_mbps": None, "up_mbps": None, "idle_ms": None,
                           "down_loaded_ms": None, "up_loaded_ms": None, "grade": None, "error": None}

    async def main():
        timeout = aiohttp.ClientTimeout(total=seconds * 3 + 30, sock_read=15)
        async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=ssl)) as s:
            with _Pinger(ping_target, pinger) as p:
                await asyncio.sleep(min(3.0, seconds / 3))
            res["idle_ms"] = p.median()
            with _Pinger(ping_target, pinger) as p:
                res["down_mbps"] = round(await _download(s, server, seconds), 1)
            res["down_loaded_ms"] = p.median()
            with _Pinger(ping_target, pinger) as p:
                res["up_mbps"] = round(await _upload(s, server, seconds), 1)
            res["up_loaded_ms"] = p.median()

    try:
        asyncio.run(main())
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    res["grade"] = grade(res["idle_ms"], [res["down_loaded_ms"], res["up_loaded_ms"]])
    return res
