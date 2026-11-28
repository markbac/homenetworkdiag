"""Which interfaces mDNS listens on.

A Docker host has dozens of bridge interfaces (docker0, br-*, veth*). Joining the multicast group on
all of them exhausts the kernel's membership limit and can leave the real LAN interface out, so only
physical / Wi-Fi interfaces are used.
"""
from __future__ import annotations

VIRTUAL_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "cni", "flannel", "tailscale", "zt", "wg")


def interfaces():
    """IPv4 addresses for zeroconf's ``interfaces=``; all interfaces when none can be picked."""
    from zeroconf import InterfaceChoice
    try:
        import ifaddr
        out: list[str] = []
        for a in ifaddr.get_adapters():
            if a.name.lower().startswith(VIRTUAL_PREFIXES):
                continue
            out += [ip.ip for ip in a.ips if isinstance(ip.ip, str) and not ip.ip.startswith(("127.", "169.254."))]
        return out or InterfaceChoice.All
    except Exception:
        return InterfaceChoice.All
