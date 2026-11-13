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

## Verified on this network (13 November 2026)

Read-only capture over SSH (port 1111) of every reachable unit. Raw outputs are kept locally (they contain device names and addresses) and will become parser fixtures.

### Units and what they run

| Unit (ASUS alias) | Model / `productid` | IP | Firmware | Chip family | Wi-Fi interfaces | SSH |
|---|---|---|---|---|---|---|
| Main router | ZenWiFi BT8 / `BT8` | 10.0.10.1 | 3.0.0.6.102_58601 (`..._M7EE`) | **MediaTek** (Linux 5.4, aarch64) | `ra0` 2.4, `rai0` 5, `rax0` 6 GHz; SSIDs `ra1-3`, `rai1-3`, `rax1-2`; backhaul client `apcli*` | Yes |
| First Floor | ZenWiFi BT8 / `BT8` | 10.0.11.29 | same | **MediaTek** | same as main | Yes |
| Second Floor | ZenWiFi BD4 / `BD4D5` | 10.0.11.14 | 3.0.0.6.102_58396 (`..._Q7PA`) | **Qualcomm** | `ath0` 2.4, `ath1` 5 GHz; SSIDs `ath001-004`, `ath101-104`; `mld0`; backhaul `sta0/sta1` | Yes |
| Office | ZenWiFi BD4 | not found | | Qualcomm (assumed) | | **No** (not in `cfg_device_list`) |
| Garden | ZenWiFi BD4 Outdoor | not found | | Qualcomm (assumed) | | **No** (backhaul links `alive:0`) |

Mesh: Second Floor prefers First Floor as its parent (`amas_wlc_target_bssid`); the BT8 pair link over 6 GHz, 320 MHz, EHT-MCS 10, 3 streams (~1.1 Gbit/s, -49 dBm), MLO active. Office and Garden appear in the client data but cannot be reached as managed nodes: the tool should report this.

### Tools present

| Tool | BT8 (MediaTek) | BD4 (Qualcomm) |
|---|---|---|
| `wl` (Broadcom) | missing | missing |
| `iw` | yes, but `station dump` lists only the mesh backhaul peer, and `survey dump` returns zeros | yes |
| `iwpriv` | "no private ioctls" | yes |
| `mwctl` (MediaTek) | yes: read commands `dev <if> stat`, `show <param>`, `dump radio_stats`, `dump bss_stats`, `dump sta_stats <mac>`, `dump radio_info`, `dump mlo_info`, `dump con_sta_mld`, `phy <phy> dump band_info` (help warns its output is not stable) | missing |
| `wlanconfig`, `cfg80211tool`, `apstats`, `athstats`, `wifitool` (Qualcomm) | missing | yes |
| `hostapd_cli` | **yes: `-i <if> all_sta`** | yes |
| `iwconfig` | yes | yes (noise floor) |
| `sqlite3`, `brctl`, `ip`, `arp` | yes | yes |
| `logread`, `bridge`, `conntrack`, `timeout`, `ethctl` | missing | missing |

### Best source per data item (verified)

| Data | BT8 (MediaTek) | BD4 (Qualcomm) | Any unit |
|---|---|---|---|
| Clients per node, band and SSID, with IP and RSSI | | | `/tmp/clientlist.json` (main router: whole mesh) |
| Clients with interface, auth type, MLO, timestamp | | | `/tmp/allwclientlist.json` (main router) |
| Wired clients per node | | | `/tmp/wiredclientlist.json` |
| Device database (name, vendor, type, OS, online, wired/wireless, MLO links, connected since, sources of each field) | | | `/jffs/nmp_cl_json.js` (66 KB, main router) |
| Per-client signal, tx/rx rate + MCS/NSS/GI, packets, bytes, idle, connected time, flags | `hostapd_cli -i ra1 all_sta` (per SSID interface) | `wlanconfig ath001 list sta` (RSSI current/min/max, rates, idle, assoc time, caps) | |
| Backhaul link (signal, rate, MCS, retries/failed) | `iw dev rax0 station dump` | `iwconfig sta0/sta1` | |
| Radio channel, width, tx power | `iw dev` | `iwconfig`, `iw dev` | nvram `wlX_*` |
| Channel utilisation | `mwctl dev <if> dump radio_stats` (to confirm) | `apstats -R`: utilisation **disabled** on stock firmware | probes |
| Noise floor | `mwctl` stats (to confirm) | `iwconfig` (-92 to -94 dBm) | |
| Interface counters | `/proc/net/dev` | `/proc/net/dev` | |
| Mesh topology, node aliases, backhaul preferences | | | `nvram get cfg_device_list`; `/tmp/<node-mac>.json` (alias e.g. "First Floor", preferred parent, channel plan); `/tmp/relist.json` (backhaul MACs per node); `/tmp/smarthaul.json`; `/tmp/cfgmnt_log.txt` |
| Channel plans | | | `/tmp/chanspec_all.json`, `chanspec_avbl.json`, `wchannel.json` |
| Router health | | | `/tmp/sysstate_all.json` (CPU, RAM, uptime averages), `/proc/*` |
| Wired port status | | | `/tmp/diag_port_status.json` |
| Wi-Fi counters | | | `/jffs/wlcnt.json` |
| Roaming and steering | | | `/tmp/syslog.log`: `roamast` (candidate node + RSSI, roam result, "disconnect weak signal strength station"), `hapdevent` Assoc/Disassoc per interface; `/tmp/sta_steer_progress` |
| DHCP | | | `/var/lib/misc/dnsmasq.leases` |

