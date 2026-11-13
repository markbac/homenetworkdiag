"""Finding the dashboard from a phone: addresses that work, a QR code, ``meshlens.local``, home-screen icon.

- ``lan_address()``: this PC's address on the home network (the one that routes to the router).
- ``Advertiser``: announces ``meshlens.local`` and an ``_http._tcp`` service over mDNS, so iPhones,
  Macs, Windows and most Android 12+ phones can open http://meshlens.local:8787 without an address.
- ``urls()``: every address worth trying, best first, each checked to resolve to this PC.
- ``qr_svg()``: QR code for a URL (scan with the phone camera).
"""
from __future__ import annotations

import io
import logging
import socket
from typing import Any

log = logging.getLogger("meshlens.connect")
MDNS_NAME = "meshlens"


def lan_address(router: str = "10.0.10.1") -> str | None:
    """The local address used to reach the router (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((router, 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def dns_query(server: str, name: str, qtype: int = 1, timeout: float = 2.0) -> list[str]:
    """Minimal DNS query to ``server`` (the router): A (1) -> addresses, PTR (12) -> names.

    Asking the router, not this PC's resolver, shows what a phone on the network will get
    (this PC's hosts file, e.g. Docker's host.docker.internal, does not count).
    """
    import random
    import struct
    tid = random.randint(0, 0xFFFF)
    q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.rstrip(".").split(".")) + b"\x00" + struct.pack(">HH", qtype, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(1500)
    except OSError:
        return []
    finally:
        s.close()

    def read_name(off: int) -> tuple[str, int]:
        labels, jumped, end = [], False, off
        for _ in range(64):
            n = data[off]
            if n == 0:
                off += 1
                break
            if n & 0xC0 == 0xC0:
                if not jumped:
                    end = off + 2
                off, jumped = ((n & 0x3F) << 8) | data[off + 1], True
                continue
            labels.append(data[off + 1:off + 1 + n].decode("ascii", "replace"))
            off += 1 + n
        return ".".join(labels), (end if jumped else off)

    try:
        tid2, flags, qd, an = struct.unpack(">HHHH", data[:8])
        if tid2 != tid or flags & 0x000F:
            return []
        off = 12
        for _ in range(qd):
            _, off = read_name(off)
            off += 4
        out = []
        for _ in range(an):
            _, off = read_name(off)
            rtype, _cls, _ttl, ln = struct.unpack(">HHIH", data[off:off + 10])
            off += 10
            if rtype == 1 and qtype == 1 and ln == 4:
                out.append(socket.inet_ntoa(data[off:off + 4]))
            elif rtype == 12 and qtype == 12:
                out.append(read_name(off)[0])
            off += ln
        return out
    except (IndexError, struct.error):
        return []


def urls(port: int, router: str, mdns_ok: bool) -> list[dict[str, Any]]:
    """Addresses a phone can use, best first. The first goes in the QR code."""
    ip = lan_address(router)
    if not ip:
        return []
    named: list[dict[str, Any]] = []
    names = set(dns_query(router, ".".join(reversed(ip.split("."))) + ".in-addr.arpa", 12))
    names.add(socket.gethostname())
    for n in sorted(names, key=lambda x: (-x.count("."), x)):
        n = n.rstrip(".").lower()
        if n and n != ip and ip in dns_query(router, n):
            named.append({"url": f"http://{n}:{port}/", "how": "This PC's name in the router's DNS. Works on any phone "
                          "or laptop on the home network that uses the router (or Pi-hole) for DNS."})
    out = named[:1]
    out.append({"url": f"http://{ip}:{port}/", "how": "This PC's address. Always works, but can change if the router hands "
                "out a different one; reserve it in the router (LAN → DHCP → manual assignment) to keep it."})
    if mdns_ok:
        out.append({"url": f"http://{MDNS_NAME}.local:{port}/", "how": "Name announced on the network (mDNS). Works on "
                    "iPhone/iPad, Mac and Windows; many Android browsers do not support .local names."})
    out += named[1:]
    return out


def qr_svg(text: str) -> bytes:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


def icon_png(size: int) -> bytes:
    """Home-screen icon (same mark as the tray icon)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), "#2463eb")
    d = ImageDraw.Draw(img)
    m, w = size * 0.18, max(2, int(size * 0.07))
    d.ellipse((m, m, size - m, size - m), outline="white", width=w)
    c = size * 0.38
    d.ellipse((c, c, size - c, size - c), fill="white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class Advertiser:
    """mDNS announcement of meshlens.local (A record) and the dashboard service."""

    def __init__(self) -> None:
        self.zc = None
        self.info = None
        self.ok = False

    def start(self, port: int, router: str) -> None:
        ip = lan_address(router)
        if not ip:
            return
        try:
            from zeroconf import ServiceInfo, Zeroconf
            self.zc = Zeroconf(interfaces=[ip])
            self.info = ServiceInfo("_http._tcp.local.", "MeshLens._http._tcp.local.", addresses=[socket.inet_aton(ip)],
                                    port=port, properties={"path": "/"}, server=f"{MDNS_NAME}.local.")
            self.zc.register_service(self.info, allow_name_change=True)
            self.ok = True
            log.info("announced http://%s.local:%s/ on %s", MDNS_NAME, port, ip)
        except Exception as e:  # another responder owns the name, no multicast, ...
            log.warning("mDNS announcement failed: %s", e)
            self.stop()

    def stop(self) -> None:
        try:
            if self.zc and self.info:
                self.zc.unregister_service(self.info)
            if self.zc:
                self.zc.close()
        except Exception:
            pass
        self.zc = None
