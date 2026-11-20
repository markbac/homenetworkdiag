# Changelog

## 0.9.2 — 20 November 2026

- **Lower CPU use**: the analysis is about five times faster (new indexes for band history and interface counters; errors
  per port read from the first and last sample instead of a day of rows), runs one at a time with callers sharing the result,
  and is kept for a minute (the tray icon reuses one up to 5 minutes old). The Graphs tab reads only the counters it draws and
  is cached for a minute. The first start after updating builds two indexes, which takes a few seconds.
- **Clickable addresses in issues**: a MAC in an issue opens that device, or the Router page when it is one of your own units;
  an address never seen on your network explains whether it is private (randomised) or maker-assigned.
- **Probe alerts recognise your own units**: disconnect floods sent by one of your mesh units are marked low and explained as
  steering or roaming (or a device that keeps talking to a unit it is no longer joined to), not an attack.
- **Click a graph to enlarge it** in a pop-up (Esc or a click outside closes it).

## 0.9.1 — 19 November 2026

- **Guidance in the app**: every issue links to step-by-step instructions for the ASUS web UI and app (fixed channel, 2.4 GHz
  channel, Smart Connect rule, binding a device, PMF, firmware, removing a loop, Ethernet backhaul, roaming assistant, IGMP
  snooping, QoS, turning off a Pi's Wi-Fi, watching devices), says how to check the fix worked, and links to the page to act on.
- **Glossary** in Help (backhaul, band steering, BSS load, bufferbloat, DFS, dBm, kick, loop, PMF, PMKID, powerline, probe, roam).
- **Issue list**: repeated issues for the same device are grouped (×N, with the times); issues are marked **new** for their first
  day and listed as **resolved** once not seen for a day; a loading indicator while the analysis runs.
- **Mark a change** (Overview) with a before/after comparison (Settings → Changes you made) and markers on every graph.
- **Setup checklist** (Settings, and on the Overview while anything is left).
- **Hidden issues** list in Settings, with "Show this again".
- **Key devices**: servers, switches, hubs and powerline adapters that aren't watched are suggested (one click to watch them all).
- Help for the device page's "Where it was connected" (unit and band) graph; change markers on every graph.
- Fixed: pages wider than the screen on phones when a settings table had long values.

## 0.9.0 — 19 November 2026

- Dual-band MediaTek nodes (RT-AX57 Go): chip detection without `mwctl`, bands per unit (`rax` = 5 GHz there), interfaces from
  `iwconfig`, station table via `iwpriv get_mac_table` (unverified format, raw output kept), backhaul traffic per band.
- Channel plan for 5 GHz blocks and 2.4 GHz 1/6/11; radar incidents with devices dropped and pushed to 2.4 GHz.
- New issues: network loop behind powerline, stuck on 2.4 GHz, failed joins (status 53 / 30), dual-homed host, PMF and
  firmware mismatch, speed path changed, outdoor wander, mesh-wide restart, suspect name, channel plan, duplicate unit name.
- Hide an issue per device / unit; "Show hidden".
- ESP32 probes (c5-probe API 0.2.0): discovery, pairing, room, access points, air monitor, alerts, environment; Probes page.

## 0.8.1 and earlier

See the GitHub releases.