### Radio health (MediaTek `mwctl dev <if> stat`, main router)

| Band | Tx failures | Rx CRC errors | Temperature | Last rates |
|---|---|---|---|---|
| 2.4 GHz `ra0` | **15.7%** | **22.6%** | 53 C | MCS1 / MCS4, 20 MHz |
| 5 GHz `rai0` | 6.1% | 4.5% | 53 C | NSS1 MCS7, 80 MHz |
| 6 GHz `rax0` | 2.4% | 0.6% | 50 C | NSS3 MCS10, 320 MHz (backhaul) |

`mwctl stat` also shows proxy ARP **off** and BSS Transition Management (802.11v) on. `mwctl dump radio_stats` / `bss_stats` give only byte counters; neither MediaTek nor the BD4 (`apstats`: "Channel Utilization = DISABLED") exposes channel utilisation on stock firmware, so **channel busy % comes from the probes**.

### Logs: what they hold

| Unit | History in the log | Connects / disconnects | Weak-signal kicks | Roam attempts |
|---|---|---|---|---|
| Main router | ~31 h (since 12 Nov 08:47) | 210 / 208 | 67 | 37 |
| First Floor BT8 | ~3.3 days (since 10 Nov 09:29) | 1,053 / 1,044 | 206 | 112 |
| Second Floor BD4 | ~1 day (since 12 Nov 17:01) | Qualcomm format (`assoc`/`ASSOC`/`deauth`) | 26 | |

MediaTek lines: `hapdevent: ... ra1: Assoc|Disassoc <mac>`; `roamast: determine candidate node [<bssid>](rssi: -63dbm) for client [<mac>](rssi: -74dbm from client)(rssi: -74dbm from ap) to roam`; `roamast: Roam a client [<mac>], status [0]`; `roamast: [EXAP]Deauth old sta in wl1.1: <mac>, because sta is already connected to <node>`; `roamast: rai1: disconnect weak signal strength station [<mac>]`. The log rotates (`syslog.log` + `syslog.log-1`), so the tool must read it often enough (every minute) to keep a complete history of its own.

Throughput: `/proc/net/dev` sampled twice gives per-interface rates (WAN `ppp0`, SSIDs `ra1..rax1`, backhaul `rax0`/`apcli*`, LAN ports). Connection tracking: ~350 active connections (counts per device to be aggregated on the PC; the router's busybox has no `uniq`).

### Settings found that matter

| Setting | Value on all bands | Effect |
|---|---|---|
| IGMP snooping (`wlX_igs`), EMF (`emf_enable`), multicast routing (`mr_enable_x`) | **0 (off)** | All multicast (mDNS, SSDP, IPv6) is flooded to every wireless client: matches the 20% mDNS air time measured |
| Multicast rate (`wlX_mrate_x`) | 0 (auto) | Broadcasts sent at a low basic rate |
| Roaming assistant (`wlX_user_rssi`) | **-70 dBm** | Clients below -70 dBm are disconnected ("disconnect weak signal strength station"); seen kicking the CrowPanel and a phone |
| Smart Connect (`smart_connect_x`) | 3 | Band steering on |
| Rate set (`wlX_rateset`) | ofdm | 802.11b rates already off |
| Width (`wlX_bw`), channel | 1 (auto), channel 0 (auto) | 2.4 GHz runs 20 MHz on ch 11; 5 GHz ch 100 80 MHz; 6 GHz ch 85 320 MHz |
| Guest networks | `MH-Guest` on 2.4 and 5 GHz (all units), 6 GHz guests off | Extra SSID per radio |
| Hidden SSIDs | `wl0/1/2_ssid` = 32-hex-char names, closed | AiMesh backhaul networks |

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
