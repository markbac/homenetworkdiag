"""Dual-band MediaTek nodes (RT-AX57 Go), channel planner, radar incidents, loops, stuck-on-2.4, failed joins.

All addresses and names are invented (02:00:... locally administered MACs, 10.0.x.x, made-up SSIDs).
"""
from meshlens import channels
from meshlens.analysis import analyse
from meshlens.analysis_net import net_issues
from meshlens.collectors.asus import parsers as P
from meshlens.collectors.asus.collector import parse_unit
from meshlens.collectors.devolo import loop_evidence
from meshlens.config import AnalysisConfig

ROUTER, NODE, BRIDGE, DETECTOR = "02:00:00:00:00:64", "02:00:00:00:00:48", "02:00:00:00:00:da", "02:00:00:00:00:44"
NOW = 1_800_000_000.0


# ---------------------------------------------------------------- bands and chip on dual-band MediaTek

def test_band_map_tri_band_vs_dual_band():
    tri = P.iface_band_map({"wl_ifnames": "ra0 rai0 rax0"})
    dual = P.iface_band_map({"wl_ifnames": "ra0 rax0"})
    assert P.band_of_iface("rax1", tri) == "6" and P.band_of_iface("apclix0", tri) == "6"
    assert P.band_of_iface("rax1", dual) == "5" and P.band_of_iface("apclix0", dual) == "5"
    assert P.band_of_iface("apcli0", dual) == "2.4" and P.band_of_iface("ra2", dual) == "2.4"
    assert P.band_of_iface("rax0") == "6"                      # no map: old default kept
    assert P.band_of_iface("ath101", dual) == "5"              # Qualcomm names unaffected


def test_band_from_frequency_wins():
    assert P.band_of_freq(2452) == "2.4" and P.band_of_freq(5240) == "5" and P.band_of_freq(6295) == "6"
    iw = P.parse_iw_dev("phy#1\n\tInterface rax0\n\t\ttype AP\n\t\tchannel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz\n")
    assert iw[0]["band"] == "5"                                # name says 6, frequency says 5
    iwc = P.parse_iwconfig('rax1      ESSID:"Home-Net"\n          Mode:Master  Frequency:5.18 GHz  Bit Rate=1201 Mb/s\n')
    assert iwc[0]["band"] == "5" and iwc[0]["freq"] == 5180 and iwc[0]["bitrate_mbps"] == 1201


def test_chip_family_falls_back_to_interface_names():
    assert P.chip_family({"iwpriv", "iwconfig"}, "ra0 rax0") == "mediatek"
    assert P.chip_family(set(), "ath0 ath1") == "qualcomm"
    assert P.chip_family({"mwctl"}, "") == "mediatek"
    assert P.chip_family(set(), "") == "unknown"


MAC_TABLE = """
HT Operating Mode : 0

MAC                AID BSS PSM WMM MIMOPS RSSI0 RSSI1 RSSI2 RSSI3 PhMd  BW   MCS SGI STBC Idle Rate
02:00:00:00:10:01  1   1   0   1   0      -52   -55   0     0     HE    80M  9   1   0    3    1201
02:00:00:00:10:02  2   1   1   1   0      -71   -68   0     0     HE    20M  4   0   0    40   86
"""


def test_mac_table_parser_reads_columns_by_header():
    st = P.parse_mac_table(MAC_TABLE, "rax0", {"ra": "2.4", "rax": "5"})
    assert [s["mac"] for s in st] == ["02:00:00:00:10:01", "02:00:00:00:10:02"]
    assert st[0]["rssi"] == -52 and st[1]["rssi"] == -68 and st[0]["band"] == "5"
    assert st[0]["tx_rate"] == 1201 and st[1]["inactive_msec"] == 40000 and st[0]["width"] == 80
    assert P.parse_mac_table("nothing useful here\n", "ra0") == []


