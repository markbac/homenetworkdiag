# ASUS AiMesh data

The router and nodes know things no other source can: which node and band each client is on, link rates, 6 GHz clients, roaming, DHCP, channel utilisation on bands the probes cannot hear (6 GHz, and 5 GHz where no C5 probe is placed), and the settings that cause problems. **Goal: collect everything the BT8 and BD4 units expose, read-only.**

Decisions (see [decisions](10-decisions.md)): stock ASUS firmware, SSH enabled, read-only access only, everything on `10.0.10.0/23`.

## This network

| Unit | Model | Role | Clients (app) | Backhaul | Bands |
|---|---|---|---|---|---|
| Main | ZenWiFi BT8 | Router, `10.0.10.1` | 33 | Wired WAN | 2.4 / 5 / 6 GHz, Wi-Fi 7 |
| 1F | ZenWiFi BT8 | Node | 11 | Wireless, MLO | 2.4 / 5 / 6 GHz, Wi-Fi 7 |
| 2F | ZenWiFi BD4 | Node | 2 | Wireless via 1F | 2.4 / 5 GHz, Wi-Fi 7 |
| Outdoor | ZenWiFi BD4 Outdoor | Node | 4 | Wireless | 2.4 / 5 GHz, Wi-Fi 7 |
| (unnamed) | ZenWiFi BD4 | Node | 0 | Grey in app (offline?) | 2.4 / 5 GHz, Wi-Fi 7 |

## Chipset matters: detect, do not assume

The BT8 is reported to use **MediaTek** silicon (MT7988 CPU, MT7995/MT7975 radios); the BD4's chipset is not documented. Asuswrt's Wi-Fi tools differ by vendor:

| Vendor | Typical tools on Asuswrt | Notes |
|---|---|---|
| Broadcom | `wl` (status, assoclist, sta_info, chanim_stats, counters, ...) | Richest; most community docs assume this |
| MediaTek | `iwpriv <if> ...`, `mwctl`, and/or `iw` + `/sys/kernel/debug` if the mac80211 (`mt7996`) driver is used | Command set varies by driver; `homelab-unified` already falls back to `iw ... station dump` "e.g. MediaTek" |
| Qualcomm | `wlanconfig`, `iwpriv`, `cfg80211tool` | Unlikely here |

**Step 0 of the build (before any parser is written):** a read-only *capability capture* run against each unit that records `uname -a`, `nvram get productid firmver buildno extendno`, `ls /usr/sbin /usr/bin /sbin /bin`, which of `wl iw iwpriv mwctl hostapd_cli wlanconfig` exist, `iw dev`, interface names, and the raw output of every candidate command below. The outputs become the parser test fixtures (NFR-08) and decide which collector variant each unit uses. The tool repeats this detection at runtime (firmware updates can change it) and stores a per-unit capability profile.

## Sources

The collector uses three sources per unit, in this order, and records which one supplied each value.

### 1. ASUS web API (chipset independent)

The same HTTP endpoints the ASUS app and web UI use. Login `POST /login.cgi` (credentials base64), then read-only `GET/POST /appGet.cgi?hook=...` calls with the session token. Only `appGet.cgi` hooks on an allow-list are ever called (no `applyapp.cgi`, `apply.cgi`, `start_apply.htm`).

| Hook (candidate, verify on firmware) | Data |
|---|---|
| `get_clientlist()` | Every known client: name, nickname, vendor, type icon, IP, MAC, online, wired/wireless, band (`isWL`), RSSI, link rates, connected time, which AiMesh node (`amesh_bind_mac`) |
| `get_allclientlist()`, `get_wclientlist()`, `get_wiredclientlist()` | Per-node wireless and wired client lists across the mesh |
| `get_cfg_clientlist()` | AiMesh node list: model, MAC, IP, firmware, online, backhaul type and quality, parent node |
| `nvram_get(<key>)` | Any allow-listed nvram value (settings, firmware, WAN state) |
| `wl_sta_list_2g()`, `wl_sta_list_5g()`, `wl_sta_list_6g()` / `wl_sta_list_5g_2()` | Associated stations per band |
| `get_wan_unit()`, `wanlink()` | WAN status, IP, uptime |
| `netdev(appobj)` | Interface byte counters (traffic over time) |
| `cpu_usage(appobj)`, `memory_usage(appobj)` | Unit health |
| `get_bwdpi_...` (Traffic Analyzer, if enabled) | Per-device traffic by app category |

### 2. SSH (read-only commands, allow-listed)

Shell access gives the radio internals the web API does not.

**System and identity (all units)**

| Command / file | Data |
|---|---|
| `nvram get productid firmver buildno extendno odmpid serial_no` | Model and exact firmware |
| `cat /proc/uptime /proc/loadavg /proc/meminfo`, `head -1 /proc/stat` | Uptime, load, memory, CPU |
| `cat /sys/class/thermal/thermal_zone*/temp` (and vendor temp tools) | CPU / radio temperature (hot radios throttle) |
| `cat /proc/net/dev` | Byte/packet/error/drop counters per interface (incl. each radio and backhaul) |
| `ip -s link`, `ip addr`, `ip route`, `ip neigh show` | Interfaces, errors/drops, neighbours |
| `brctl show`, `bridge fdb show` | Which MAC is behind which port/radio/backhaul (wired vs wireless, which node) |
| Switch/port status (`ethctl`, `/sys/class/net/eth*/speed`, `.../carrier`) | Wired port link up/down and speed (e.g. a 100 Mbps link on a 2.5G port) |

**Main router only**

