import json
import time

from meshlens import config, graphs, hostpc
from meshlens.analysis import analyse
from meshlens.collectors.asus import parsers as P
from meshlens.collectors.asus import settings_info
from meshlens.collectors.asus import speedtest as rst

UNITS = [{"mac": "02:28:aa:6c:2e:64", "role": "router", "alias": "Main", "reachable": 1, "macs": []},
         {"mac": "02:28:aa:6c:2e:60", "role": "node", "alias": "First Floor", "reachable": 1, "macs": []}]

NETSH = """
Interface name : WiFi
There are 2 networks currently visible.

SSID 1 : TestNet
    Network type            : Infrastructure
    Authentication          : WPA3-Personal
    Encryption              : CCMP
    BSSID 1                 : 06:28:aa:1c:2e:64
         Signal             : 80%
         Radio type         : 802.11be
         Band               : 2.4 GHz
         Channel            : 11
         Bss Load:
             Connected Stations:         14
             Channel Utilization:        212 (83 %)
             Medium Available Capacity:  31250 (1000000 us/s)
    BSSID 2                 : 06:28:aa:5c:2e:61
         Signal             : 50%
         Radio type         : 802.11be
         Band               : 5 GHz
         Channel            : 56

SSID 2 : Neighbour
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : 0a:11:22:33:44:55
         Signal             : 60%
         Radio type         : 802.11ax
         Band               : 2.4 GHz
         Channel            : 11
"""


def test_parse_networks_and_own_unit():
    bss = hostpc.parse_networks(NETSH)
    assert [b["bssid"] for b in bss] == ["06:28:aa:1c:2e:64", "06:28:aa:5c:2e:61", "0a:11:22:33:44:55"]
    a = bss[0]
    assert a["ssid"] == "TestNet" and a["band"] == "2.4" and a["channel"] == 11 and a["util_pct"] == 83 and a["stations"] == 14
    assert a["rssi"] == -60 and bss[1]["util_pct"] is None and bss[2]["ssid"] == "Neighbour"
    # ASUS varies byte 0, byte 3 and the last byte; the closest last byte picks the unit.
    assert hostpc.own_unit("06:28:aa:1c:2e:64", UNITS) == "02:28:aa:6c:2e:64"
    assert hostpc.own_unit("06:28:aa:5c:2e:61", UNITS) == "02:28:aa:6c:2e:60"
    assert hostpc.own_unit("0a:11:22:33:44:55", UNITS) is None


def test_wifi_link_and_adapter_rates():
    link = hostpc.parse_interfaces("""    Name                   : WiFi
    Description            : Test adapter
    State                  : connected
    SSID                   : TestNet
    AP BSSID               : 06:28:aa:5c:2e:65
    Band                   : 5 GHz
    Channel                : 56
    Receive rate (Mbps)    : 1201
    Transmit rate (Mbps)   : 960.5
    Signal                 : 76%
""")
    assert link["connected"] and link["ssid"] == "TestNet" and link["channel"] == 56 and link["rssi"] == -62
    assert link["rx_mbps"] == 1201 and link["tx_mbps"] == 960.5
    assert hostpc.parse_interfaces("    State                  : disconnected\n")["connected"] is False
    prev = {"ReceivedBytes": 0, "SentBytes": 0, "ReceivedUnicastPackets": 0, "ReceivedMulticastPackets": 0,
            "ReceivedBroadcastPackets": 0, "ReceivedDiscardedPackets": 0, "ReceivedPacketErrors": 0}
    cur = {**prev, "ReceivedBytes": 7_500_000, "ReceivedBroadcastPackets": 600, "ReceivedMulticastPackets": 1200}
    r = hostpc.rates(prev, cur, 60)
    assert r["rx_mbps"] == 1.0 and r["bcast_pps"] == 10 and r["mcast_pps"] == 20
    assert hostpc.rates(cur, prev, 60) is None          # counters reset


def test_settings_describe_and_ports():
    s = {"wl0_ssid": "TestNet", "wl0_bw": "1", "wl0_user_rssi": "-70", "wl0_chanspec": "0", "wl1_ssid": "TestNet",
         "wl1_bw": "3", "wl1_user_rssi": "0", "wl0.1_ssid": "Guest", "wl0.1_bss_enabled": "1", "wl0.1_closed": "0",
         "emf_enable": "0", "wps_enable": "1", "sshd_enable": "1", "wan0_state_t": "2"}
    d = settings_info.describe(s)
    assert d["bands"] == ["2.4 GHz", "5 GHz"]
    rows = {r["key"]: r["values"] for r in d["band_rows"]}
    assert rows["bw"] == {"2.4 GHz": "1", "5 GHz": "3"} and rows["user_rssi"]["5 GHz"] == "off"
    assert rows["chanspec"]["2.4 GHz"] == "Auto" and d["networks"][0]["ssid"] == "Guest"
    net = {r["key"]: r["value"] for r in d["network"] + d["admin"]}
    assert net["emf_enable"] == "off" and net["wps_enable"] == "on" and net["sshd_enable"] == "LAN only" and net["wan0_state_t"] == "connected"
    info = {"ports": {"port_info": {"02:28:AA:6C:2E:64": {
        "W0": {"is_on": "1", "max_rate": "2500", "link_rate": "1000", "ui_display": "2.5G WAN"},
        "L1": {"is_on": "1", "max_rate": "1000", "link_rate": "1000", "ui_display": "LAN1"},
        "L2": {"is_on": "0", "max_rate": "1000", "link_rate": "0", "ui_display": "LAN2"}}}}}
    ports = settings_info.ports(info, "02:28:aa:6c:2e:64")
    assert [p["port"] for p in ports] == ["2.5G WAN", "LAN1", "LAN2"]
    assert ports[0]["below_max"] and not ports[1]["below_max"] and ports[2]["up"] is False


