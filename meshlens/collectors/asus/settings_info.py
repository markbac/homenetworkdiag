"""Plain-English view of the ASUS settings MeshLens reads (read-only; nothing here changes the router)."""
from __future__ import annotations

from typing import Any

BANDS = {"wl0": "2.4 GHz", "wl1": "5 GHz", "wl2": "5 GHz-2 / 6 GHz", "wl3": "6 GHz"}
ON_OFF = {"0": "off", "1": "on"}

# key suffix -> (label, value decoder, what it means)
BAND_KEYS: dict[str, tuple[str, Any, str]] = {
    "ssid": ("Network name", None, "The main network name on this band."),
    "closed": ("Hidden", {"0": "no", "1": "yes (hidden)"}, "Hidden networks don't add security and can confuse roaming."),
    "bss_enabled": ("Enabled", {"0": "off", "1": "on"}, ""),
    "chanspec": ("Channel setting", {"0": "Auto"}, "Auto lets the router pick; a fixed channel avoids sudden changes."),
    "channel": ("Channel", {"0": "Auto"}, ""),
    "bw": ("Channel width setting", None,
           "Raw ASUS value (its meaning varies by firmware); the width actually in use is in the Radios table."),
    "txpower": ("Transmit power", None, "Percent or level of maximum. Lowering it can help devices roam to closer units."),
    "user_rssi": ("Roaming assistant threshold", {"0": "off"},
                  "Devices weaker than this are disconnected so they reconnect to a closer unit. About -70 to -75 dBm suits most homes."),
    "11ax": ("Wi-Fi 6 (802.11ax)", ON_OFF, ""),
    "11be": ("Wi-Fi 7 (802.11be)", ON_OFF, ""),
    "mfp": ("Protected management frames", {"0": "off", "1": "capable", "2": "required"},
            "Required blocks older devices; capable is the safe middle ground."),
    "igs": ("Wireless IGMP snooping", ON_OFF, "Sends multicast only to the devices that asked for it."),
    "mrate_x": ("Multicast rate", {"0": "Auto"}, "Low multicast rates use a lot of airtime."),
    "rateset": ("Rate set", None, ""),
    "ifname": ("Interface", None, ""),
}

NETWORK_KEYS: list[tuple[str, str, Any, str]] = [
    ("smart_connect_x", "Smart Connect", {"0": "off", "1": "on (one name, router picks the band)", "2": "on (5 GHz only)"},
     "Lets the router steer devices between bands."),
    ("emf_enable", "IGMP snooping (LAN)", ON_OFF, "Off means multicast (casting, IPTV, smart-home discovery) reaches every device."),
    ("mr_enable_x", "Multicast routing (IPTV)", ON_OFF, ""),
    ("upnp_enable", "UPnP", ON_OFF, "Lets devices open ports on the router by themselves (consoles, calls)."),
    ("vts_enable_x", "Port forwarding", ON_OFF, ""),
    ("dmz_ip", "DMZ", {"": "off"}, "A device here gets every incoming internet connection."),
    ("dhcp_start", "DHCP pool start", None, ""),
    ("dhcp_end", "DHCP pool end", None, ""),
    ("dhcp_lease", "DHCP lease time (s)", None, "How long a device keeps its address."),
    ("wan0_proto", "Internet connection type", None, ""),
    ("wan0_state_t", "Internet connection state", {"0": "initialising", "1": "connecting", "2": "connected", "3": "disconnected", "4": "stopped"}, ""),
    ("wan0_ipaddr", "Internet (WAN) address", None, "100.64.x.x-100.127.x.x means carrier-grade NAT: port forwarding won't work."),
]
ADMIN_KEYS: list[tuple[str, str, Any, str]] = [
    ("wps_enable", "WPS", ON_OFF, "Rarely needed and a known weak point."),
    ("misc_http_x", "Web admin from the internet", ON_OFF, "Should be off."),
    ("sshd_enable", "SSH", {"0": "off", "1": "LAN only", "2": "LAN and internet"}, "MeshLens needs LAN only."),
    ("sshd_port", "SSH port", None, ""),
    ("time_zone_x", "Time zone", None, ""),
]


def _decode(raw: str | None, dec: Any) -> str:
    if raw is None:
        return "–"
    if isinstance(dec, dict) and raw in dec:
        return dec[raw]
    return raw or "–"


def describe(settings: dict[str, str]) -> dict[str, Any]:
    """Grouped settings: a band table, extra networks (guest/IoT), network, admin; plus everything raw."""
    bands = [b for b in BANDS if any(k.startswith(b + "_") for k in settings)]
    band_rows = []
    for suffix, (label, dec, meaning) in BAND_KEYS.items():
        vals = {b: settings.get(f"{b}_{suffix}") for b in bands}
        if all(v is None for v in vals.values()):
            continue
        band_rows.append({"key": suffix, "label": label, "meaning": meaning,
                          "values": {BANDS[b]: _decode(v, dec) for b, v in vals.items()}})
    extra = []
    for b in bands:
        for g in (1, 2, 3):
            ssid = settings.get(f"{b}.{g}_ssid")
            if ssid:
                extra.append({"band": BANDS[b], "ssid": ssid, "slot": f"{b}.{g}",
                              "enabled": settings.get(f"{b}.{g}_bss_enabled") == "1",
                              "hidden": settings.get(f"{b}.{g}_closed") == "1", "iface": settings.get(f"{b}.{g}_ifname")})

    def simple(keys):
        return [{"key": k, "label": label, "value": _decode(settings.get(k), dec), "raw": settings.get(k), "meaning": meaning}
                for k, label, dec, meaning in keys if k in settings]

    return {"bands": [BANDS[b] for b in bands], "band_rows": band_rows, "networks": extra,
            "network": simple(NETWORK_KEYS), "admin": simple(ADMIN_KEYS), "raw": dict(sorted(settings.items()))}


def ports(info: dict[str, Any] | None, unit_mac: str) -> list[dict[str, Any]]:
    """Port table for one unit from the router's diag_port_status.json (it covers every unit)."""
    pi = ((info or {}).get("ports") or {}).get("port_info") or {}
    mine = pi.get(unit_mac.upper()) or pi.get(unit_mac.lower()) or {}
    out = []
    for pid, p in mine.items():
        on = str(p.get("is_on")) == "1"
        mx, lr = int(p.get("max_rate") or 0), int(p.get("link_rate") or 0)
        kind = {"W": "WAN", "L": "LAN", "U": "USB"}.get(pid[:1], pid[:1])
        out.append({"port": p.get("ui_display") or pid, "kind": kind, "up": on, "link_mbps": lr if on else None,
                    "max_mbps": mx, "ifname": p.get("ifname"),
                    "below_max": on and kind != "USB" and 0 < lr < mx})
    order = {"WAN": 0, "LAN": 1, "USB": 2}
    return sorted(out, key=lambda x: (order.get(x["kind"], 3), x["port"]))
