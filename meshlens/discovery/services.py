"""Service discovery: mDNS / DNS-SD (SVC-01) and SSDP / UPnP (SVC-02). Listen-and-ask only."""
from __future__ import annotations

import ipaddress
import re
import socket
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

# Friendly names for common DNS-SD types (shown in the device drawer and used to classify devices).
SERVICE_LABELS = {
    "_airplay._tcp": "AirPlay", "_raop._tcp": "AirPlay audio", "_googlecast._tcp": "Google Cast",
    "_spotify-connect._tcp": "Spotify Connect", "_sonos._tcp": "Sonos", "_hap._tcp": "HomeKit",
    "_matter._tcp": "Matter", "_matterc._udp": "Matter (commissioning)", "_meshcop._udp": "Thread border router",
    "_ipp._tcp": "Printer (IPP)", "_ipps._tcp": "Printer (IPPS)", "_printer._tcp": "Printer (LPD)",
    "_pdl-datastream._tcp": "Printer (raw)", "_scanner._tcp": "Scanner", "_uscan._tcp": "Scanner (eSCL)",
    "_smb._tcp": "File share (SMB)", "_afpovertcp._tcp": "File share (AFP)", "_nfs._tcp": "File share (NFS)",
    "_ssh._tcp": "SSH", "_sftp-ssh._tcp": "SFTP", "_http._tcp": "Web page", "_https._tcp": "Web page (HTTPS)",
    "_companion-link._tcp": "Apple device", "_device-info._tcp": "Device info", "_homekit._tcp": "HomeKit",
    "_hue._tcp": "Philips Hue", "_esphomelib._tcp": "ESPHome", "_home-assistant._tcp": "Home Assistant",
    "_mqtt._tcp": "MQTT", "_workstation._tcp": "Workstation", "_rdlink._tcp": "Apple remote",
    "_amzn-wplay._tcp": "Amazon Fire TV", "_androidtvremote2._tcp": "Android TV", "_nvstream._tcp": "NVIDIA GameStream",
    "_xbox._tcp": "Xbox", "_dvl-deviceapi._tcp": "devolo device API", "_dvl-plcnetapi._tcp": "devolo powerline API",
    "_elg._tcp": "Elgato", "_alexa._tcp": "Alexa", "_googlezone._tcp": "Google Cast group",
    "_esphomebuilder._tcp": "ESPHome builder", "_glances._tcp": "Glances monitor", "_trel._udp": "Thread (TREL)", "_axis-video._tcp": "Axis camera", "_rtsp._tcp": "Video stream (RTSP)",
    "_daap._tcp": "iTunes library", "_touch-able._tcp": "Apple remote", "_adisk._tcp": "Time Machine",
}
BROWSE_SECONDS = 6.0


def mdns_browse(seconds: float = BROWSE_SECONDS) -> dict[str, dict[str, Any]]:
    """Find every advertised service type, then resolve instances. Returns {ip: {names, services, model}}."""
    from zeroconf import ServiceBrowser, Zeroconf, ZeroconfServiceTypes

    out: dict[str, dict[str, Any]] = {}
    zc = Zeroconf()
    try:
        try:
            types = set(ZeroconfServiceTypes.find(zc=zc, timeout=3))
        except Exception:
            types = set()
        types = {t for t in types if "._sub." not in t} | {f"{t}.local." for t in SERVICE_LABELS}
        found: list[tuple[str, str]] = []

        class _L:
            def add_service(self, zc_, type_, name):
                found.append((type_, name))

            def update_service(self, zc_, type_, name):
                pass

            def remove_service(self, zc_, type_, name):
                pass

        browsers = [ServiceBrowser(zc, t, _L()) for t in sorted(types)]
        time.sleep(seconds)
        for b in browsers:
            b.cancel()
        for type_, name in set(found):
            info = zc.get_service_info(type_, name, timeout=1500)
            if not info:
                continue
            short = type_.replace(".local.", "")
            txt = {k.decode(errors="replace"): (v or b"").decode(errors="replace")
                   for k, v in (info.properties or {}).items() if k}
            instance = name.replace("." + type_, "")
            for ip in info.parsed_addresses():
                if ":" in ip:
                    continue
                d = out.setdefault(ip, {"names": set(), "services": set(), "model": None, "host": None})
                d["services"].add(SERVICE_LABELS.get(short, short))
                d["names"].add(instance)
                d["host"] = (info.server or "").rstrip(".").removesuffix(".local") or d["host"]
                d["model"] = d["model"] or txt.get("model") or txt.get("md") or txt.get("ty") or txt.get("am")
    finally:
        zc.close()
    for d in out.values():
        d["names"], d["services"] = sorted(d["names"]), sorted(d["services"])
    return out


def _ssdp_search(seconds: float) -> dict[str, set[str]]:
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
           "MX: 2\r\nST: ssdp:all\r\n\r\n").encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.3)
    locs: dict[str, set[str]] = {}
    try:
        s.sendto(msg, ("239.255.255.250", 1900))
        end = time.time() + seconds
        while time.time() < end:
            try:
                data, (ip, _) = s.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m:
                locs.setdefault(ip, set()).add(m.group(1).decode(errors="replace"))
    finally:
        s.close()
    return locs


def parse_upnp_description(xml: bytes) -> dict[str, str | None]:
    root = ET.fromstring(xml)
    ns = {"u": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    dev = root.find("u:device", ns) if ns else root.find("device")

    def get(tag):
        el = dev.find(f"u:{tag}", ns) if ns else dev.find(tag)
        return el.text.strip() if el is not None and el.text else None

    if dev is None:
        return {}
    dt = get("deviceType") or ""
    parts = dt.split(":")
    return {"friendly_name": get("friendlyName"), "manufacturer": get("manufacturer"), "model": get("modelName"),
            "model_number": get("modelNumber"), "device_type": parts[-2] if len(parts) >= 2 else (dt or None)}


def ssdp_discover(subnets: list[str], seconds: float = 3.0) -> dict[str, dict[str, Any]]:
    """{ip: friendly name, maker, model} from UPnP device descriptions (only fetched from LAN addresses)."""
    nets = [ipaddress.ip_network(s, strict=False) for s in subnets]
    out: dict[str, dict[str, Any]] = {}
    for ip, urls in _ssdp_search(seconds).items():
        if not any(ipaddress.ip_address(ip) in n for n in nets):
            continue
        for url in sorted(urls):
            host = re.match(r"https?://([^/:]+)", url)
            if not host or host.group(1) != ip:
                continue  # only follow descriptions served by the device that answered
            try:
                with urllib.request.urlopen(url, timeout=3) as r:
                    desc = parse_upnp_description(r.read(200_000))
            except Exception:
                continue
            if desc:
                cur = out.setdefault(ip, {})
                for k, v in desc.items():
                    cur.setdefault(k, v)
                break
    return out