def test_parse_unit_dual_band_node_without_iw():
    text = "\n".join([
        "@@@ identity", "productid=EXAMPLE-AX", "wl_ifnames=ra0 rax0", "sta_ifnames=apcli0 apclix0",
        "@@@ tools", "wl: MISSING", "iw: MISSING", "iwpriv: /usr/sbin/iwpriv", "iwconfig: /usr/sbin/iwconfig",
        "mwctl: MISSING", "hostapd_cli: MISSING", "wlanconfig: MISSING",
        "@@@ iwconfig",
        'ra1       ESSID:"Home-Net"', "          Bit Rate=286 Mb/s",
        'rax1      ESSID:"Home-Net"', "          Bit Rate=1201 Mb/s",
        'apclix0   ESSID:"0123456789ABCDEF0123456789ABCDEF"', "          Bit Rate=1201 Mb/s",
        "@@@ mactable:rax0", *MAC_TABLE.strip().splitlines(),
        "@@@ iwpriv_stat:rax0", "Tx success = 100",
    ])
    r = parse_unit(text, BRIDGE, "10.0.0.140", False)
    assert r.chip == "mediatek" and r.bands["rax"] == "5"
    assert {i["iface"]: i["band"] for i in r.info["ifaces"]} == {"ra1": "2.4", "rax1": "5", "apclix0": "5"}
    assert next(i for i in r.info["ifaces"] if i["iface"] == "apclix0")["type"] == "managed"
    assert len(r.stations) == 2 and r.stations[0]["band"] == "5"
    assert "iwpriv_stat:rax0" in r.raw_unparsed and "mactable:rax0" not in r.raw_unparsed


# ---------------------------------------------------------------- channel planner and radar

def _air(bssid, ch, band, rssi, own=None, util=None, ssid="Neighbour"):
    return {"ts": NOW - 60, "bssid": bssid, "ssid": ssid, "band": band, "channel": ch, "rssi": rssi,
            "util_pct": util, "own_unit": own, "signal_pct": 80}


UNITS = [{"mac": ROUTER, "role": "router", "alias": "Main router", "macs": [ROUTER, "02:00:00:00:00:65"],
          "info": {"ifaces": [{"iface": "rai0", "band": "5", "type": "AP", "channel": 100, "width": 80},
                              {"iface": "ra0", "band": "2.4", "type": "AP", "channel": 9, "width": 20}]},
          "backhaul": []},
         {"mac": DETECTOR, "role": "node", "alias": "Upstairs", "macs": [DETECTOR], "info": {}, "backhaul": []}]
RADAR = [{"ts": NOW - 3600, "unit": DETECTOR, "kind": "radar", "mac": None, "iface": None, "detail": {"channel": 106}},
         {"ts": NOW - 3590, "unit": ROUTER, "kind": "channel_replan", "mac": None, "iface": "rai0",
          "detail": {"channel": 100, "bw": 80, "unavailable": "100,104,108,112"}},
         {"ts": NOW - 3500, "unit": DETECTOR, "kind": "radar", "mac": None, "iface": None, "detail": {"channel": 58}}]


def test_plan_prefers_non_dfs_block_after_radar_and_fixes_overlapping_24():
    air = [_air("02:aa:00:00:00:01", 100, "5", -60), _air("02:aa:00:00:00:02", 104, "5", -65),
           _air("02:aa:00:00:00:03", 36, "5", -82), _air("02:aa:00:00:00:04", 6, "2.4", -55),
           _air("02:aa:00:00:00:05", 11, "2.4", -60), _air("02:00:00:00:00:66", 100, "5", -40, own=ROUTER, util=40)]
    p = channels.plan(air, UNITS, RADAR, NOW)
    five = {f["block"]: f for f in p["five"]}
    assert five["100-112"]["current"] and five["100-112"]["radar_hits"] == 1 and five["100-112"]["strong_neighbours"] == 2
    assert five["52-64"]["radar_hits"] == 1
    assert p["recommend"]["5"]["block"] == "36-48" and p["recommend"]["5"]["change"]
    assert "radar" in p["recommend"]["5"]["text"]
    r24 = p["recommend"]["2.4"]
    assert r24["change"] and r24["channel"] == 1 and "overlaps channels 6 and 11" in r24["text"]


