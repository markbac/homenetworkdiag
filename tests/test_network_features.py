import asyncio
import socket
import struct
import threading
import time

from aiohttp import web

from meshlens import config, topology
from meshlens.analysis import analyse
from meshlens.collectors.devolo import DevoloCollector, links
from meshlens.discovery import classify, ports, services, sweep
from meshlens.monitor import PingMonitor, summarise
from meshlens.net import icmp, speedtest
from meshlens.store import db

HEAD, OFFICE, ARLO = "02:fc:a6:00:00:01", "02:fc:a6:00:00:02", "02:fc:a6:00:00:03"
OVERVIEW = {
    "asked": "10.0.11.136",
    "devices": [
        {"mac": HEAD, "ip": "10.0.11.9", "name": "Hall", "product": "Magic 2 LAN 1-1", "firmware": "7.12",
         "attached_to_router": True, "bridged": ["0a:aa:00:00:00:01", "02:00:00:00:01:64"]},
        {"mac": OFFICE, "ip": "10.0.11.136", "name": "Office", "product": "Magic 2 LAN triple", "firmware": "7.12",
         "attached_to_router": False, "bridged": ["02:bb:00:00:00:07", "06:00:00:00:02:44"]},
        {"mac": ARLO, "ip": "10.0.11.236", "name": "Arlo", "product": "Magic 2 LAN 1-1", "firmware": "7.12",
         "attached_to_router": False, "bridged": []},
    ],
    "rates": [
        {"src": HEAD, "dst": OFFICE, "tx_mbps": 322, "rx_mbps": 108}, {"src": OFFICE, "dst": HEAD, "tx_mbps": 108, "rx_mbps": 322},
        {"src": HEAD, "dst": ARLO, "tx_mbps": 129, "rx_mbps": 50}, {"src": ARLO, "dst": HEAD, "tx_mbps": 50, "rx_mbps": 129},
        {"src": OFFICE, "dst": ARLO, "tx_mbps": 182, "rx_mbps": 7}, {"src": ARLO, "dst": OFFICE, "tx_mbps": 8, "rx_mbps": 182},
    ],
}


# ---------------------------------------------------------------- devolo

def test_devolo_collector_and_links(tmp_home):
    db.upsert_device(OFFICE, time.time(), ip="10.0.11.136", vendor="devolo solutions GmbH")
    asked = []

    async def fetch(ip):
        asked.append(ip)
        if ip == "10.0.11.1":
            raise OSError("no route")
        return OVERVIEW

    c = DevoloCollector(config.DevoloConfig(adapters=["10.0.11.1"]), tmp_home, fetcher=fetch)
    st = c.poll()
    assert st["error"] is None and st["adapters"] == 3 and asked == ["10.0.11.1", "10.0.11.136"]
    assert {a["mac"] for a in db.plc_adapters()} == {HEAD, OFFICE, ARLO}
    d = db.device(OFFICE)
    assert d["dtype"] == "powerline" and d["model"] == "Magic 2 LAN triple"
    ls = links(db.plc_adapters(), db.plc_latest_rates())
    assert ls[0]["slowest"] == 8 and {ls[0]["a_name"], ls[0]["b_name"]} == {"Office", "Arlo"}
    assert next(x for x in ls if {x["a"], x["b"]} == {HEAD, OFFICE})["slowest"] == 108


def test_devolo_no_adapters(tmp_home):
    st = DevoloCollector(config.DevoloConfig(), tmp_home).poll()
    assert "no devolo adapters" in st["error"]


# ---------------------------------------------------------------- monitor

def test_summarise_jitter_loss():
    s = summarise([10.0, 12.0, None, 11.0, 30.0])
    assert s["sent"] == 5 and s["lost"] == 1 and s["min_ms"] == 10 and s["max_ms"] == 30
    assert s["jitter_ms"] == round((2 + 1 + 19) / 3, 2) and s["avg_ms"] == 15.75
    assert summarise([None, None])["avg_ms"] is None


