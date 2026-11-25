"""The only commands the ASUS collector ever runs (read-only allow-list, D-04).

Each poll sends one shell script to a unit over SSH. Output is split into
sections by ``@@@ <key>`` marker lines. Nothing here writes state, restarts a
service or triggers a radio scan. Secrets are never read: there is no
``nvram show``; nvram is read key by key from the allow-lists below.
"""
from __future__ import annotations

IDENTITY_KEYS = ["productid", "odmpid", "firmver", "buildno", "extendno", "lan_ipaddr", "lan_netmask",
                 "sw_mode", "re_mode", "cfg_master", "wl_ifnames", "sta_ifnames", "lan_ifnames",
                 # Internet port and type: which counters measure internet traffic, and which connections are internet.
                 "wan0_ifname", "wan0_gw_ifname", "wan0_proto", "wan0_ipaddr"]

# Settings that matter for diagnosis (docs/05-asus-aimesh.md, "Settings found that matter").
_BAND_KEYS = ["ifname", "ssid", "closed", "chanspec", "channel", "bw", "mrate_x", "igs", "user_rssi", "rateset",
              "11ax", "11be", "txpower", "bss_enabled", "mfp"]
SETTINGS_KEYS = (
    [f"wl{b}_{k}" for b in range(4) for k in _BAND_KEYS]
    + [f"wl{b}.{g}_{k}" for b in range(4) for g in (1, 2, 3) for k in ("bss_enabled", "ssid", "closed", "ifname")]
    + ["smart_connect_x", "emf_enable", "mr_enable_x", "upnp_enable", "wps_enable", "misc_http_x", "dmz_ip",
       "vts_enable_x", "dhcp_start", "dhcp_end", "dhcp_lease", "wan0_proto", "wan0_state_t", "wan0_ipaddr",
       "sshd_enable", "sshd_port", "time_zone_x",
       # QoS (bufferbloat) and firmware-update state
       "qos_enable", "qos_type", "qos_obw", "qos_ibw", "bwdpi_db_enable", "webs_state_flag", "webs_state_info"]
)

TA_DB = "/jffs/.sys/TrafficAnalyzer/TrafficAnalyzer.db"   # ASUS Traffic Analyzer statistics (when the user enables it)
APSTATS_MAX = 48    # stations per Qualcomm unit per poll whose byte counters are read with apstats

TOOLS = ["wl", "iw", "iwpriv", "iwconfig", "mwctl", "hostapd_cli", "wlanconfig", "cfg80211tool", "apstats"]

LOG_PATTERN = ("hapdevent|roamast|ieee80211_node_leave|assoc failed with status"
               # radar (DFS) detections, forced channel changes, channel re-planning, deliberate restarts
               "|Radar found|Changing to DFS|trigger acs|current \\[reboot\\]"
               "|no address range available|DHCPNAK|nf_conntrack: table full|possible SYN flooding|Out of memory"
               "|oom-killer|segfault|Kernel panic|Unable to handle kernel|hung_task|BUG: ")


INET_FLOWS_AWK = (
    "awk -v w=\"$(nvram get wan0_ipaddr)\" '{ n=0; rd=\"\"; sp=\"\"; dp=\"\"; for (i=1; i<=NF; i++) { split($i, a, \"=\"); "
    "if (a[1]==\"src\") { n++; if (n==1) s=a[2] } else if (a[1]==\"dst\") { if (n==2) rd=a[2]; else d=a[2] } "
    "else if (a[1]==\"sport\" && n==1) sp=a[2]; else if (a[1]==\"dport\" && n==1) dp=a[2]; "
    "else if (a[1]==\"bytes\") { if (n==1) b1=a[2]; else b2=a[2] } } "
    "if (w != \"\" && rd==w) print $3\"|\"s\"|\"d\"|\"sp\"|\"dp\"|\"b1\"|\"b2 }' /proc/net/nf_conntrack 2>/dev/null"
)


def _nvram_block(key: str, keys: list[str]) -> str:
    gets = "; ".join(f'echo "{k}=$(nvram get {k} 2>/dev/null)"' for k in keys)
    return f'echo "@@@ {key}"; {gets}'


