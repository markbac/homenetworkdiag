# Open questions

All questions from the first round are answered; see [decisions](10-decisions.md) D-01 to D-22.

## Remaining

1. **Product name.** The repository stays `homenetworkdiag`. Candidates for the app's name:
   - **MeshLens**: looks into the mesh (router, nodes, air) - short, descriptive. *(suggested)*
   - **LagHunter**: says what it is for, friendlier for the family.
   - **AirTrace**: emphasises the radio side.
   - Keep **homenetworkdiag**.
2. **Pi-hole and Home Assistant:** confirm they should be added as optional sources (D-22), and in which phase.
3. **Probe screens:** which pages a stand-alone probe should show first (proposal: live signal, quick test, air survey summary, monitor status, placement).
4. **Reports:** which sections a shareable report must contain (proposal: summary findings, evidence charts, device table with names optional/anonymised for sharing).

## Decided recently (for reference)

| Topic | Answer |
|---|---|
| Language | Python, reuse `homelab-unified` |
| How it runs | Tray app, always-on mini PC, admin available |
| Relationship | Separate tool; `homelab-unified` later consumes its API |
| Probe firmware | ESP-IDF where required |
| Probes | Up to 6, moved around, Wi-Fi only, stand-alone with screens, may channel-hop, USB-C power banks |
| Router | SSH port 1111, stock firmware, tool may hold the password, read-only forever |
| Network | Everything on `10.0.10.0/23` |
| Fingbox / Domotz | Removed from the network |
| Watched devices | Chosen in the web UI |
| Dashboard | LAN access from phones/laptops with login |
| Alerts | None for now |
| Reports | Yes |
| Retention | 1 month |