def test_ping_monitor_minutes(tmp_home):
    seq = iter([1.0, 2.0, None, 3.0])

    def pinger(host, timeout):
        v = next(seq)
        return icmp.Reply(v is not None, v)

    m = PingMonitor(config.MonitorConfig(), lambda: [{"target": "10.0.10.1", "kind": "router", "label": "Router"}], pinger)
    m.minute = 1000
    for i in range(3):
        m.tick(1000 * 60 + i)
    m.tick(1001 * 60 + 1)     # next minute: the first one is stored
    rows = db.ping_minutes(0)
    assert len(rows) == 1 and rows[0]["sent"] == 3 and rows[0]["lost"] == 1 and rows[0]["kind"] == "router"
    live = m.live()
    assert live[0]["label"] == "Router" and len(live[0]["series"]) == 4
    m.pool.shutdown()


# ---------------------------------------------------------------- speed test

def test_speedtest_against_local_server():
    async def down(request):
        n = int(request.query["bytes"])
        resp = web.StreamResponse()
        await resp.prepare(request)
        chunk = b"x" * 65536
        sent = 0
        while sent < n:
            await resp.write(chunk)
            sent += len(chunk)
        return resp

    async def up(request):
        async for _ in request.content.iter_chunked(65536):
            pass
        return web.Response(text="ok")

    app = web.Application(client_max_size=1 << 30)
    app.router.add_get("/__down", down)
    app.router.add_post("/__up", up)
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", port).start())
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        calls = []

        def pinger(host, timeout):
            calls.append(host)
            return icmp.Reply(True, 10.0)

        res = speedtest.run(f"http://127.0.0.1:{port}", seconds=2, ping_target="x", pinger=pinger)
    finally:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        t.join(5)
    assert res["error"] is None, res["error"]
    assert res["down_mbps"] > 1 and res["up_mbps"] > 1
    assert res["idle_ms"] == 10.0 and res["grade"] == "A+" and calls


def test_bufferbloat_grades():
    assert speedtest.grade(10, [12, 14]) == "A+"
    assert speedtest.grade(10, [35, 20]) == "A"
    assert speedtest.grade(10, [150, 20]) == "C"
    assert speedtest.grade(10, [500, None]) == "F"
    assert speedtest.grade(None, [5]) is None


# ---------------------------------------------------------------- discovery

def test_addresses_and_arp_parse():
    ips = sweep.addresses(["10.0.10.0/30", "10.0.11.0/29"], exclude=["10.0.11.2", "10.0.11.4/31"])
    assert ips == ["10.0.10.1", "10.0.10.2", "10.0.11.1", "10.0.11.3", "10.0.11.6"]
    assert sweep.addresses(["10.0.0.0/8"]) == []     # refuses huge ranges
    text = """
Interface: 10.0.10.50 --- 0x7
  Internet Address      Physical Address      Type
  10.0.10.1             02-00-00-00-01-64     dynamic
  10.0.11.9             02-fc-a6-00-00-01     dynamic
  10.0.11.255           ff-ff-ff-ff-ff-ff     static
  224.0.0.22            01-00-5e-00-00-16     static
"""
    assert sweep.parse_arp(text) == {"10.0.10.1": "02:00:00:00:01:64", "10.0.11.9": "02:fc:a6:00:00:01"}


def test_netbios_parse():
    q = sweep._nbstat_query(7)
    assert q[:2] == b"\x00\x07" and q[12] == 0x20 and q.endswith(b"\x00\x21\x00\x01")

    def name(n, suffix, flags):
        return n.ljust(15).encode() + bytes([suffix]) + struct.pack(">H", flags)

    body = bytes([3]) + name("WORKGROUP", 0, 0x8400) + name("GAMING-PC", 0, 0x0400) + name("GAMING-PC", 0x20, 0x0400)
    resp = struct.pack(">HHHHHH", 7, 0x8400, 0, 1, 0, 0) + b"\x20" + b"CK" + b"A" * 30 + b"\x00" + \
        struct.pack(">HHIH", 0x21, 1, 0, len(body)) + body
    assert sweep.parse_nbstat(resp) == {"netbios": "GAMING-PC", "workgroup": "WORKGROUP"}
    assert sweep.parse_nbstat(b"\x00\x01") == {"netbios": None, "workgroup": None}