def test_radar_incidents_count_drops_and_stuck_devices():
    tv, phone = "02:00:00:00:20:01", "02:00:00:00:20:02"
    drops = [{"ts": NOW - 3598, "mac": tv, "kind": "disconnect"}, {"ts": NOW - 3597, "mac": phone, "kind": "disconnect"},
             {"ts": NOW - 100, "mac": phone, "kind": "disconnect"}]
    t0 = NOW - 3600
    samples = [{"ts": t0 - 60, "mac": tv, "band": "5"}, {"ts": t0 + 30, "mac": tv, "band": "2.4"},
               {"ts": t0 - 60, "mac": phone, "band": "5"}, {"ts": t0 + 30, "mac": phone, "band": "5"},
               {"ts": t0 - 60, "mac": "02:00:00:00:00:65", "band": "5"}, {"ts": t0 + 30, "mac": "02:00:00:00:00:65", "band": "2.4"}]
    moves = {t0: channels.band_moves_after(t0, samples, {tv: {"band": "2.4"}}, {"02:00:00:00:00:65"}, {tv: "Lounge TV"})}
    r = channels.radar_incidents(RADAR, drops, moves, {DETECTOR: "Upstairs", ROUTER: "Main router"})
    assert r["count"] == 2 and len(r["incidents"]) == 1          # 100 s apart: one incident
    inc = r["incidents"][0]
    assert inc["detected_by"] == ["Upstairs"] and inc["channels"] == [58, 106] and inc["blocks"] == ["100-112", "52-64"]
    assert inc["devices_dropped"] == 2 and inc["unavailable"] == [100, 104, 108, 112]
    assert [m["name"] for m in inc["still_on_24"]] == ["Lounge TV"]
    assert r["single_detector"] == "Upstairs"


# ---------------------------------------------------------------- loops behind powerline

def test_loop_evidence():
    units = [{"mac": ROUTER, "role": "router", "macs": [ROUTER, "02:00:00:00:00:65"], "alias": "Main router"},
             {"mac": BRIDGE, "role": "node", "macs": [BRIDGE], "alias": "Study bridge",
              "backhaul": [{"parent": ROUTER, "band": "5", "rssi": -57, "ts": NOW - 60}]}]
    adapters = [{"mac": "02:dd:00:00:00:01", "name": "Hall plug", "attached_to_router": True, "bridged": ["02:00:00:00:00:65"]},
                {"mac": "02:dd:00:00:00:02", "name": "Study plug", "attached_to_router": False,
                 "bridged": ["02:00:00:00:30:01", BRIDGE, "02:00:00:00:00:65", "02:00:00:00:40:01", "02:00:00:00:40:02"]}]
    wifi = {"02:00:00:00:40:01": ROUTER, "02:00:00:00:40:02": BRIDGE}   # 2nd is the bridge's own client: legitimate
    f = loop_evidence(adapters, wifi, units, NOW)
    assert len(f) == 1 and f[0]["adapter_name"] == "Study plug"
    assert f[0]["router_macs"] == ["02:00:00:00:00:65"] and f[0]["wifi_clients"] == ["02:00:00:00:40:01"]
    assert f[0]["nodes"][0]["name"] == "Study bridge"
    clean = [{**adapters[1], "bridged": ["02:00:00:00:30:01"]}]
    assert loop_evidence(clean, wifi, units, NOW) == []


def test_net_issues_loop_stuck_plan_and_names():
    loop = {"ts": NOW - 600, "unit": "02:dd:00:00:00:02", "kind": "l2_loop", "mac": None, "iface": None,
            "detail": {"adapter": "02:dd:00:00:00:02", "adapter_name": "Study plug", "router_macs": ["02:00:00:00:00:65"],
                       "wifi_clients": ["02:00:00:00:40:01"], "wifi_client_units": ["Main router"], "nodes": []}}
    pings = [{"ts": NOW - 600, "target": "10.0.0.1", "label": "Router", "kind": "router", "sent": 30, "lost": 9},
             {"ts": NOW - 7200, "target": "10.0.0.1", "label": "Router", "kind": "router", "sent": 30, "lost": 0}]
    tv = "02:00:00:00:20:01"
    plan = channels.plan([], UNITS, RADAR, NOW)
    plan["radar"] = channels.radar_incidents(RADAR, [], {}, {DETECTOR: "Upstairs"})
    net = {"unit_events": RADAR + [loop], "pings": pings, "analysis_cfg": AnalysisConfig(), "channel_plan": plan,
           "units": UNITS + [{"mac": "02:00:00:00:00:77", "role": "node", "alias": "Study", "macs": []},
                             {"mac": "02:00:00:00:00:78", "role": "node", "alias": "study", "macs": []}],
           "unit_names": {ROUTER: "Main router", DETECTOR: "Upstairs"},
           "band_history": {tv: {"hi": 300, "lo": 50, "last_hi": NOW - 3620, "last_lo": NOW - 30, "hi_rssi": -64}},
           "latest_stations": [{"ts": NOW - 30, "mac": tv, "unit": ROUTER, "band": "2.4", "rssi": -63}]}
    out = {i["rule"]: i for i in net_issues(net, {tv: {"mac": tv, "name": "Lounge TV"}}, NOW)}
    assert out["network_loop"]["severity"] == "high" and "30.0% around those times vs 0.0%" in out["network_loop"]["evidence"]
    assert "Lounge TV" in out["stuck_on_24"]["title"] and "radar" in out["stuck_on_24"]["evidence"]
    assert out["channel_plan"]["severity"] == "medium" and "36-48" in " ".join(out["channel_plan"]["fixes"])
    assert out["radar_dfs"]["count"] == 2 and "false trigger" in out["radar_dfs"]["cause"]
    assert out["duplicate_unit_name"]["title"].startswith("2 mesh units")


