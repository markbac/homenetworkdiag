from datetime import datetime

from meshlens import config
from meshlens.analysis import analyse
from meshlens.collectors.asus import parsers as P


def test_rate_and_mode_capability():
    assert P.rate_capability("2340 vhtmcs 3 vhtnss 2") == {"wifi_gen": "Wi-Fi 5", "nss": 2, "mcs": 3}
    assert P.rate_capability("28820 hemcs 11 henss 2") == {"wifi_gen": "Wi-Fi 6", "nss": 2, "mcs": 11}
    assert P.rate_capability("57640 ehtmcs 13 ehtnss 2")["wifi_gen"] == "Wi-Fi 7"
    assert P.rate_capability("1300 mcs 15") == {"wifi_gen": "Wi-Fi 4", "nss": 2, "mcs": 7}
    assert P.rate_capability("6") == {}                       # a legacy rate says nothing about the device
    assert P.mode_capability("IEEE80211_MODE_11AXA_HE80") == {"wifi_gen": "Wi-Fi 6", "width": 80}
    assert P.mode_capability("IEEE80211_MODE_11NG_HT20") == {"wifi_gen": "Wi-Fi 4", "width": 20}
    assert P.mode_capability("IEEE80211_MODE_11BEA_EHT160")["wifi_gen"] == "Wi-Fi 7"
    sta = P.parse_hostapd_all_sta("02:aa:00:00:00:01\nsignal=-60\ntx_rate_info=2340 vhtmcs 3 vhtnss 2\nrx_rate_info=6\n", "rai1")
    assert sta[0]["wifi_gen"] == "Wi-Fi 5" and sta[0]["nss"] == 2
    q = P.parse_wlanconfig_list_sta(
        "ADDR AID CHAN TXRATE RXRATE RSSI MINRSSI MAXRSSI IDLE TXSEQ RXSEQ CAPS XCAPS ACAPS ERP STATE MAXRATE(DOT11) HTCAPS VHTCAPS ASSOCTIME IEs MODE RXNSS TXNSS PSMODE\n"
        "02:aa:00:00:00:02 1 104 1201M 960M -63 -69 -53 0 0 65535 EPR EBO NULL 0 b 1201000 AWPSM gGTRs 00:33:50 RSN WME IEEE80211_MODE_11AXA_HE80 2 2 0\n",
        "ath101")
    assert q[0]["wifi_gen"] == "Wi-Fi 6" and q[0]["nss"] == 2 and q[0]["width"] == 80


def test_conn_counts_dhcp_pool_and_warnings():
    c = P.parse_conn_counts("3 10.0.11.200\n69 10.0.11.63\nbad line\n41 10.0.10.54\n")
    assert [x["ip"] for x in c] == ["10.0.11.63", "10.0.10.54", "10.0.11.200"]
    pool = P.dhcp_pool({"dhcp_start": "10.0.10.2", "dhcp_end": "10.0.11.254"}, 60)
    assert pool["size"] == 509 and pool["pct"] == 11.8
    assert P.dhcp_pool({}, 5) is None
    ev = P.parse_log_events("Nov 14 03:00:00 dnsmasq-dhcp[123]: DHCPDISCOVER(br0) no address range available\n"
                            "Nov 14 03:01:00 kernel: nf_conntrack: table full, dropping packet\n", unit="u",
                            now=datetime(2026, 11, 14, 12))
    assert [e["detail"]["what"] for e in ev] == ["no address range available", "nf_conntrack: table full"]


def test_rules():
    now = 1_800_000_000.0
    router = {"mac": "02:00:00:00:01:64", "role": "router", "alias": "Main router", "reachable": 1, "macs": [],
              "info": {"dhcp": {"start": "10.0.10.2", "end": "10.0.10.101", "size": 100, "leases": 96, "pct": 96.0},
                       "conn_top": [{"ip": "10.0.11.5", "mac": "02:aa:00:00:00:05", "count": 4000}], "conn_total": 4500}}
    uev = [{"ts": now - 100, "unit": router["mac"], "kind": "router_warning", "mac": None, "iface": None,
            "detail": {"what": "no address range available"}}]
    net = {"analysis_cfg": config.AnalysisConfig(), "units": [router], "unit_names": {router["mac"]: "Main router"},
           "devices": [{"mac": "02:aa:00:00:00:05", "name": "NAS"}], "unit_events": uev}
    res = analyse([], [], [], [router], [], {}, now=now, net=net)
    r = {i["rule"]: i for i in res["issues"]}
    assert r["dhcp_pool_full"]["severity"] == "high" and "96 of 100" in r["dhcp_pool_full"]["evidence"]
    assert r["conn_heavy"]["title"] == "NAS has 4000 connections open" and r["conn_heavy"]["severity"] == "medium"
    assert r["router_warning"]["severity"] == "medium" and "exhausted" in r["router_warning"]["cause"]


def test_best_wifi_capability_is_kept(tmp_home):
    import time
    from meshlens.collectors.asus.collector import AsusCollector, UnitResult
    from meshlens.config import RouterConfig
    from meshlens.store import db
    mac = "02:aa:00:00:00:09"
    db.upsert_device(mac, time.time(), name="Phone")
    c = AsusCollector(RouterConfig(), tmp_home, runner=lambda *a: "")
    for gen, nss in (("Wi-Fi 6", 2), ("Wi-Fi 4", 1)):        # idle later: an older mode on the link
        r = UnitResult(mac="02:00:00:00:01:64", ip="10.0.10.1", main=True, ok=True)
        r.stations = [{"mac": mac, "wifi_gen": gen, "nss": nss}]
        c.store([r], [], time.time(), False)
    d = db.device(mac)
    assert d["wifi_gen"] == "Wi-Fi 6" and d["wifi_nss"] == 2
