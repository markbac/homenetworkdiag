from datetime import datetime

from meshlens.collectors.asus import parsers as P
from meshlens.collectors.asus.collector import parse_unit


def test_sections_identity_and_chip(fixture_text):
    s = P.split_sections(fixture_text("asus/router_mediatek.txt"))
    ident = P.parse_nvram_kv(s["identity"])
    assert ident["productid"] == "BT8" and ident["wl_ifnames"] == "ra0 rai0 rax0"
    assert P.chip_family(P.which_tools(s["tools"])) == "mediatek"
    q = P.split_sections(fixture_text("asus/node_qualcomm.txt"))
    assert P.chip_family(P.which_tools(q["tools"])) == "qualcomm"


def test_band_of_iface():
    assert P.band_of_iface("ra1") == "2.4"
    assert P.band_of_iface("rai2") == "5"
    assert P.band_of_iface("rax0") == "6"
    assert P.band_of_iface("ath001") == "2.4"
    assert P.band_of_iface("ath101") == "5"
    assert P.band_of_iface("eth0") is None


def test_cfg_device_list():
    units = P.parse_cfg_device_list("<ZenWiFi_BT8>10.0.10.1>02:00:00:00:01:64>1<ZenWiFi_BD4>10.0.11.14>02:00:00:00:02:44>0")
    assert units == [
        {"model": "ZenWiFi_BT8", "ip": "10.0.10.1", "mac": "02:00:00:00:01:64", "main": True},
        {"model": "ZenWiFi_BD4", "ip": "10.0.11.14", "mac": "02:00:00:00:02:44", "main": False},
    ]


def test_clientlist_bands_and_wired(fixture_text):
    s = P.split_sections(fixture_text("asus/router_mediatek.txt"))
    clients = {c["mac"]: c for c in P.parse_clientlist(s["clientlist"])}
    assert clients["02:aa:00:00:00:02"]["band"] == "2.4" and clients["02:aa:00:00:00:02"]["rssi"] == -47
    assert clients["0a:00:00:00:00:01"]["band"] == "5" and clients["0a:00:00:00:00:01"]["unit"] == "02:00:00:00:01:60"
    assert clients["02:bb:00:00:00:01"]["wired"] is True and clients["02:bb:00:00:00:01"]["band"] is None
    assert clients["02:aa:00:00:00:09"]["ip"] is None


def test_hostapd_all_sta(fixture_text):
    s = P.split_sections(fixture_text("asus/router_mediatek.txt"))
    sta = P.parse_hostapd_all_sta(s["sta_hostapd:ra1"], "ra1")
    assert [x["mac"] for x in sta] == ["02:aa:00:00:00:02", "02:aa:00:00:00:03"]
    assert sta[0]["rssi"] == -47 and sta[0]["rx_rate"] == 72.2 and sta[0]["tx_rate"] == 28.9
    assert sta[0]["connected_time"] == 11940 and sta[0]["band"] == "2.4"
    assert sta[1]["tx_rate"] == 78.0 and sta[1]["inactive_msec"] == 26000


def test_wlanconfig_list_sta(fixture_text):
    s = P.split_sections(fixture_text("asus/node_qualcomm.txt"))
    sta = P.parse_wlanconfig_list_sta(s["sta_wlanconfig:ath001"], "ath001")
    assert sta == [{"mac": "02:aa:00:00:00:05", "iface": "ath001", "band": "2.4", "channel": 11, "tx_rate": 72.0,
                    "rx_rate": 58.0, "rssi": -50, "rssi_min": -58, "rssi_max": -44, "idle_s": 17, "assoc_time": "08:11:46"}]


def test_mwctl_stat(fixture_text):
    s = P.split_sections(fixture_text("asus/router_mediatek.txt"))
    st = P.parse_mwctl_stat(s["mwctl_stat:ra0"])
    assert st["temperature"] == 53 and st["tx_fail_pct"] == 15.7 and st["rx_crc_pct"] == 22.6
    assert st["proxy_arp"] is False and st["bss_transition"] is True


def test_iwconfig_noise(fixture_text):
    s = P.split_sections(fixture_text("asus/node_qualcomm.txt"))
    ifs = {i["iface"]: i for i in P.parse_iwconfig(s["iwconfig"])}
    assert ifs["ath0"]["noise"] == -94 and ifs["ath1"]["signal"] == -65 and ifs["ath0"]["bitrate_mbps"] == 574
    assert "lo" not in ifs


def test_iw_dev(fixture_text):
    s = P.split_sections(fixture_text("asus/router_mediatek.txt"))
    ifs = {i["iface"]: i for i in P.parse_iw_dev(s["iw_dev"])}
    assert ifs["rax0"]["width"] == 320 and ifs["rax0"]["channel"] == 85 and ifs["rax0"]["band"] == "6"
    assert ifs["ra1"]["ssid"] == "TestNet" and ifs["ra1"]["txpower_dbm"] == 23.0


