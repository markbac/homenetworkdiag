import asyncio
import time

from aiohttp.test_utils import TestClient, TestServer

from meshlens import graphs
from meshlens.web import connect


def test_events_and_clients_buckets():
    now = time.time()
    since = now - 3600
    ev = [{"ts": now - 100, "kind": "kick"}, {"ts": now - 90, "kind": "kick"}, {"ts": now - 50, "kind": "roam_candidate"},
          {"ts": now - 7200, "kind": "kick"}]
    e = graphs.events_per_bucket(ev, since, 300)
    assert sum(v for _, v in e["Kicks"]) == 2 and sum(v for _, v in e["Roam attempts"]) == 1
    samples = [{"ts": 1000, "unit": "u1", "mac": "a", "band": "5"}, {"ts": 1010, "unit": "u1", "mac": "b", "band": "2.4"},
               {"ts": 1300, "unit": "u2", "mac": "a", "band": "6"}]
    c = graphs.clients_over_time(samples, {"u1": "Main", "u2": "Office"}, 300)
    assert c["by_unit"]["Main"] == [[900, 2], [1200, 0]] and c["by_unit"]["Office"] == [[900, 0], [1200, 1]]
    assert set(c["by_band"]) == {"5 GHz", "2.4 GHz", "6 GHz"}


def test_rssi_histogram_and_heatmap():
    now = time.time()
    h = graphs.rssi_histogram([{"ts": now, "rssi": -42}, {"ts": now, "rssi": -77}, {"ts": now - 5000, "rssi": -50},
                               {"ts": now, "rssi": -99}], now)
    by = {b["from"]: b["count"] for b in h}
    assert by[-45] == 1 and by[-80] == 1 and by[-90] == 1 and sum(by.values()) == 3
    t = time.mktime((2026, 11, 16, 20, 15, 0, 0, 0, -1))      # a Monday 20:15
    hm = graphs.latency_heatmap([{"ts": t, "kind": "internet", "p95_ms": 30, "lost": 1, "sent": 30},
                                 {"ts": t + 60, "kind": "internet", "p95_ms": 90, "lost": 0, "sent": 30},
                                 {"ts": t, "kind": "router", "p95_ms": 900, "lost": 0, "sent": 30}])
    cell = hm["grid"][0][20]
    assert cell["minutes"] == 2 and cell["p95"] == 90 and cell["loss_pct"] == round(100 / 60, 2)


def test_throughput_wan_wifi_and_counter_reset():
    c = [{"ts": 0, "unit": "r", "iface": "ppp0", "rx_bytes": 0, "tx_bytes": 0},
         {"ts": 60, "unit": "r", "iface": "ppp0", "rx_bytes": 75_000_000, "tx_bytes": 7_500_000},
         {"ts": 120, "unit": "r", "iface": "ppp0", "rx_bytes": 10, "tx_bytes": 10},           # reboot: skipped
         {"ts": 0, "unit": "r", "iface": "rai1", "rx_bytes": 0, "tx_bytes": 0},
         {"ts": 60, "unit": "r", "iface": "rai1", "rx_bytes": 3_750_000, "tx_bytes": 3_750_000},
         {"ts": 0, "unit": "r", "iface": "eth0", "rx_bytes": 0, "tx_bytes": 0},
         {"ts": 60, "unit": "r", "iface": "eth0", "rx_bytes": 9e9, "tx_bytes": 9e9}]
    t = graphs.throughput(c, {"r": "Main"}, "r")
    assert t["wan_iface"] == "ppp0" and t["wan"]["Download"] == [[60, 10.0]] and t["wan"]["Upload"] == [[60, 1.0]]
    assert t["wifi"] == {"Main 5 GHz": [[60, 1.0]]}


def test_powerline_and_latency_series():
    ad = [{"mac": "a", "name": "Hall"}, {"mac": "b", "name": "Office"}]
    r = [{"ts": 1, "src": "a", "dst": "b", "tx_mbps": 300}, {"ts": 1, "src": "b", "dst": "a", "tx_mbps": 100}]
    assert graphs.powerline_series(r, ad) == {"Hall ↔ Office": [[1, 100]]}
    p = [{"ts": 1, "target": "10.0.11.29", "label": "ZenWiFi_BT8", "kind": "node", "avg_ms": 5, "lost": 0, "sent": 30},
         {"ts": 2, "target": "10.0.11.29", "label": "First Floor", "kind": "node", "avg_ms": 6, "lost": 0, "sent": 30}]
    assert graphs.latency_series(p)["avg"] == {"First Floor": [[1, 5], [2, 6]]}     # renamed unit: one line


def test_qr_and_icon():
    svg = connect.qr_svg("http://minisf.example:8787/")
    assert svg.startswith(b"<?xml") and b"<svg" in svg
    assert connect.icon_png(64)[:8] == b"\x89PNG\r\n\x1a\n"


def test_urls_prefer_router_dns_name(monkeypatch):
    monkeypatch.setattr(connect, "lan_address", lambda router: "10.0.11.63")
    monkeypatch.setattr(connect.socket, "gethostname", lambda: "MINISF")

    def fake_dns(server, name, qtype=1, timeout=2.0):
        if qtype == 12:
            return ["MINISF.home.example", "host.docker.internal"]
        return {"minisf.home.example": ["10.0.11.63"], "minisf": ["10.0.11.63"]}.get(name, [])
    monkeypatch.setattr(connect, "dns_query", fake_dns)
    urls = [u["url"] for u in connect.urls(8787, "10.0.10.1", True)]
    assert urls == ["http://minisf.home.example:8787/", "http://10.0.11.63:8787/", "http://meshlens.local:8787/",
                    "http://minisf:8787/"]                                   # docker name dropped: router doesn't know it


def test_public_manifest_icon_and_headers(tmp_home):
    from meshlens import config
    from meshlens.web.auth import Auth
    from meshlens.web.server import build_app

    class E:
        cfg = config.Config()

    async def scenario():
        async with TestClient(TestServer(build_app(E(), Auth(tmp_home / "web_auth.json")))) as c:
            m = await c.get("/manifest.webmanifest")
            assert m.status == 200 and (await m.json())["display"] == "standalone"
            i = await c.get("/icon-192.png")
            assert i.status == 200 and i.headers["Content-Type"] == "image/png"
            assert (await c.get("/api/connect")).status == 401                  # addresses need a login
            r = await c.get("/")
            assert r.headers["Cache-Control"] == "no-cache" and "script-src 'self'" in r.headers["Content-Security-Policy"]
    asyncio.run(scenario())
