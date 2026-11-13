"""Raw records as JSON Lines, one file per day (NFR-05): data/raw/YYYY-MM-DD.jsonl."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def append(root: Path, record: dict[str, Any]) -> None:
    rec = dict(record)
    rec.setdefault("time", time.strftime("%Y-%m-%dT%H:%M:%S"))
    d = root / "raw"
    d.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, separators=(",", ":"), default=str)
    with _lock, open(d / f"{time.strftime('%Y-%m-%d')}.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def prune(root: Path, keep_days: int) -> None:
    cut = time.strftime("%Y-%m-%d", time.localtime(time.time() - keep_days * 86400))
    for p in (root / "raw").glob("*.jsonl"):
        if p.stem < cut:
            p.unlink(missing_ok=True)
