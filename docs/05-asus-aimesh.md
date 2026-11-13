# ASUS AiMesh data

The router knows things no other source can: which node and band each client is on, its link rate, 6 GHz clients, roaming and DHCP. The tool reads this **read-only over SSH**, using commands already proven in `homelab-unified` (`discovery/router_ssh.py`, `discovery/node_ssh.py`).

## This network

| Unit | Model | Role | Clients (app) | Backhaul |
|---|---|---|---|---|
| Main | ZenWiFi BT8 | Router, `10.0.10.1` | 33 | Wired to internet |
| 1F | ZenWiFi BT8 | Node | 11 | Wireless, MLO |
| 2F | ZenWiFi BD4 | Node | 2 | Wireless via 1F |
| Outdoor | ZenWiFi BD4 Outdoor | Node | 4 | Wireless |
| (unnamed) | ZenWiFi BD4 | Node | 0 | Shown grey (offline?) |

55 devices in total. SSIDs: `MH-Pvt`, `MH-Pvt_IoT`, `MH-Guest`, plus hidden AiMesh networks. All 2.4 GHz radios on channel 11. BT8 is tri-band (2.4 / 5 / 6 GHz), BD4 dual-band (2.4 / 5 GHz).

## Prerequisites

- SSH enabled on the router (Administration > System > Enable SSH, LAN only). Nodes inherit SSH and the same admin credentials.
- One admin account; the tool stores the password with Windows DPAPI / Credential Manager (ASUS-07).

## Data to collect

### Main router

| Data | Command / source | Used for |
|---|---|---|
| Client list with names, band, node | `nvram get clientlist`, `nvram get custom_clientlist`, `/jffs/nmp_cl_json.js` (where present) | Device names, band, which node |
| DHCP leases | `cat /var/lib/misc/dnsmasq.leases` | IP, hostname, lease time |
| ARP / neighbours | `arp -a` or `ip neigh show` | IP-MAC mapping, wired devices |
| Radios | `wl -i <if> status`, `wl -i <if> chanspec`, `wl -i <if> noise` | Channel, width, noise per band |
| Associated clients | `wl -i <if> assoclist`, `wl -i <if> sta_info <mac>` | Signal, tx/rx rate, idle, connected time, retries per client |
| Mesh nodes | `nvram get cfg_device_list` | Node list, IPs, models |
| Interface counters | `cat /proc/net/dev` | Traffic per interface |
| Bridge forwarding | `bridge fdb show` | Which port/node a MAC is behind (wired vs wireless) |
| System | `/proc/uptime`, `/proc/loadavg`, `/proc/meminfo`, `/proc/stat` | Router health |
| Roams / disconnects | `logread` filtered for assoc / disassoc / deauth / roam | Roam events with reasons |
| WAN events | `logread` filtered for WAN / PPPoE / link up/down | Internet drop-outs |
| Connections | `/proc/net/nf_conntrack` (head) | Busiest devices by connections (optional) |
| Settings | `nvram get <key>` for an allow-listed set | See below |

### Each node

Same `wl` radio and client commands, `bridge fdb`, `/proc` health, and backhaul link quality to its parent (signal and rate of the backhaul interface).

### Settings worth reading (ASUS-04)

Read and explain, never change (first release):

| Setting | Why it matters |
|---|---|
| Channel and width per band (`wl*_chanspec`, `wl*_bw`) | 40 MHz on 2.4 GHz hurts; all nodes share the 2.4 GHz channel in AiMesh |
| Multicast rate (`wl*_mrate_x`) | Broadcasts/multicasts are sent at this rate to everyone; a low rate makes them very expensive in air time |
| IGMP snooping (`emf_enable`, `wl*_igs`) | Stops multicast being flooded to every device |
| Guest networks per node | Each SSID on each node adds beacons and broadcast copies |
| Smart Connect / band steering | Whether capable clients are pushed to 5/6 GHz |
| Legacy 802.11b rates | Slow devices drag everyone down |
| Airtime fairness, roaming assistant thresholds | Behaviour of slow / distant clients |
| AiMesh backhaul preference | Wired vs wireless backhaul per node |

The exact nvram key names vary by firmware; the collector should record what it finds and the documentation should list the verified keys for BT8 / BD4.

## Safety

- Only commands on an allow-list are ever run; no writes (`nvram set`, `service`, `reboot`) in the first release.
- Commands have short timeouts and are run sequentially per unit to keep load low.
- Raw outputs are stored (redacted of passwords/keys found in nvram) as test fixtures for the parsers (NFR-08).