def test_upnp_description():
    xml = b"""<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>
      <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType><friendlyName>Living room TV</friendlyName>
      <manufacturer>Samsung</manufacturer><modelName>QE55</modelName></device></root>"""
    d = services.parse_upnp_description(xml)
    assert d["friendly_name"] == "Living room TV" and d["device_type"] == "MediaRenderer" and d["model"] == "QE55"


def test_classify():
    assert classify({"vendor": "devolo solutions"}, set()) == "powerline"
    assert classify({"services": '["Google Cast"]'}, set()) == "tv / streamer"
    assert classify({"name": "x"}, {3389}) == "computer"
    assert classify({"randomised": 1}, set()) == "phone / tablet"
    assert classify({"vendor": "Unknown Co"}, set()) is None


def test_port_scan_local_and_history(tmp_home):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    try:
        found = asyncio.run(ports.scan_host("127.0.0.1", [port, 1], timeout=0.5))
    finally:
        srv.close()
    assert [f["port"] for f in found] == [port]
    mac = "02:aa:00:00:00:02"
    db.upsert_device(mac, time.time(), ip="127.0.0.1")
    ch = db.record_ports(mac, "127.0.0.1", [{"port": 23, "service": "Telnet", "risk": ports.RISKS[23]}], [22, 23], "tcp", 1.0)
    assert ch[0]["kind"] == "port_opened" and ch[0]["first_scan"] is True
    ch = db.record_ports(mac, "127.0.0.1", [{"port": 22, "service": "SSH"}], [22, 23], "tcp", 2.0)
    assert {c["kind"] for c in ch} == {"port_opened", "port_closed"}
    assert [p["port"] for p in db.open_ports(mac)] == [22]
    assert len(db.inventory_events(0, mac)) == 3


# ---------------------------------------------------------------- topology

def test_topology_tree():
    now = time.time()
    units = [{"mac": "02:00:00:00:01:64", "role": "router", "alias": "Main", "reachable": 1, "macs": []},
             {"mac": "02:00:00:00:01:60", "role": "node", "alias": "First Floor", "reachable": 1,
              "macs": ["06:00:00:00:01:61"]},
             {"mac": "02:00:00:00:02:44", "role": "node", "alias": "Office", "reachable": 1, "macs": ["06:00:00:00:02:44"]}]
    stations = [{"ts": now, "unit": "02:00:00:00:01:64", "mac": "06:00:00:00:01:61", "band": "5", "rssi": -58, "tx_rate": 1200},
                {"ts": now, "unit": "02:00:00:00:01:60", "mac": "02:aa:00:00:00:02", "band": "5", "rssi": -66, "tx_rate": 400}]
    devices = [{"mac": "02:aa:00:00:00:02", "name": "Laptop", "last_seen": now},
               {"mac": "02:bb:00:00:00:07", "name": "Desk PC", "wired": 1, "unit": "02:00:00:00:01:64", "last_seen": now},
               {"mac": "02:bb:00:00:00:08", "name": "Old", "wired": 1, "last_seen": now - 9999}]
    plc = [dict(a, bridged=a["bridged"]) for a in OVERVIEW["devices"]]
    t = topology.build(units, devices, stations, plc, OVERVIEW["rates"], now=now)
    n = {x["id"]: x for x in t["nodes"]}
    assert n["02:00:00:00:01:64"]["parent"] == "internet"
    ff = n["02:00:00:00:01:60"]
    assert ff["parent"] == "02:00:00:00:01:64" and ff["link"]["type"] == "wireless" and ff["link"]["rssi"] == -58
    # Office node is bridged behind the Office powerline adapter: powerline backhaul.
    assert n["02:00:00:00:02:44"]["parent"] == OFFICE and n[OFFICE]["parent"] == HEAD
    assert n[OFFICE]["link"] == {"type": "powerline", "down_mbps": 322, "up_mbps": 108, "slowest": 108}
    assert n[HEAD]["parent"] == "02:00:00:00:01:64"
    assert n["02:aa:00:00:00:02"]["parent"] == "02:00:00:00:01:60" and n["02:aa:00:00:00:02"]["link"]["type"] == "wifi"
    assert n["02:bb:00:00:00:07"]["parent"] == OFFICE          # wired device behind the Office adapter
    assert "02:bb:00:00:00:08" not in n                          # offline devices are left out
    assert any(e["type"] == "powerline" for e in t["edges"])


