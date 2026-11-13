# Network inventory, services and security

Beyond Wi-Fi: what is on the network, what each device offers, what is exposed, and whether the core services (DHCP, DNS, NAT, ISP) are healthy. Most collectors already exist in `homelab-unified` (see [reuse](06-reuse.md)).

## Ground rules

- **Passive first.** Listen (mDNS, SSDP, DHCP, ARP, router data) before probing.
- **Active scans are paced and scheduled** (NFR-02): a full TCP scan of the top ports runs at most daily per device, spread over time; on-demand deep scans need a click.
- **Never intrusive.** No password guessing, no exploits, no vulnerability "attacks", no traffic interception. Version detection and banners only.
- **Everything stays on the LAN.** No device data sent to online services.

## 1. Device identity and services (what is it, what does it offer)

| ID | Requirement | Source | Pri |
|---|---|---|---|
| SVC-01 | **mDNS / DNS-SD browse of all service types** (`_services._dns-sd._udp`), recording each device's services: AirPlay (`_airplay`, `_raop`), Google Cast (`_googlecast`), Spotify Connect, Sonos, HomeKit (`_hap`), Matter (`_matter`, `_matterc`), Thread border routers (`_meshcop`), printers (`_ipp`, `_ipps`, `_printer`, `_pdl-datastream`), file shares (`_smb`, `_afpovertcp`), `_ssh`, `_http`/`_https`, `_companion-link`, `_device-info` (model). TXT records parsed for model, firmware, friendly name. | mDNS (reuse `enrichment/dns.py`, `zeroconf`) | M |
| SVC-02 | **SSDP / UPnP**: device description XML (friendlyName, manufacturer, model, serial, services), including the router's IGD. | reuse `discovery/ssdp.py` | M |
| SVC-03 | **Names** from DHCP, mDNS, NetBIOS, LLMNR, reverse DNS, router database, Home Assistant, user. Show which source each came from. | reuse `enrichment/*`, ASUS `nmp_cl_json.js` | M |
| SVC-04 | **DHCP fingerprinting**: option 55 parameter list and vendor class (e.g. `MSFT 5.0`, `android-dhcp-16`) to infer OS/device type. | Router database (`vendorclass`), optional Npcap capture of DHCP | S |
| SVC-05 | **OS / device hints**: IPv4 TTL, TCP window, HTTP server header and page title, TLS certificate subject, SSH banner, SNMP `sysDescr`. | reuse `enrichment/http.py`, `ssh.py`, `snmp.py` | S |
| SVC-06 | **IPv6**: neighbour table, SLAAC/temporary addresses per device, router advertisements, whether IPv6 internet works. | Windows `GetIpNetTable2`, router | S |
| SVC-07 | **Device classification** (phone, PC, console, TV, speaker, camera, plug, hub, printer, NAS, network gear) combining vendor, services, ports, DHCP and names; user can override. | reuse `classification/*` | M |
| SVC-08 | **Inventory changes**: new device, device gone, new service, name or vendor changed, MAC changed for a known name. Shown as findings (no alerts, D-19). | store diff | M |

## 2. Open ports and exposed services

| ID | Requirement | Source | Pri |
|---|---|---|---|
| PRT-01 | **TCP port scan** of each device: top 100 ports daily (paced), top 1,000 or full on demand. Service/version detection with nmap when installed (Npcap allowed, D-11); built-in connect scan otherwise. | reuse `discovery/nmap.py` + Windows connect scan | M |
| PRT-02 | **Selected UDP checks**: DNS 53, NTP 123, SNMP 161, SSDP 1900, mDNS 5353, NetBIOS 137, WS-Discovery 3702, CoAP 5683. | built-in probes | S |
| PRT-03 | **Port history**: when a port opened or closed, per device. | store | M |
| PRT-04 | **Risk labels** per open service (reuse `classification/risk.py`): e.g. Telnet, FTP, SMBv1, unauthenticated MQTT/RTSP/VNC/RDP, UPnP admin, default web admin pages, expired or self-signed TLS on admin pages. Shown as "worth checking", not as vulnerabilities. | reuse | M |
| PRT-05 | **Web interfaces list**: every device serving HTTP(S), with title and a link (printers, cameras, NAS, IoT hubs). | `enrichment/http.py` | S |

## 3. Router exposure and NAT (also matters for gaming)

| ID | Requirement | Source | Pri |
|---|---|---|---|
| NAT-01 | **UPnP / NAT-PMP port mappings** currently open on the router: which device opened which external port (consoles, PCs, cameras). | Router: UPnP leases file / `iptables -t nat` (read-only, to verify on stock firmware); IGD query via SSDP | M |
| NAT-02 | **Configured port forwards, DMZ, UPnP on/off** (nvram `vts_rulelist`, `dmz_ip`, `upnp_enable`). | Router nvram (allow-listed) | M |
| NAT-03 | **WAN addressing**: public vs private WAN IP (double NAT), CGNAT range (100.64.0.0/10), IPv6 prefix; explains console "NAT type" problems. | Router + optional public-IP lookup (opt-in) | M |
| NAT-04 | **Firewall state** on the router (enabled, ping from WAN, remote admin access, SSH bound to LAN only). | nvram (allow-listed) | S |

