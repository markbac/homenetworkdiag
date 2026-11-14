"""Speed test on the ASUS router itself (the internet line, without Wi-Fi or powerline in the way).

Two parts:
- ``parse_history``: the results the router already stores in /jffs/.sys/OOKLA/ookla_history (tests run from
  the ASUS app or web UI). Read-only; always available.
- ``run_script`` / ``parse_run``: run the firmware's own Ookla CLI (/usr/sbin/ookla). Running it needs Ookla's
  licence and GDPR terms accepted, so it only happens when the user switches it on in Settings. A ping loop
  on the router runs alongside, so latency before, during download and during upload gives the bufferbloat
  grade the same way as the PC's test. Only temporary files in /tmp are written (and removed); no settings.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
from typing import Any

from ...net.speedtest import grade

HISTORY = "/jffs/.sys/OOKLA/ookla_history"


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _result_row(d: dict[str, Any]) -> dict[str, Any]:
    dl, ul, p = d.get("download") or {}, d.get("upload") or {}, d.get("ping") or {}
    srv = d.get("server") or {}
    return {
        "ts": _ts(d.get("timestamp")),
        "down_mbps": round(dl["bandwidth"] * 8 / 1e6, 1) if dl.get("bandwidth") else None,
        "up_mbps": round(ul["bandwidth"] * 8 / 1e6, 1) if ul.get("bandwidth") else None,
        "idle_ms": round(p["latency"], 1) if p.get("latency") is not None else None,
        "down_loaded_ms": round((dl.get("latency") or {}).get("iqm"), 1) if (dl.get("latency") or {}).get("iqm") is not None else None,
        "up_loaded_ms": round((ul.get("latency") or {}).get("iqm"), 1) if (ul.get("latency") or {}).get("iqm") is not None else None,
        "server": f"Ookla: {srv.get('name') or '?'} ({srv.get('location') or '?'})" + (f", ISP {d['isp']}" if d.get("isp") else ""),
    }


def parse_history(text: str) -> list[dict[str, Any]]:
    """Stored results (one JSON object per line) -> speed test rows."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "result":
            r = _result_row(d)
            if r["ts"] and r["down_mbps"] is not None:
                r["grade"] = grade(r["idle_ms"], [r["down_loaded_ms"], r["up_loaded_ms"]])
                out.append(r)
    return out


def run_script(ping_target: str = "1.1.1.1") -> str:
    """Batch script: ping loop in the background, Ookla in the foreground, then print both."""
    t = ping_target.replace("'", "")
    return f"""rm -f /tmp/ml_ping.txt /tmp/ml_ookla.jsonl; touch /tmp/ml_run
( while [ -f /tmp/ml_run ]; do echo "$(date +%s) $(ping -c 1 -W 1 '{t}' 2>/dev/null | grep -o 'time=[0-9.]*')"; usleep 250000 2>/dev/null || sleep 1; done > /tmp/ml_ping.txt ) &
sleep 4
/usr/sbin/ookla --accept-license --accept-gdpr -f jsonl -p yes --progress-update-interval=500 > /tmp/ml_ookla.jsonl 2>/dev/null
sleep 2; rm -f /tmp/ml_run; sleep 2
echo "@@@ ookla"; cat /tmp/ml_ookla.jsonl; echo
echo "@@@ ping"; cat /tmp/ml_ping.txt; echo
rm -f /tmp/ml_ping.txt /tmp/ml_ookla.jsonl
"""


def parse_run(ookla_jsonl: str, ping_text: str) -> dict[str, Any]:
    """Ookla progress/result events + timestamped pings -> one speed test row with loaded latency."""
    events = []
    for line in ookla_jsonl.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    result = next((e for e in events if e.get("type") == "result"), None)
    errors = [e.get("message") for e in events if e.get("type") == "log" and e.get("level") == "error"]
    if not result:
        return {"error": "router speed test gave no result" + (f": {errors[-1]}" if errors else "")}
    row = _result_row(result)

    def window(kind: str) -> tuple[float, float] | None:
        ts = [_ts(e.get("timestamp")) for e in events if e.get("type") == kind]
        ts = [t for t in ts if t]
        return (min(ts), max(ts) + 1) if ts else None

    pings: list[tuple[float, float]] = []
    for line in ping_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("time="):
            try:
                pings.append((float(parts[0]), float(parts[1][5:])))
            except ValueError:
                pass
    dl, ul = window("download"), window("upload")
    start = min((w[0] for w in (window("ping"), dl) if w), default=None)

    def med(lo: float | None, hi: float | None) -> float | None:
        vals = [v for t, v in pings if (lo is None or t >= lo) and (hi is None or t < hi)]
        return round(statistics.median(vals), 1) if vals else None

    idle = med(None, start) if start else None
    row["idle_ms"] = idle if idle is not None else row["idle_ms"]
    row["down_loaded_ms"] = (med(*dl) if dl else None) or row["down_loaded_ms"]
    row["up_loaded_ms"] = (med(*ul) if ul else None) or row["up_loaded_ms"]
    row["grade"] = grade(row["idle_ms"], [row["down_loaded_ms"], row["up_loaded_ms"]])
    row["error"] = None
    return row
