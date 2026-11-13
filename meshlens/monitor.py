"""Latency / jitter / loss monitors (TST-01, TST-05) and scheduled speed tests (TST-02/03).

Every target is pinged every ``interval_seconds``; results are folded into one row per target per
minute (sent, lost, avg, min, max, p95, jitter) so a month of history stays small.
Jitter = mean absolute difference between consecutive replies (RFC 3550 style, unsmoothed).
"""
from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .net import icmp
from .store import db

log = logging.getLogger("meshlens.monitor")


def summarise(rtts: list[float | None]) -> dict[str, Any]:
    """One minute (or any window) of ping results; ``None`` = lost."""
    ok = [r for r in rtts if r is not None]
    out: dict[str, Any] = {"sent": len(rtts), "lost": len(rtts) - len(ok), "avg_ms": None, "min_ms": None,
                           "max_ms": None, "p95_ms": None, "jitter_ms": None}
    if ok:
        s = sorted(ok)
        out.update(avg_ms=round(statistics.fmean(ok), 2), min_ms=round(s[0], 2), max_ms=round(s[-1], 2),
                   p95_ms=round(s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))], 2))
        if len(ok) > 1:
            out["jitter_ms"] = round(statistics.fmean(abs(a - b) for a, b in zip(ok, ok[1:])), 2)
    return out


class PingMonitor:
    def __init__(self, cfg, targets_fn: Callable[[], list[dict[str, str]]], pinger=icmp.ping_target):
        self.cfg = cfg                 # MonitorConfig
        self.targets_fn = targets_fn   # -> [{"target", "kind", "label"}]
        self.pinger = pinger
        self.buckets: dict[str, list[float | None]] = defaultdict(list)
        self.meta: dict[str, dict[str, str]] = {}
        self.recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=150))   # last ~5 min for the live view
        self.minute = int(time.time() // 60)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.paused = False            # speed tests pause the monitor so they don't skew each other
        self.pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="ping")

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="meshlens-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.pool.shutdown(wait=False, cancel_futures=True)

    def tick(self, now: float | None = None) -> None:
        """Ping every target once and store finished minutes."""
        now = now or time.time()
        self.flush(now)   # close the previous minute before adding this round's results
        targets = self.targets_fn() if self.cfg.enabled else []
        futs = {t["target"]: (t, self.pool.submit(self.pinger, t["target"], 1000)) for t in targets}
        for target, (t, f) in futs.items():
            try:
                r = f.result(timeout=3)
                rtt = r.rtt_ms if r.ok else None
            except Exception:
                rtt = None
            self.meta[target] = t
            self.buckets[target].append(rtt)
            self.recent[target].append((now, rtt))

    def flush(self, now: float, force: bool = False) -> int:
        minute = int(now // 60)
        if minute == self.minute and not force:
            return 0
        rows = []
        for target, rtts in self.buckets.items():
            if rtts:
                m = self.meta.get(target, {})
                rows.append({"ts": self.minute * 60, "target": target, "kind": m.get("kind"), "label": m.get("label"),
                             **summarise(rtts)})
        self.buckets = defaultdict(list)
        self.minute = minute
        if rows:
            db.insert_many("ping_minutes", rows)
        return len(rows)

    def live(self) -> list[dict[str, Any]]:
        """Last ~5 minutes per target for the dashboard."""
        out = []
        for target, dq in self.recent.items():
            m = self.meta.get(target, {})
            vals = [r for _, r in dq]
            out.append({"target": target, "kind": m.get("kind"), "label": m.get("label"),
                        "last_ms": vals[-1] if vals else None, **summarise(vals),
                        "series": [[round(t, 1), r] for t, r in dq]})
        order = {"router": 0, "node": 1, "isp": 2, "internet": 3, "extra": 4, "device": 5}
        return sorted(out, key=lambda x: (order.get(x["kind"] or "", 9), x["label"] or x["target"]))

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            if not self.paused:
                try:
                    self.tick(t0)
                except Exception:
                    log.exception("monitor tick failed")
            self._stop.wait(max(0.2, self.cfg.interval_seconds - (time.time() - t0)))
        self.flush(time.time(), force=True)