## 4. Security hygiene

| ID | Requirement | Source | Pri |
|---|---|---|---|
| SEC-01 | **Wi-Fi security per device**: WPA3-SAE vs WPA2-PSK vs WPA-PSK-SHA256, PMF; which SSID (main, IoT, guest); flags IoT devices on the main SSID and PCs/phones on the IoT SSID. | Router `allwclientlist.json` (`wifi_auth`, verified) | M |
| SEC-02 | **Rogue / lookalike access points**: an SSID matching yours (`MH-Pvt`...) from a BSSID that is not part of your mesh. | Probe scans + mesh family | M |
| SEC-03 | **Rogue DHCP servers**. | reuse `discovery/dhcp_probe.py` | M |
| SEC-04 | **ARP anomalies**: gateway IP answered by a different MAC, duplicate IPs, one MAC claiming many IPs (except mesh nodes proxying, which are recognised). | Windows neighbour table, router ARP, optional Npcap | M |
| SEC-05 | **DNS integrity**: answers from the configured resolver compared with a reference (opt-in DoH check) to spot hijacking; Pi-hole block stats. | built-in + Pi-hole plug-in | S |
| SEC-06 | **Router hygiene**: firmware version and age, auto-update setting, WPS on/off, admin over WAN, default SSID names in use. | nvram (allow-listed) | S |
| SEC-07 | **Certificates** on LAN services: expiring soon, expired, self-signed (informational). | `enrichment/http.py` | C |

## 5. Core services and the internet connection

| ID | Requirement | Source | Pri |
|---|---|---|---|
| ISP-01 | **DHCP health**: pool size vs leases in use (e.g. `10.0.10.0/23` range), lease churn per device, static leases, response time. | Router leases + nvram, `dhcp_probe` timing | M |
| ISP-02 | **DNS**: latency and failures per resolver (router, Pi-hole, ISP, 1.1.1.1, 8.8.8.8), cache hit behaviour, per-device query volume (Pi-hole). | reuse `discovery/dns_latency.py`, Pi-hole | M |
| ISP-03 | **WAN stability**: PPPoE/WAN reconnects and IP changes over time, uptime. | Router syslog + nvram `wan0_*` | M |
| ISP-04 | **First-hop and path**: loss/latency to the ISP's first hop (beyond the router) to separate home problems from ISP problems; periodic traceroute to key destinations (game servers, streaming CDNs) with hop changes. | ICMP/traceroute from PC and probes | M |
| ISP-05 | **Bufferbloat / loaded latency** and speed history. | reuse `discovery/speedtest.py` | M |
| ISP-06 | **MTU / path MTU** problems (PPPoE 1492), fragmentation. | DF-bit pings | S |
| ISP-07 | **Time sync**: NTP reachability and clock offset of the PC, router and probes. | built-in | C |

## 6. Wired topology and links

| ID | Requirement | Source | Pri |
|---|---|---|---|
| TOP-01 | **Topology map**: internet, router, mesh nodes (wired or wireless backhaul), switches, and each device under the unit/port it is behind. | Router/node bridge FDB, `wiredclientlist.json`, mesh data | M |
| TOP-02 | **Port link status**: speed and duplex per router/node port, errors and drops; flag e.g. a 100 Mbit/s link on a 2.5G port. | `/tmp/diag_port_status.json`, `/sys/class/net/*/speed` (verified present) | M |
| TOP-03 | **Managed switches** (if any): LLDP/SNMP for port-to-device mapping. | reuse `discovery/snmp_probe.py` | C |
| TOP-04 | **Multicast groups** (IGMP membership) and the top multicast/broadcast senders on the wire (with Npcap), matched to what probes see in the air. | Npcap, router | S |

## Dashboard additions

- **Devices** page: identity (all names with sources), type, vendor, services (mDNS/SSDP), open ports with risk labels, web UI links, Wi-Fi security, SSID, history.
- **Services** page: "every printer / speaker / cast target / camera / web admin page on the network".
- **Exposure** page: router port forwards, UPnP mappings (which device opened what), DMZ, NAT type, WAN/IPv6 status.
- **Security** page: hygiene checks with plain-English explanations, inventory changes.
- **Internet** page: WAN stability, first-hop loss, DNS, bufferbloat, traceroutes, speed history.
- **Topology** page: wired/wireless tree with link speeds.

## Example from this network

The router already records Wi-Fi security per device (`allwclientlist.json`): most clients use WPA3-SAE, some devices WPA-PSK (several on MH-Pvt_IoT), and one device on the Garden unit WPA-PSK-SHA256. SEC-01 would list them, and flag any phone or PC that should be on the main SSID with WPA3 instead.
