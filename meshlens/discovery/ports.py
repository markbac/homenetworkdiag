"""TCP port scan (PRT-01), web page titles and banners (PRT-05, SVC-05), risk labels (PRT-04).

A plain connect scan: no admin rights, no raw packets. Paced and with a small number of parallel
connections per device so cameras and smart plugs are not upset.
"""
from __future__ import annotations

import asyncio
import re
import ssl
import time
from typing import Any

# Service name per port (the ones a home network usually has).
SERVICES = {
    21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP", 81: "HTTP", 88: "Kerberos",
    110: "POP3", 111: "RPC", 135: "MS RPC", 139: "NetBIOS", 143: "IMAP", 161: "SNMP", 389: "LDAP", 443: "HTTPS",
    445: "SMB", 465: "SMTPS", 515: "LPD printer", 548: "AFP", 554: "RTSP camera", 587: "SMTP", 631: "IPP printer",
    853: "DNS over TLS", 873: "rsync", 993: "IMAPS", 995: "POP3S", 1080: "SOCKS proxy", 1400: "Sonos",
    1433: "SQL Server", 1723: "PPTP VPN", 1883: "MQTT", 1900: "UPnP", 2049: "NFS", 2375: "Docker API",
    2376: "Docker API (TLS)", 3000: "Web app", 3074: "Xbox Live", 3306: "MySQL", 3389: "Remote Desktop",
    3478: "STUN", 3689: "iTunes (DAAP)", 4070: "Spotify", 4443: "HTTPS", 5000: "Web / UPnP", 5001: "HTTPS (Synology)",
    5060: "SIP", 5353: "mDNS", 5357: "WSD", 5432: "PostgreSQL", 5555: "Android debug", 5900: "VNC",
    5985: "WinRM", 6379: "Redis", 6443: "Kubernetes", 6466: "Android TV remote", 6467: "Android TV remote",
    7000: "AirPlay", 7100: "AirPlay", 8000: "HTTP", 8008: "Google Cast", 8009: "Google Cast", 8060: "Roku",
    8080: "HTTP", 8081: "HTTP", 8083: "HTTP", 8086: "InfluxDB", 8123: "Home Assistant", 8443: "HTTPS",
    8554: "RTSP camera", 8883: "MQTT (TLS)", 8888: "HTTP", 9000: "Web app", 9090: "Web app", 9100: "Printer (raw)",
    9443: "HTTPS", 10000: "Webmin", 11211: "Memcached", 14791: "devolo", 27017: "MongoDB", 32400: "Plex",
    49152: "UPnP", 62078: "Apple sync",
}
TOP100 = sorted(SERVICES)[:100]
TOP1000 = sorted(set(TOP100) | set(range(1, 1025)) | {1400, 1883, 3389, 5000, 5001, 5555, 5900, 7000, 8000, 8008,
                                                     8009, 8080, 8081, 8123, 8443, 8554, 8883, 8888, 9000, 9100,
                                                     9443, 32400, 49152, 62078})
WEB_PORTS = {80, 81, 443, 3000, 4443, 5000, 5001, 8000, 8008, 8080, 8081, 8083, 8123, 8443, 8888, 9000, 9090, 9443, 10000}
TLS_PORTS = {443, 4443, 5001, 8443, 9443}

# "Worth checking" labels (PRT-04): informational, not vulnerabilities.
RISKS = {
    21: "FTP sends passwords unencrypted",
    23: "Telnet: unencrypted remote login, often with default passwords",
    445: "File sharing; fine on a PC or NAS, unexpected on a gadget",
    1883: "MQTT without TLS: check it requires a password",
    2375: "Docker API without TLS gives full control of the host",
    3389: "Remote Desktop: keep it off the internet",
    5555: "Android debugging open: anyone on the network can control the device",
    5900: "VNC: check it requires a password",
    6379: "Redis usually has no password",
    11211: "Memcached usually has no password",
    27017: "MongoDB: check it requires a password",
    554: "Camera stream: check it requires a password",
    8554: "Camera stream: check it requires a password",
}
_TITLE = re.compile(rb"<title[^>]*>\s*(.*?)\s*</title>", re.I | re.S)


async def _probe(ip: str, port: int, timeout: float) -> dict[str, Any] | None:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    res: dict[str, Any] = {"port": port, "service": SERVICES.get(port), "risk": RISKS.get(port)}
    try:
        if port in (22, 21, 23, 25, 110, 143):  # these speak first
            try:
                line = await asyncio.wait_for(reader.readline(), 1.5)
                res["banner"] = line.decode(errors="replace").strip()[:120] or None
            except asyncio.TimeoutError:
                pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    if port in WEB_PORTS:
        res.update(await _web_title(ip, port, port in TLS_PORTS))
    return res


async def _web_title(ip: str, port: int, tls: bool) -> dict[str, Any]:
    ctx = None
    if tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE   # home devices use self-signed certificates; we only read the title
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port, ssl=ctx), 3)
        writer.write(f"GET / HTTP/1.0\r\nHost: {ip}\r\nUser-Agent: MeshLens\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.read(65536), 3)
        writer.close()
    except Exception:
        return {}
    out: dict[str, Any] = {"url": f"{'https' if tls else 'http'}://{ip}{'' if port in (80, 443) else f':{port}'}/"}
    m = _TITLE.search(data)
    if m:
        out["title"] = re.sub(r"\s+", " ", m.group(1).decode(errors="replace"))[:120]
    srv = re.search(rb"(?im)^server:\s*(.+?)\r?$", data)
    if srv:
        out["banner"] = srv.group(1).decode(errors="replace")[:120]
    return out


async def scan_host(ip: str, ports: list[int], parallel: int = 32, timeout: float = 0.8,
                    rate: int = 200) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(parallel)
    found: list[dict[str, Any]] = []

    async def one(p: int, delay: float) -> None:
        await asyncio.sleep(delay)
        async with sem:
            r = await _probe(ip, p, timeout)
            if r:
                found.append(r)

    await asyncio.gather(*(one(p, i / rate) for i, p in enumerate(ports)))
    return sorted(found, key=lambda r: r["port"])


def scan(ip: str, port_set: str = "top100", rate: int = 200) -> tuple[list[dict[str, Any]], list[int]]:
    ports = TOP1000 if port_set == "top1000" else TOP100
    t0 = time.time()
    found = asyncio.run(scan_host(ip, ports, rate=rate))
    for f in found:
        f["scan_s"] = round(time.time() - t0, 1)
    return found, ports