def test_uptime():
    assert P.parse_uptime("340861.20 123.4\n2.40 2.40 2.37 1/200 999\n") == {"uptime_s": 340861, "load": [2.4, 2.4, 2.37]}


def test_router_speedtest_history_and_run():
    line = json.dumps({"type": "result", "timestamp": "2026-10-22T11:47:39Z", "ping": {"jitter": 0.1, "latency": 9.26},
                       "download": {"bandwidth": 64_541_911}, "upload": {"bandwidth": 8_912_500}, "isp": "TestISP",
                       "server": {"name": "Test", "location": "Town"}})
    h = rst.parse_history(line + "\nnot json\n")
    assert len(h) == 1 and h[0]["down_mbps"] == 516.3 and h[0]["up_mbps"] == 71.3 and h[0]["idle_ms"] == 9.3
    t0 = 1_800_000_000
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    ev = [{"type": "ping", "timestamp": iso(t0)}] + [{"type": "download", "timestamp": iso(t0 + i)} for i in range(2, 12)] + \
         [{"type": "upload", "timestamp": iso(t0 + i)} for i in range(13, 23)] + \
         [{**json.loads(line), "timestamp": iso(t0 + 24)}]
    pings = [f"{t0 - 3 + i} time=9.0" for i in range(3)] + [f"{t0 + i} time=40.0" for i in range(2, 12)] + \
            [f"{t0 + i} time=260.0" for i in range(13, 23)] + [f"{t0 + 5}"]   # a lost ping (no time=)
    r = rst.parse_run("\n".join(json.dumps(e) for e in ev), "\n".join(pings))
    assert r["error"] is None and r["idle_ms"] == 9.0 and r["down_loaded_ms"] == 40.0 and r["up_loaded_ms"] == 260.0
    assert r["grade"] == "D"
    assert "no result" in rst.parse_run('{"type":"log","level":"error","message":"Timeout"}', "")["error"]
    sc = rst.run_script("1.1.1.1")
    assert "--accept-license" in sc and "rm -f /tmp/ml_ping.txt /tmp/ml_ookla.jsonl" in sc and "nvram" not in sc


def test_add_speed_tests_dedupes(tmp_home):
    from meshlens.store import db
    row = {"ts": 1000.2, "down_mbps": 500, "location": "router", "trigger": "router (ASUS app)"}
    assert db.add_speed_tests([row, {**row, "ts": 1000.6}]) == 1
    assert db.add_speed_tests([{**row, "location": "pc"}]) == 1          # same time, other place: kept
    assert {r["location"] for r in db.speed_tests(0)} == {"router", "pc"}


def test_new_issue_rules():
    now = 1_800_000_000.0
    acfg = config.AnalysisConfig()
    net = {"analysis_cfg": acfg, "unit_names": {"02:28:aa:6c:2e:64": "Main"},
           "host_lan": [{"ts": now - i * 60, "adapter": "Ethernet", "bcast_pps": 150, "mcast_pps": 60} for i in range(6)],
           "air": [{"ts": now - i * 120, "own_unit": "02:28:aa:6c:2e:64", "band": "2.4", "channel": 11, "util_pct": 83,
                    "signal_pct": 80, "bssid": "x", "ssid": "TestNet"} for i in range(3)]
                  + [{"ts": now, "own_unit": None, "band": "2.4", "channel": 11, "util_pct": None, "signal_pct": 60,
                      "bssid": f"0a:00:00:00:00:0{i}", "ssid": f"N{i}"} for i in range(3)],
           "speeds": [{"ts": now - 3600, "location": "router", "down_mbps": 516, "up_mbps": 72, "grade": None},
                      {"ts": now - 600, "location": "pc", "down_mbps": 51, "up_mbps": 29, "idle_ms": 14,
                       "down_loaded_ms": 35, "up_loaded_ms": 321, "grade": "D"}]}
    res = analyse([], [], [], UNITS, [], {}, now=now, net=net)
    by = {i["rule"]: i for i in res["issues"]}
    assert by["lan_flood"]["severity"] == "medium" and "210" in by["lan_flood"]["evidence"]
    assert by["channel_busy"]["severity"] == "high" and "Main 2.4 GHz channel 11 is 83% busy" == by["channel_busy"]["title"]
    assert "3 neighbouring networks" in by["crowded_channel"]["title"]
    assert "516/72" in by["pc_path_slow"]["evidence"] and "51/29" in by["pc_path_slow"]["evidence"]
    assert "from the MeshLens PC" in by["bufferbloat"]["evidence"]


def test_graph_series():
    lan = graphs.lan_noise([{"ts": 1, "adapter": "Eth", "bcast_pps": 5, "mcast_pps": 7}])
    assert lan == {"Broadcast (Eth)": [[1, 5]], "Multicast (Eth)": [[1, 7]]}
    air = graphs.air_util([{"ts": 1, "own_unit": "u", "band": "5", "channel": 56, "util_pct": 37},
                           {"ts": 1, "own_unit": "u", "band": "5", "channel": 56, "util_pct": 40},
                           {"ts": 1, "own_unit": None, "band": "5", "channel": 56, "util_pct": 90}], {"u": "Main"})
    assert air == {"Main 5 GHz ch 56": [[1, 40]]}