# ---------------------------------------------------------------- failed joins paired by time

def test_join_failures_paired_with_leaving_device():
    speaker, unit = "02:00:00:00:50:01", NODE
    ev = []
    for k in range(12):
        t = NOW - 3000 + k * 120
        ev.append({"ts": t, "unit": unit, "kind": "assoc_failed", "mac": None, "iface": "ath101", "detail": {"status": 53}})
        ev.append({"ts": t + 0.4, "unit": unit, "kind": "leave", "mac": speaker, "iface": "ath101", "detail": {"rssi": -57}})
    ev.append({"ts": NOW - 50, "unit": unit, "kind": "assoc_failed", "mac": None, "iface": "ath101", "detail": {"status": 30}})
    res = analyse(ev, [], [], [{"mac": unit, "alias": "Box room", "role": "node"}], [{"mac": speaker, "name": "Kitchen speaker"}],
                  {}, AnalysisConfig(), NOW)
    j = [i for i in res["issues"] if i["rule"] == "join_failures"]
    assert len(j) == 1 and j[0]["subject"] == speaker and j[0]["count"] == 12 and j[0]["status"] == 53
    assert "invalid PMKID" in j[0]["evidence"] and "Box room" in j[0]["title"]


# ---------------------------------------------------------------- hiding issues

def test_hidden_issues_marked_and_left_out_of_counts():
    plug, unit = "02:00:00:00:60:01", NODE
    ev = [{"ts": NOW - 3000 + k * 60, "unit": unit, "kind": "disconnect", "mac": plug, "iface": None, "detail": {}} for k in range(8)]
    args = (ev, [], [], [{"mac": unit, "alias": "Hall", "role": "node"}], [{"mac": plug, "name": "Plug"}], {}, AnalysisConfig(), NOW)
    shown = analyse(*args)
    flap = [i for i in shown["issues"] if i["rule"] == "flapping"]
    assert flap and not flap[0]["hidden"] and shown["network"]["issue_counts"]["medium"] >= 1
    hid = analyse(*args, hidden=[("flapping", plug)])
    flap = [i for i in hid["issues"] if i["rule"] == "flapping"]
    assert flap and flap[0]["hidden"]                                   # still returned, for "show hidden"
    assert hid["network"]["issue_counts"]["medium"] == shown["network"]["issue_counts"]["medium"] - len(flap)
    assert hid["network"]["hidden"] == len(flap)


def test_hidden_issue_store(tmp_home):
    from meshlens.store import db
    db.set_issue_hidden("flapping", "02:00:00:00:60:01", True, "Plug keeps disconnecting")
    assert [(h["rule"], h["subject"]) for h in db.hidden_issues()] == [("flapping", "02:00:00:00:60:01")]
    db.set_issue_hidden("flapping", "02:00:00:00:60:01", False)
    assert db.hidden_issues() == []


# ---------------------------------------------------------------- more network rules

def _net(**kw):
    return {"analysis_cfg": AnalysisConfig(), "unit_names": {ROUTER: "Main router", NODE: "Box room", DETECTOR: "Upstairs",
                                                              BRIDGE: "Study"}, **kw}