| Command / file | Data |
|---|---|
| `cat /var/lib/misc/dnsmasq.leases` | DHCP leases: IP, MAC, hostname, expiry |
| `nvram get clientlist`, `custom_clientlist`, `nmp_client_list`; `/jffs/nmp_cl_json.js`, `/tmp/clientlist.json`, `/tmp/allwclientlist.json`, `/tmp/wiredclientlist.json` (where present) | Network-map client database: names, vendor, type, per-node association |
| `nvram get cfg_device_list`, `cfg_*` / `amas_*` keys, `/tmp/cfg_*` files | AiMesh topology, node IPs, backhaul |
| `cat /proc/net/nf_conntrack` (first N lines) | Active connections per device (who is busy) |
| `cat /tmp/syslog.log /tmp/syslog.log-1` (stock firmware) or `logread` | Wi-Fi associate/disassociate/deauth with reason codes, roaming (802.11k/v), DHCP, WAN up/down, AiMesh events, DFS radar hits, reboots |
| `/jffs/.sys/TrafficAnalyzer/*.db` (copied, read-only, if Traffic Analyzer is on) | Per-device traffic history |

**Radios, per unit and per band (vendor-specific; detected in step 0)**

| Data | Broadcom | MediaTek / mac80211 candidates |
|---|---|---|
| Interface per band, SSID, BSSID | `wl -i <if> status` | `iw dev`, `iw dev <if> info`, `iwpriv <if> stat` |
| Channel, width, centre, DFS state | `wl -i <if> chanspec`, `wl dfs_status` | `iw dev <if> info`, `iwpriv <if> get_channel`, `mwctl <if> ...` |
| Noise floor | `wl -i <if> noise` | `iw dev <if> survey dump` (noise), `iwpriv <if> stat` |
| **Channel utilisation (busy %, own vs other BSS, interference)** | `wl -i <if> chanim_stats` | `iw dev <if> survey dump` (active / busy / rx / tx time), MediaTek `iwpriv ... ChannelUtil` style stats |
| Associated stations | `wl -i <if> assoclist` | `iw dev <if> station dump`, `iwpriv <if> show stainfo` |
| Per station: RSSI (per chain), tx/rx rate, MCS, NSS, width, PHY mode (HE/EHT), idle, connected time, tx retries/failures, PS mode, MLO links | `wl -i <if> sta_info <mac>` | `iw dev <if> station get <mac>` (signal, tx/rx bitrate, retries, failed, inactive time, connected time) |
| Radio counters (tx/rx, retries, failures, CRC errors) | `wl -i <if> counters` | `iwpriv <if> stat`, `/proc/net/dev` |
| Transmit power, regulatory | `wl txpwr1`, `wl country` | `iw reg get`, `iw dev <if> info` |
| Neighbouring APs seen by the router | `wl scanresults` (passive cache only, never trigger a scan) | `iw dev <if> scan dump` (cached only) |
| Multicast / basic rates | `wl rate`, `wl mrate` | `iwpriv` / nvram |
| Backhaul link quality (node to parent) | `wl -i <backhaul if> sta_info <parent>` | `iw dev <backhaul if> station dump` |
| MLO (Wi-Fi 7 multi-link) status | `wl mlo ...` | `mwctl ... mlo` / `iw` MLD info |

### 3. Settings (nvram, allow-listed keys, read-only)

Read on every full poll and diffed, so "what changed" can be lined up with when problems started.

| Area | Keys (prefixes vary: `wl0_` 2.4 GHz, `wl1_` 5 GHz, `wl2_`/`wl3_` 6 GHz or 5 GHz-2) |
|---|---|
| Channel / width / band | `wlX_chanspec`, `wlX_bw`, `wlX_nmode_x`, `wlX_11ax`, `wlX_11be` |
| Air-time costs | `wlX_mrate_x` (multicast rate), `wlX_rateset`, legacy/b rates, `wlX_atf` (airtime fairness), `wlX_frameburst` |
| Multicast handling | `emf_enable`, `wlX_igs` (IGMP snooping), `mr_enable_x` |
| SSIDs | `wlX_ssid`, guest networks `wlX.1_*`, `wlX.2_*` ... (enabled, bands, per node), `wlX_closed` (hidden) |
| Steering / roaming | `smart_connect_x`, `wlX_user_rssi` (roaming assistant), `wlX_rrm`, `wlX_wnm` (802.11k/v), `wlX_bss_trans` |
| Mesh | `amas_*`, `cfg_*`, backhaul preference, ethernet backhaul mode |
| DHCP / LAN | `lan_ipaddr`, `lan_netmask`, `dhcp_start`, `dhcp_end`, `dhcp_lease`, `dhcp_staticlist` |
| WAN | `wan0_*` state, protocol, DNS |
| Firmware | `firmver`, `buildno`, `extendno`, auto-update settings |

Secrets (`*_wpa_psk`, `http_passwd`, keys, certificates) are **never** read or stored: the allow-list excludes them, and any value matching a secret pattern is redacted before storage.

## Polling plan

| Data | Interval | Source |
|---|---|---|
| Clients, per-station radio stats, channel utilisation | 30 to 60 s while the tray app runs | Web API + SSH |
| Interface counters, unit health | 60 s | SSH |
| Syslog (new lines since last read) | 60 s | SSH |
| Topology, settings, DHCP leases, capability profile | 15 min and at start | Web API + SSH |

Load: one SSH session per unit kept open, commands run sequentially with short timeouts; nothing that triggers a scan or restarts a service.

## What this enables

- Per client: band, node, signal, rate, retries over time; "on 2.4 GHz but capable of 5/6 GHz"; "stuck on a distant node"; "rate collapses every evening".
- Per radio: busy % on every band including 6 GHz, noise, DFS events, compared with what the probes hear.
- Per node: backhaul quality, offline periods, client counts.
- Settings explained (multicast rate, IGMP snooping, guest SSIDs, steering) and changes tracked.