def test_log_events_mediatek(fixture_text):
    s = P.split_sections(fixture_text("asus/router_mediatek.txt"))
    ev = P.parse_log_events(s["log"], unit="u1", now=datetime(2026, 11, 13, 16, 0))
    kinds = [e["kind"] for e in ev]
    assert kinds == ["roam_candidate", "roam", "connect", "moved_away", "kick", "disconnect"]
    cand = ev[0]
    assert cand["mac"] == "0a:00:00:00:00:01" and cand["detail"] == {"target": "0e:00:00:00:02:45", "target_rssi": -60, "rssi": -71}
    assert ev[3]["detail"]["new_unit"] == "02:00:00:00:01:60"
    assert ev[4]["iface"] == "rai1" and ev[4]["band"] == "5"
    assert len({e["raw_hash"] for e in ev}) == len(ev)


def test_log_events_qualcomm(fixture_text):
    s = P.split_sections(fixture_text("asus/node_qualcomm.txt"))
    ev = {e["kind"]: e for e in P.parse_log_events(s["log"], unit="u2", now=datetime(2026, 11, 13))}
    assert ev["leave"]["detail"]["rssi"] == -46 and ev["leave"]["iface"] == "ath001"
    assert ev["assoc_failed"]["detail"]["status"] == 30
    assert ev["disconnect"]["detail"]["initiator"] == "ap" and ev["disconnect"]["iface"] is None
    assert ev["kick"]["band"] == "2.4"


def test_log_year_rollover():
    ev = P.parse_log_events("Dec 31 23:59:00 hapdevent: hapdevent_proc_event(347): ra1: Assoc 02:aa:00:00:00:02",
                            unit="u", now=datetime(2027, 1, 1, 0, 5))
    assert datetime.fromtimestamp(ev[0]["ts"]).year == 2026


def test_randomised():
    assert P.is_randomised("0a:00:00:00:00:01") and P.is_randomised("3a:99:7f:fd:a8:ed")
    assert not P.is_randomised("00:1c:c2:ba:0a:d3")


def test_parse_unit_qualcomm(fixture_text):
    r = parse_unit(fixture_text("asus/node_qualcomm.txt"), "02:00:00:00:02:44", "10.0.11.14", False)
    assert r.chip == "qualcomm" and len(r.stations) == 1
    assert {x["iface"] for x in r.radios} == {"ath0", "ath1"}
    assert r.counters["ath0"]["tx_bytes"] == 200


def test_file_without_trailing_newline_does_not_swallow_next_section():
    # Real /tmp/clientlist.json has no final newline, so the next marker lands on the same line.
    text = ('@@@ clientlist\n{"02:00:00:00:01:64":{"2G_1":{"02:AA:00:00:00:02":{"ip":"10.0.11.6","rssi":"-58"}},'
            '"wired_mac":{"02:BB:00:00:00:01":{"ip":"10.0.10.203"}}}}@@@ allwclientlist\n{}\n@@@ relist\n{}\n')
    s = P.split_sections(text)
    assert set(s) == {"clientlist", "allwclientlist", "relist"}
    cl = {c["mac"]: c for c in P.parse_clientlist(s["clientlist"])}
    assert cl["02:aa:00:00:00:02"]["band"] == "2.4" and cl["02:bb:00:00:00:01"]["wired"] is True
    # A marker-like string inside data is left alone.
    assert P.split_sections('@@@ log\nsaw @@@ in text here\n')["log"] == "saw @@@ in text here"


def test_unit_script_uses_which_not_command_v():
    from meshlens.collectors.asus.commands import unit_script
    sc = unit_script(True, False, True)
    assert "command -v" not in sc            # BusyBox on ASUS firmware has no "command" builtin
    assert "if which mwctl" in sc and "if which hostapd_cli" in sc and "if which wlanconfig" in sc
    assert "clientlist.json 2>/dev/null; echo" in sc and 'cat "$f"; echo;' in sc


def test_allwclientlist_mld_link():
    al = P.parse_allwclientlist('{"02:00:00:00:01:64":{"6G_1":{"0A:00:00:00:00:01":{"ifname":"rax1","wifi_auth":"WPA3-SAE",'
                                '"mld_mac":"0A:00:00:00:00:00","mlo_active":0},"02:AA:00:00:00:03":{"mld_mac":"00:00:00:00:00:00"}}}}')
    assert al["0a:00:00:00:00:01"]["mld"] == "0a:00:00:00:00:00" and al["02:aa:00:00:00:03"]["mld"] is None