def test_mesh_restart_grouped_and_not_repeated_per_unit():
    us = []
    for u, at in ((ROUTER, 0), (NODE, 600), (DETECTOR, 1200)):
        night = 1_799_982_000.0   # a night-time base; restart shows as uptime dropping
        us += [{"ts": night - 300 + at, "unit": u, "uptime_s": 90000}, {"ts": night + at, "unit": u, "uptime_s": 60}]
    out = net_issues(_net(unit_samples=us), {}, NOW)
    rules = [i["rule"] for i in out]
    assert rules.count("mesh_restart") == 1 and "unit_restarted" not in rules


def test_speed_path_change_explains_local_bufferbloat():
    sp = [{"ts": NOW - 86400 * 2 + k * 3600, "down_mbps": 95, "up_mbps": 40, "idle_ms": 16, "down_loaded_ms": 30,
           "up_loaded_ms": 300, "grade": "D", "location": "pc"} for k in range(6)]
    sp.append({"ts": NOW - 600, "down_mbps": 390, "up_mbps": 95, "idle_ms": 8, "down_loaded_ms": 19, "up_loaded_ms": 23,
               "grade": "A", "location": "pc"})
    out = {i["rule"]: i for i in net_issues(_net(speeds=sp), {}, NOW)}
    i = out["speed_path_changed"]
    assert i["severity"] == "info" and "improved" in i["title"] and "old path" in i["cause"] and "Only one test" in i["evidence"]


def test_dual_homed_conflict_pmf_firmware_and_names():
    devs = [{"mac": "02:00:00:00:70:01", "ip": "10.0.0.53", "name": "dns-pi", "wired": 1, "last_seen": NOW - 60},
            {"mac": "02:00:00:00:70:02", "ip": "10.0.0.53", "name": "dns-pi", "wired": 0, "unit": NODE, "last_seen": NOW - 60},
            {"mac": "02:00:00:00:70:03", "ip": "10.0.0.77", "name": "printer", "wired": 0, "last_seen": NOW - 60},
            {"mac": "02:00:00:00:70:04", "ip": "10.0.0.77", "name": "camera", "wired": 0, "last_seen": NOW - 60},
            {"mac": "02:00:00:00:70:05", "ip": "10.0.0.80", "name": "ZenWiFi_BD4", "name_src": "asus:AsusOui",
             "vendor": "Espressif Inc.", "last_seen": NOW - 60}]
    sets = {ROUTER: {"wl0_mfp": "1", "wl1_mfp": "1"}, NODE: {"wl0_mfp": "0", "wl1_mfp": "0"}}
    units = [{"mac": ROUTER, "role": "router", "model": "BT8", "firmware": "3.0.0.6.102_58601-gabc"},
             {"mac": NODE, "role": "node", "model": "BD4", "firmware": "3.0.0.6.102_58396-gdef"}]
    out = {i["rule"]: i for i in net_issues(_net(devices=devs, settings=sets, units=units), {}, NOW)}
    assert out["dual_homed"]["severity"] == "high" and "dns-pi" in out["dual_homed"]["title"]
    assert "ip_conflict" not in out          # differently named devices: a stored address may be stale
    assert "Main router: capable" in out["pmf_mismatch"]["evidence"] and "Box room: off" in out["pmf_mismatch"]["evidence"]
    assert "build 58396" in out["firmware_mismatch"]["evidence"] and "Box room" in out["firmware_mismatch"]["fixes"][0]
    assert out["suspect_name"]["subject"] == "02:00:00:00:70:05"


def test_outdoor_wander():
    garden, tv = "02:00:00:00:00:77", "02:00:00:00:80:01"
    units = [{"mac": ROUTER, "alias": "Main router", "role": "router", "model": "BT8"},
             {"mac": garden, "alias": "Garden", "role": "node", "model": "ExampleMesh_Outdoor"}]
    smp = [{"ts": NOW - 4000 + k * 60, "unit": garden if k % 4 == 0 else ROUTER, "mac": tv, "band": "5",
            "rssi": -76 if k % 4 == 0 else -62, "tx_rate": 200} for k in range(40)]
    res = analyse([], smp, [], units, [{"mac": tv, "name": "Lounge TV"}], {}, AnalysisConfig(), NOW)
    w = [i for i in res["issues"] if i["rule"] == "outdoor_wander"]
    assert len(w) == 1 and "Garden" in w[0]["title"] and "25%" in w[0]["evidence"]