def unit_script(main: bool, first_poll: bool, full: bool) -> str:
    """Batch script for one unit. ``first_poll`` also reads the rotated log; ``full`` adds settings."""
    log_files = "/tmp/syslog.log-1 /tmp/syslog.log" if first_poll else "/tmp/syslog.log"
    lines = [
        _nvram_block("identity", IDENTITY_KEYS),
        'echo "@@@ tools"; for t in ' + " ".join(TOOLS) + '; do p=$(which $t 2>/dev/null); echo "$t: ${p:-MISSING}"; done',
        'echo "@@@ uptime"; cat /proc/uptime /proc/loadavg 2>/dev/null',
        # CPU time, memory, chip temperatures, connection tracking, storage (all read-only /proc and /sys files).
        "echo \"@@@ cpu_stat\"; grep '^cpu' /proc/stat",
        "echo \"@@@ meminfo\"; grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached):' /proc/meminfo",
        'echo "@@@ thermal"; for z in /sys/class/thermal/thermal_zone*; do echo "$(cat $z/type 2>/dev/null) $(cat $z/temp 2>/dev/null)"; done',
        'echo "@@@ conntrack"; cat /proc/sys/net/netfilter/nf_conntrack_count /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null',
        'echo "@@@ df"; df -k /jffs /tmp 2>/dev/null',
        # Per interface: link changes since start-up (carrier_changes), state and speed.
        'echo "@@@ links"; for i in /sys/class/net/*; do n=${i##*/}; echo "$n $(cat $i/carrier_changes 2>/dev/null) '
        '$(cat $i/operstate 2>/dev/null) $(cat $i/speed 2>/dev/null)"; done',
        'echo "@@@ iw_dev"; iw dev 2>/dev/null',
        'echo "@@@ iwconfig"; iwconfig 2>/dev/null',
        'echo "@@@ net_dev"; cat /proc/net/dev',
        f'echo "@@@ log"; cat {log_files} 2>/dev/null | grep -E "{LOG_PATTERN}" | tail -n 3000',
        # MediaTek: radio health per band, stations per SSID interface.
        'if which mwctl >/dev/null 2>&1; then for i in $(nvram get wl_ifnames); do '
        'echo "@@@ mwctl_stat:$i"; mwctl dev $i stat 2>/dev/null; done; fi',
        'if which hostapd_cli >/dev/null 2>&1; then for i in $(ls /sys/class/net | grep -E "^(ra|rai|rax)[0-9]+$"); do '
        'case $i in ra0|rai0|rax0) continue;; esac; echo "@@@ sta_hostapd:$i"; hostapd_cli -i $i all_sta 2>/dev/null; done; fi',
        # Older MediaTek firmware (no mwctl, no hostapd_cli, e.g. RT-AX57 Go): the driver's station table and
        # radio counters per radio. Display-only iwpriv commands; nothing is changed or scanned.
        'if ! which mwctl >/dev/null 2>&1 && ! which hostapd_cli >/dev/null 2>&1 && which iwpriv >/dev/null 2>&1; then '
        'for i in $(nvram get wl_ifnames); do case $i in ra*) echo "@@@ mactable:$i"; iwpriv $i get_mac_table 2>/dev/null '
        '| head -n 300; echo "@@@ iwpriv_stat:$i"; iwpriv $i stat 2>/dev/null | head -n 80;; esac; done; fi',
        # Qualcomm: stations per VAP.
        'if which wlanconfig >/dev/null 2>&1; then for i in $(ls /sys/class/net | grep -E "^ath[0-9]{3}$"); do '
        'echo "@@@ sta_wlanconfig:$i"; wlanconfig $i list sta 2>/dev/null; done; fi',
        # Qualcomm: data bytes per station (wlanconfig has none). Only for stations listed above, at most
        # APSTATS_MAX per poll, and only the byte/packet lines leave the unit. Display-only (D-34).
        'if which wlanconfig >/dev/null 2>&1 && which apstats >/dev/null 2>&1; then echo "@@@ apstats_sta"; n=0; '
        'for i in $(ls /sys/class/net | grep -E "^ath[0-9]{3}$"); do '
        "for m in $(wlanconfig $i list sta 2>/dev/null | awk 'NR>1 && $1 ~ /^([0-9a-f][0-9a-f]:){5}[0-9a-f][0-9a-f]$/ "
        "{print $1}'); do n=$((n+1)); [ $n -gt " + str(APSTATS_MAX) + " ] && break 2; apstats -s -m $m 2>/dev/null "
        '| grep -E "^(Node Level Stats|(Tx|Rx) Data (Bytes|Packets) +=)"; done; done; fi',
    ]
    if full:
        lines.append(_nvram_block("settings", SETTINGS_KEYS))
    if main:
        lines += [
            'echo "@@@ cfg_device_list"; nvram get cfg_device_list',
            'echo "@@@ clientlist"; cat /tmp/clientlist.json 2>/dev/null; echo',
            'echo "@@@ allwclientlist"; cat /tmp/allwclientlist.json 2>/dev/null; echo',
            'echo "@@@ wiredclientlist"; cat /tmp/wiredclientlist.json 2>/dev/null; echo',
            'echo "@@@ relist"; cat /tmp/relist.json 2>/dev/null; echo',
            # Which hardware address answers for each IP: a unit behind another unit's backhaul radio shows that radio.
            'echo "@@@ arp"; cat /proc/net/arp 2>/dev/null',
            # The mesh controller's own record of every node (address, model, firmware, online, signal) - it
            # includes nodes missing from cfg_device_list (e.g. ones whose settings sync is incomplete).
            'echo "@@@ cfgmnt"; cat /tmp/cfgmnt_log.txt 2>/dev/null; echo',
            # Internet connections and their byte counters (nf_conntrack accounting), for per-device internet use.
            # Only connections whose reply comes back to the WAN address; only "proto|src|dst|sport|dport|out|in"
            # lines leave the router. Read-only.
            'echo "@@@ inet_flows"; ' + INET_FLOWS_AWK,
        ]
        if full:
            lines += [
                'echo "@@@ nmp_clients"; cat /jffs/nmp_cl_json.js 2>/dev/null; echo',
                'echo "@@@ leases"; cat /var/lib/misc/dnsmasq.leases 2>/dev/null; echo',
                # Connections per local address, counted on the router (only counts leave the router).
                "echo \"@@@ conn_counts\"; awk '{ if (match($0, /src=[0-9.]+/)) c[substr($0, RSTART+4, RLENGTH-4)]++ } "
                "END { for (k in c) print c[k], k }' /proc/net/nf_conntrack 2>/dev/null",
                'for f in /tmp/[0-9A-F][0-9A-F]:*.json; do [ -f "$f" ] && { echo "@@@ node:$(basename $f .json)"; cat "$f"; echo; }; done',
                'echo "@@@ port_status"; cat /tmp/diag_port_status.json 2>/dev/null; echo',
                # Results of speed tests already run on the router (ASUS app / web UI). Read-only.
                'echo "@@@ ookla_history"; cat /jffs/.sys/OOKLA/ookla_history 2>/dev/null; echo',
                # ASUS Traffic Analyzer statistics, only if the user has turned the feature on (MeshLens never does).
                # Read-only query of its database on the router; only hourly totals per device and app leave it.
                f'if [ -f {TA_DB} ] && which sqlite3 >/dev/null 2>&1; then echo "@@@ ta_schema"; '
                f'sqlite3 -readonly {TA_DB} "PRAGMA table_info(traffic);" 2>&1 | head -n 20; echo "@@@ ta_traffic"; '
                f'sqlite3 -readonly -separator "|" {TA_DB} "SELECT mac, app_name, cat_name, (timestamp / 3600) * 3600, '
                "SUM(tx), SUM(rx) FROM traffic WHERE timestamp >= strftime('%s', 'now') - 93600 GROUP BY 1, 2, 3, 4;\" "
                '2>&1 | head -n 6000; fi',
            ]
    return "\n".join(lines) + "\n"