# ---------------------------------------------------------------- analysis of the new data

def test_network_rules():
    net = {
        "plc_adapters": OVERVIEW["devices"], "plc_rates": OVERVIEW["rates"], "devolo_cfg": config.DevoloConfig(),
        "monitor_cfg": config.MonitorConfig(expected_down_mbps=500, expected_up_mbps=50),
        "pings": [{"ts": 1000 + i * 60, "target": "10.0.10.1", "kind": "router", "label": "Router", "sent": 30, "lost": 0,
                   "avg_ms": 3, "max_ms": 9, "p95_ms": 6, "jitter_ms": 1} for i in range(3)]
                 + [{"ts": 1000 + i * 60, "target": "1.1.1.1", "kind": "internet", "label": "1.1.1.1", "sent": 30, "lost": 2,
                     "avg_ms": 20, "max_ms": 300, "p95_ms": 40, "jitter_ms": 9} for i in range(6)],
        "speeds": [{"ts": 5000, "down_mbps": 180, "up_mbps": 45, "idle_ms": 12, "down_loaded_ms": 250, "up_loaded_ms": 90,
                    "grade": "D", "error": None}],
        "open_ports": [{"mac": "02:aa:00:00:00:09", "port": 23, "service": "Telnet", "risk": "Telnet: unencrypted"}],
        "inventory": [{"ts": 4000, "mac": "02:aa:00:00:00:09", "kind": "port_opened", "detail": {"port": 8080, "first_scan": False}},
                      {"ts": 4000, "mac": "02:aa:00:00:00:09", "kind": "port_opened", "detail": {"port": 80, "first_scan": True}}],
    }
    res = analyse([], [], [], [], [{"mac": "02:aa:00:00:00:09", "name": "Camera"}], {}, now=6000, net=net)
    rules = [i["rule"] for i in res["issues"]]
    plc = [i for i in res["issues"] if i["rule"] == "powerline_slow"]
    assert len(plc) == 1 and plc[0]["severity"] == "high" and "Office" in plc[0]["title"]
    isp = next(i for i in res["issues"] if i["rule"] == "isp_problem")
    assert "Inside your home the same pings were clean" in isp["evidence"]
    assert "local_latency" not in rules
    bb = next(i for i in res["issues"] if i["rule"] == "bufferbloat")
    assert bb["severity"] == "high" and any("Adaptive QoS" in f for f in bb["fixes"])
    assert "speed_low" in rules and rules.count("new_open_port") == 1 and "risky_port" in rules


def test_proxy_arp_entries_are_not_devices():
    from meshlens.discovery import split_proxied
    units = [{"mac": "02:00:00:00:01:64", "macs": []}, {"mac": "02:00:00:00:01:60", "macs": ["06:00:00:00:01:61"]}]
    arp = {"10.0.10.1": "02:00:00:00:01:64",                      # the router itself
           "10.0.11.29": "0a:00:00:00:01:60",                      # node's own address (byte-0 variant): skipped
           "10.0.11.5": "0a:00:00:00:01:60", "10.0.11.6": "0a:00:00:00:01:60",   # node answers for its clients
           "10.0.11.7": "02:aa:00:00:00:01", "10.0.11.8": "02:aa:00:00:00:02"}
    usable, proxied = split_proxied(arp, units)
    assert usable == {"10.0.11.7": "02:aa:00:00:00:01", "10.0.11.8": "02:aa:00:00:00:02"}
    assert sorted(proxied) == ["10.0.11.29", "10.0.11.5", "10.0.11.6"]


def test_mdns_name_prefers_friendly():
    from meshlens.discovery import mdns_name
    assert mdns_name({"friendly": "Kitchen speaker", "names": ["Google-Nest-Mini-1cf29a"], "host": "1CF29A492944"}) == "Kitchen speaker"
    assert mdns_name({"names": [], "host": "889425ca-3fa2-6f5e-849c-db966cddcf8e"}) is None
    assert mdns_name({"names": ["NASHOME"], "host": "1CF29A492944"}) == "NASHOME"
