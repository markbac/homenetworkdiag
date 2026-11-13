from meshlens import journeys


def ev(ts, kind, mac="m1", unit="u1", **detail):
    return {"ts": ts, "kind": kind, "mac": mac, "unit": unit, "iface": "ra1", "band": "2.4", "detail": detail}


def test_stability_counts_and_order():
    events = [ev(1, "connect"), ev(2, "kick"), ev(3, "disconnect"), ev(4, "roam_candidate", rssi=-74),
              ev(5, "roam_candidate", rssi=-71, mac="m2"), ev(6, "connect", mac="m2", unit="u2")]
    st = journeys.stability(events)
    assert st[0].mac == "m1" and st[0].kicks == 1 and st[0].disconnects == 1 and st[0].weakest_rssi == -74
    assert st[1].units == {"u1", "u2"}


def test_kick_loop_and_roam_storm():
    kicks = [ev(t, "kick", rssi=-72) for t in (0, 600, 1200, 9000)]
    roams = [ev(5000 + t, "roam_candidate", rssi=-73) for t in (0, 10, 20, 30, 40)]
    f = journeys.findings(kicks + roams)
    rules = sorted(x["rule"] for x in f)
    assert rules == ["kick_loop", "roam_storm"]
    loop = next(x for x in f if x["rule"] == "kick_loop")
    assert loop["count"] == 3 and loop["from"] == 0 and loop["to"] == 1200 and "-72 dBm" in loop["text"]
    storm = next(x for x in f if x["rule"] == "roam_storm")
    assert storm["count"] == 5


def test_flapping_threshold():
    assert journeys.findings([ev(t * 60, "disconnect") for t in range(5)]) == []
    assert journeys.findings([ev(t * 60, "disconnect") for t in range(6)])[0]["rule"] == "flapping"


def test_journey_gaps():
    j = journeys.journey([ev(100, "connect"), ev(400, "kick"), ev(430, "connect", unit="u2"), ev(50, "connect", mac="other")], "m1")
    assert [x["kind"] for x in j] == ["connect", "kick", "connect"]
    assert j[1]["connected_s"] == 300 and j[2]["offline_s"] == 30 and j[2]["unit"] == "u2"
