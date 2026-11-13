# Goals and scope

## The problem

Someone in the house says "the Wi-Fi is laggy". The usual tools give partial answers:

- A **speed test** says the broadband is fine.
- The **router app** lists 55 devices and some signal bars.
- A **Wi-Fi analyser** on a phone shows networks and channels, but not what is using the air.

None of them say *why* a game stutters at 8 pm in one bedroom. Measurements on this network showed the real cause was invisible to all three: one device sending ~800 broadcasts a second, which the mesh repeated to every wireless device at a slow rate, using over half of the 2.4 GHz air time (see [Findings](07-findings.md)).

## Goals

1. **Explain lag and drop-outs in plain language**, with evidence, and say what to change (router setting, device, placement, band, cable).
2. **Know every device on the network**: name, type, vendor, IP, MAC, wired or wireless, which mesh node and band, signal, how much it talks.
3. **See the radio**: channel use, air time by category and by device, broadcasts, retries, neighbours, per room and per band (2.4 and 5 GHz), using ESP32 probes.
4. **Watch over time**: run for hours (an evening of gaming) and line up lag spikes against what was happening on the network and in the air at that moment.
5. **Read the ASUS AiMesh system** for the facts only the router knows: client-to-node mapping, band, link rate, roaming, DHCP, radio configuration.
6. **Run on Windows** without Docker or WSL, as an ordinary user where possible.

## Users

- **The household admin** (primary): technical, comfortable with config files and a browser dashboard, wants detail and raw data (JSON/CSV) as well as summaries.
- **Everyone else** (secondary): needs a one-line answer ("Xbox is on the weak 2.4 GHz link to the outdoor node") and a simple test they can run.

## In scope

- Discovery and identification of devices on the local IPv4 network (and IPv6 neighbours where visible).
- Read-only collection from ASUS Asuswrt / AiMesh routers and nodes.
- ESP32 probes for 2.4 GHz and 5 GHz radio analysis, controlled by the Windows tool.
- Latency, jitter, loss, DNS and throughput tests; long-running monitors; event detection.
- Diagnosis rules that turn measurements into findings and recommendations.
- Local web dashboard, reports, and machine-readable logs.

## Out of scope (for now)

- **Changing router settings automatically.** The tool recommends; the user changes settings. (May be revisited: see [open questions](08-open-questions.md).)
- Packet capture of payloads or decryption of traffic. Probes read frame headers only.
- Non-ASUS routers (design should not prevent adding them later).
- Internet-facing access, multi-site, accounts.
- Mobile apps.

## Success criteria

- Given a "laggy" complaint, the tool identifies the dominant cause on this network in under 10 minutes of measurement, with the evidence (charts, numbers, device names).
- Every device in the ASUS client list is matched to a name, vendor and type, or flagged as unknown.
- A 4-hour evening monitor produces a timeline in which each lag spike can be lined up with radio, router and device activity in the same minute.
