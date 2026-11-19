"use strict";
// Router tab: everything each mesh unit reports, and "This PC" (air survey, wired broadcast/multicast, Wi-Fi link).

let routerSel = null;

function fmtUptime(s) {
  if (s == null) return "–";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}
const pctClass = (v, warn, bad) => v == null ? "" : v >= bad ? "bad" : v >= warn ? "warn" : "good";
const tbl = (head, rows, cls = "") => `<div class="table-scroll short"><table class="${cls}"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("") || `<tr><td colspan="${head.length}" class="muted">Nothing reported.</td></tr>`}</tbody></table></div>`;

async function loadRouter() {
  const { units } = await refreshBase();
  const order = [...units].sort((a, b) => (a.role === "router" ? -1 : b.role === "router" ? 1 : (a.alias || "").localeCompare(b.alias || "")));
  if (!routerSel) routerSel = order.length ? order[0].mac : "pc";
  $("#router-nav").innerHTML = order.map((u) => `<button data-unit="${esc(u.mac)}" class="${u.mac === routerSel ? "on" : ""}">
      ${u.role === "router" ? "◎" : "◉"} ${esc(u.alias || u.model || u.mac)}${u.reachable ? "" : ' <span class="bad small">●</span>'}</button>`).join("")
    + `<button data-unit="pc" class="${routerSel === "pc" ? "on" : ""}">🖥 This PC</button>`
    + `<button data-unit="probes" class="${routerSel === "probes" ? "on" : ""}">📡 Probes</button>`;
  if (routerSel === "pc") return renderHostPc();
  if (routerSel === "probes") return renderProbes();
  return renderUnit(routerSel);
}

async function renderUnit(mac) {
  const [d, host] = await Promise.all([api(`/api/units/${encodeURIComponent(mac)}/detail`), api("/api/hostpc").catch(() => ({ scan: [] }))]);
  const plan = d.unit.role === "router" ? await api("/api/channels").catch(() => null) : null;
  const u = d.unit, id = d.identity || {};
  const air = (host.scan || []).filter((b) => b.own_unit === mac && b.util_pct != null);
  const unreachable = !u.reachable;
  $("#router-body").innerHTML = `
  <div class="card"><h3>${esc(u.alias || u.model)} <span class="muted small">${esc(u.role === "router" ? "main router" : "mesh node")}</span>
      <button class="help" data-help="router-tab">?</button></h3>
    ${unreachable ? `<p class="bad">MeshLens can't read this unit: ${esc(u.last_error || "no answer")}. What's shown comes from the main router.</p>` : ""}
    <div class="kv kv-wide">
      <div>Model</div><div>${esc(u.model)} ${id.productid ? `<span class="muted">(${esc(id.productid)})</span>` : ""}</div>
      <div>Firmware</div><div>${esc(u.firmware || "–")}</div>
      <div>Address</div><div>${esc(u.ip || "–")} · <span class="mono">${esc(u.mac)}</span></div>
      <div>Wi-Fi chip family</div><div>${esc(u.chip || "–")}</div>
      <div>Up for</div><div>${esc(fmtUptime(d.uptime_s))}${d.uptime_s != null && d.uptime_s < 86400 ? ' <span class="warn">(restarted recently)</span>' : ""}</div>
      <div>Load (1/5/15 min)</div><div>${d.load ? esc(d.load.join(" / ")) : "–"}</div>
      <div>Last read</div><div>${esc(ago(d.snapshot_ts || u.last_seen))}</div>
      <div>Backhaul</div><div>${d.backhaul.length ? d.backhaul.map((b) => `${esc(b.band)} GHz to ${esc(b.parent_name)} <span class="${rssiClass(b.rssi)}">${esc(b.rssi ?? "?")} dBm</span>`).join(" · ") : (u.role === "router" ? "– (main router)" : "none seen over Wi-Fi (cabled, or via a unit MeshLens can't read)")}</div>
    </div></div>

  ${systemHtml(d)}
  ${backhaulHtml(d)}

  <div class="card"><h3>Radios and networks</h3>
    ${tbl(["Interface", "Network", "Band", "Channel", "Width", "Power", "Devices", "Traffic now", "Noise", "Tx failures", "Rx errors", "Temp"],
      d.ifaces.filter((i) => i.type === "AP" || i.ssid || i.iface.startsWith("apcli")).map((i) => `<tr class="${i.role === "backhaul" ? "muted" : ""}">
        <td class="mono">${esc(i.iface)}</td><td>${esc(i.role === "backhaul" ? "mesh backhaul" + (i.ssid ? "" : " (client)") : i.ssid || "")}</td>
        <td>${esc(i.band ? i.band + " GHz" : "")}</td><td>${esc(i.channel ?? "")}</td><td>${i.width ? esc(i.width) + " MHz" : ""}</td>
        <td>${i.txpower_dbm != null ? esc(i.txpower_dbm) + " dBm" : ""}</td><td>${i.clients || ""}</td>
        <td class="small">${i.rx_mbps != null ? `↓${esc(i.tx_mbps)} ↑${esc(i.rx_mbps)} Mbit/s` : ""}</td>
        <td>${i.noise != null ? esc(i.noise) + " dBm" : ""}</td>
        <td class="${pctClass(i.tx_fail_pct, 5, 10)}">${i.tx_fail_pct != null ? esc(i.tx_fail_pct) + "%" : ""}</td>
        <td class="${pctClass(i.rx_crc_pct, 5, 10)}">${i.rx_crc_pct != null ? esc(i.rx_crc_pct) + "%" : ""}</td>
        <td>${i.temperature != null ? esc(i.temperature) + " °C" : ""}</td></tr>`))}
    <p class="muted small">Grey rows are the hidden mesh backhaul networks. Tx failures, Rx errors and temperature are for the whole radio (shown on its first interface), counted since the unit started.</p>
    ${air.length ? `<h4>How busy its channels are (its own report, heard by this PC)</h4>
      ${tbl(["Network", "Band", "Channel", "Busy", "Devices", "Heard at"], air.map((b) => `<tr><td>${esc(b.ssid || "(hidden)")}</td><td>${esc(b.band)} GHz</td><td>${esc(b.channel)}</td>
        <td><span class="bar"><i class="${pctClass(b.util_pct, 50, 75)}" style="width:${Math.min(100, b.util_pct)}%"></i></span> ${esc(b.util_pct)}%</td>
        <td>${esc(b.stations ?? "")}</td><td class="${rssiClass(b.rssi)}">${esc(b.rssi)} dBm</td></tr>`))}` : ""}
  </div>

  ${plan ? channelPlanHtml(plan) : ""}

  <div class="grid">
    <div class="card"><h3>Ports</h3>
      ${tbl(["Port", "Link", "Speed", "Max", "Link changes*"], d.ports.map((p) => `<tr><td>${esc(p.port)}</td>
        <td class="${p.up ? "good" : "muted"}">${p.up ? "connected" : "not connected"}</td>
        <td class="${p.below_max ? "warn" : ""}">${p.link_mbps ? esc(p.link_mbps >= 1000 ? p.link_mbps / 1000 + " Gbit/s" : p.link_mbps + " Mbit/s") : ""}</td>
        <td class="muted">${esc(p.max_mbps >= 1000 ? p.max_mbps / 1000 + " Gbit/s" : p.max_mbps + " Mbit/s")}</td>
        <td class="${((d.links[p.ifname] || {}).changes || 0) > 2 ? "warn" : ""}">${esc((d.links[p.ifname] || {}).changes ?? "")}</td></tr>`))}
      ${d.wired.length ? `<h4>Wired interface errors</h4>${tbl(["Interface", "Errors (24 h)", "Drops (24 h)", "Errors since start", "Link changes*"],
        d.wired.map((w) => `<tr><td class="mono">${esc(w.iface)}</td><td class="${w.rx_errs_24h + w.tx_errs_24h ? "warn" : ""}">${esc(w.rx_errs_24h + w.tx_errs_24h)}</td>
          <td>${esc(w.rx_drop_24h + w.tx_drop_24h)}</td><td class="muted">${esc((w.rx_errs || 0) + (w.tx_errs || 0))}</td><td>${esc(w.carrier_changes ?? "")}</td></tr>`))}` : ""}
      <p class="muted small">* Times the link went down/up since the unit started.</p>
      <p class="muted small">Amber speed = the link runs below what the port can do (cable, or the other end is slower).</p></div>
    <div class="card"><h3>Devices on this unit now (${d.clients.length})</h3>
      ${tbl(["Device", "Band", "Wi-Fi", "Signal", "Rate"], d.clients.map((c) => `<tr data-mac="${esc(c.mac)}" class="clickrow"><td>${esc(c.name)}</td>
        <td>${esc(c.band ? c.band + " GHz" : "")}</td><td class="small">${esc(wifiLabel(c.wifi_gen, c.nss))}</td>
        <td class="${rssiClass(c.rssi)}">${esc(c.rssi ?? "")}</td><td>${esc(c.tx_rate ?? "")}</td></tr>`), "clickable")}</div>
  </div>
  ${d.dhcp || (d.conn_top && d.conn_top.length) ? `<div class="grid">
    ${d.dhcp ? `<div class="card"><h3>Addresses (DHCP)</h3><div class="kv kv-wide">
      <div>Pool</div><div>${esc(d.dhcp.start)} – ${esc(d.dhcp.end)} (${esc(d.dhcp.size)} addresses)</div>
      <div>In use</div><div class="${pctClass(d.dhcp.pct, 75, 90)}">${esc(d.dhcp.leases)} (${esc(d.dhcp.pct)}%)</div></div>
      <p class="muted small">When the pool fills, new devices can't join. Phones using private addresses can take more than one.</p></div>` : ""}
    ${d.conn_top && d.conn_top.length ? `<div class="card"><h3>Most connections <span class="muted small">${esc(d.conn_total)} tracked in total</span></h3>
      ${tbl(["Device", "Address", "Connections"], d.conn_top.slice(0, 10).map((c) => `<tr ${c.mac ? `data-mac="${esc(c.mac)}" class="clickrow"` : ""}><td>${esc(c.name)}</td>
        <td class="mono small">${esc(c.ip)}</td><td>${esc(c.count)}</td></tr>`))}</div>` : ""}
  </div>` : ""}

  ${d.settings ? settingsHtml(d.settings) : `<div class="card"><h3>Settings</h3><p class="muted">${u.role === "router" ? "Read at the next full poll (every 15 minutes)." : "Read at the next full poll."}</p></div>`}

  <div class="card"><details><summary><b>Raw identity values</b> <span class="muted small">(as reported by the unit)</span></summary>
    <div class="table-scroll short"><table>${Object.entries(id).map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td class="mono wrap">${esc(v)}</td></tr>`).join("")}</table></div></details></div>`;
  drawSystemCharts(d);
  drawBackhaul(d);
}

function backhaulHtml(d) {
  const b = d.backhaul_traffic;
  if (!b || !Object.keys(b.series || {}).length) return "";
  const tot = Object.entries(b.totals_gb || {}).map(([k, v]) => `${esc(k)}: <b>${esc(v)} GB</b>`).join(" · ");
  return `<div class="card"><h3>Backhaul traffic, last 24 h <span class="muted small">this unit's own link to the mesh, per band</span>
      <button class="help" data-help="backhaul-traffic">?</button></h3>
    <div id="bh-chart" class="chart-box"></div>
    <p class="muted small">Total: ${tot}. Both directions added together. Units with hardware packet offload can leave some traffic out of these counters, so treat the numbers as a minimum.</p></div>`;
}

function drawBackhaul(d) {
  const el = $("#bh-chart");
  if (!el) return;
  Charts.time(el, { series: d.backhaul_traffic.series, type: "line", unit: "Mbit/s", yMin: 0, height: 180,
    empty: "Appears after a few polls." });
}

function channelPlanHtml(p) {
  const r5 = (p.recommend || {})["5"] || {}, r24 = (p.recommend || {})["2.4"] || {};
  const cur = p.current || {};
  const busy = (v) => v != null ? `<span class="bar"><i class="${pctClass(v, 50, 75)}" style="width:${Math.min(100, v)}%"></i></span> ${esc(v)}%` : "–";
  const radar = (p.radar || {}).incidents || [];
  const five = tbl(["Block", "", "Strong neighbours", "All neighbours", "Neighbours busy", "Radar (30 d)", "Score"],
    (p.five || []).map((f) => `<tr class="${f.block === r5.block ? "good-row" : ""}"><td><b>${esc(f.block)}</b>${f.current ? ' <span class="pill">now</span>' : ""}${f.block === r5.block ? ' <span class="pill good">best</span>' : ""}</td>
      <td class="muted small">${esc(f.note)}</td><td class="${f.strong_neighbours >= 3 ? "warn" : ""}" title="${esc((f.neighbour_names || []).join(", "))}">${esc(f.strong_neighbours)}</td>
      <td>${esc(f.neighbours)}</td><td>${busy(f.busy_pct)}${f.own_busy_pct != null ? ` <span class="muted small">yours ${esc(f.own_busy_pct)}%</span>` : ""}</td>
      <td class="${f.radar_hits ? "bad" : ""}">${f.radar_hits ? esc(f.radar_hits) + "× · " + esc(ago(f.last_radar)) : "–"}</td><td class="muted">${esc(f.score)}</td></tr>`));
  const two = tbl(["Channel", "Strong neighbours", "Overlapping neighbours", "Neighbours busy", "Score"],
    (p.two4 || []).map((c) => `<tr><td><b>${esc(c.channel)}</b>${c.current ? ' <span class="pill">now</span>' : ""}${c.channel === r24.channel ? ' <span class="pill good">best</span>' : ""}</td>
      <td class="${c.strong_neighbours >= 3 ? "warn" : ""}" title="${esc((c.neighbour_names || []).join(", "))}">${esc(c.strong_neighbours)}</td><td>${esc(c.neighbours)}</td>
      <td>${busy(c.busy_pct)}${c.own_busy_pct != null ? ` <span class="muted small">yours ${esc(c.own_busy_pct)}%</span>` : ""}</td><td class="muted">${esc(c.score)}</td></tr>`));
  const inc = radar.length ? tbl(["When", "Heard by", "Channel", "Moved to", "Devices dropped", "Fell to 2.4 GHz", "Still on 2.4 GHz"],
    radar.slice().reverse().map((i) => `<tr><td>${esc(new Date(i.ts * 1000).toLocaleString())}</td><td>${esc(i.detected_by.join(", "))}</td>
      <td>${esc(i.channels.join(", "))} <span class="muted small">(${esc(i.blocks.join(", "))})</span></td><td>${esc((i.moved_to || []).join(", ") || "–")}</td>
      <td class="${i.devices_dropped >= 4 ? "warn" : ""}">${esc(i.devices_dropped)}</td>
      <td>${(i.pushed_to_24 || []).map((m) => esc(m.name)).join(", ") || "–"}</td>
      <td class="${(i.still_on_24 || []).length ? "bad" : ""}">${(i.still_on_24 || []).map((m) => esc(m.name)).join(", ") || "–"}</td></tr>`)) : `<p class="muted small">No radar detected in the last 30 days.</p>`;
  const single = (p.radar || {}).single_detector;
  return `<div class="card"><h3>Channel plan <span class="muted small">which channels to use, from the air survey and radar history</span>
      <button class="help" data-help="channel-plan">?</button></h3>
    <div class="kv kv-wide">
      <div>5 GHz now</div><div>${cur["5"] ? `channel ${esc(cur["5"].channel)}${cur["5"].width ? `, ${esc(cur["5"].width)} MHz` : ""}` : "–"}</div>
      <div>5 GHz advice</div><div class="${r5.change ? "warn" : "good"}">${esc(r5.text || "–")}</div>
      <div>2.4 GHz now</div><div>${cur["2.4"] ? `channel ${esc(cur["2.4"].channel)}` : "–"}</div>
      <div>2.4 GHz advice</div><div class="${r24.change ? "warn" : "good"}">${esc(r24.text || "–")}</div>
      <div>Based on</div><div>${esc(p.access_points_heard)} access points heard by this PC (${esc(p.neighbours_heard)} neighbours), last survey ${esc(ago(p.last_scan))}</div>
    </div>
    <h4>5 GHz blocks (80 MHz)</h4>${five}
    <h4>2.4 GHz channels</h4>${two}
    <h4>Radar events</h4>${single ? `<p class="muted small">Every detection came from <b>${esc(single)}</b>. If no other unit ever hears it, it may be a false trigger near that unit.</p>` : ""}${inc}
    <p class="muted small">Neighbours are counted from what this PC's Wi-Fi card hears, so they reflect where this PC is. "Strong" means louder than -70 dBm.
      Change channels on the main router (Wireless → General); the nodes follow it.</p></div>`;
}

function wifiLabel(gen, nss) {
  if (!gen) return "";
  return gen + (nss ? ` · ${nss}×${nss}` : "");
}

function systemHtml(d) {
  const s = d.system || {};
  if (!Object.keys(s).length) return "";
  const v = (x, unit = "") => (x == null ? "–" : `${x}${unit}`);
  const ct = s.conntrack != null && s.conntrack_max ? `${s.conntrack} of ${s.conntrack_max} (${(100 * s.conntrack / s.conntrack_max).toFixed(1)}%)` : "–";
  return `<div class="card"><h3>System <span class="muted small">processor, memory and storage of the unit</span></h3>
    <div class="sys-tiles">
      <div><div class="big ${pctClass(s.cpu_pct, 60, 85)}">${v(s.cpu_pct, "%")}</div><div class="muted small">CPU busy${s.cores ? ` (${s.cores.length} cores)` : ""}</div></div>
      <div><div class="big ${pctClass(s.max_core_pct, 80, 95)}">${v(s.max_core_pct, "%")}</div><div class="muted small">busiest core</div></div>
      <div><div class="big ${pctClass(s.mem_used_pct, 75, 90)}">${v(s.mem_used_pct, "%")}</div><div class="muted small">memory used (${v(s.mem_avail_mb)} of ${v(s.mem_total_mb)} MB free)</div></div>
      <div><div class="big ${pctClass(s.temp_c, 75, 85)}">${v(s.temp_c, " °C")}</div><div class="muted small">hottest chip sensor</div></div>
      <div><div class="big">${v(s.load1)}</div><div class="muted small">load (1 min)</div></div>
    </div>
    <div class="kv kv-wide"><div>Per core</div><div>${s.cores ? s.cores.map((c, i) => `core ${i}: <span class="${pctClass(c, 80, 95)}">${c}%</span>`).join(" · ") : "–"}${s.softirq_pct != null ? ` <span class="muted small">(network packet work ${s.softirq_pct}% of all CPU time)</span>` : ""}</div>
      <div>Connections tracked</div><div>${ct}</div>
      <div>Storage</div><div>settings (/jffs) ${v(s.jffs_pct, "%")} · temporary (/tmp) ${v(s.tmp_pct, "%")}</div></div>
    <div class="grid"><div><h4>CPU, last 24 h</h4><div id="sys-cpu" class="chart-box"></div></div>
      <div><h4>Memory and temperature, last 24 h</h4><div id="sys-mem" class="chart-box"></div></div></div></div>`;
}

function drawSystemCharts(d) {
  const h = d.system_history || {};
  if (!$("#sys-cpu")) return;
  Charts.time($("#sys-cpu"), { series: { "CPU": h.cpu_pct || [], "Busiest core": h.max_core_pct || [] }, type: "line", unit: "%", yMin: 0, yMax: 100,
    height: 180, empty: "Appears after a few router polls." });
  Charts.time($("#sys-mem"), { series: { "Memory used %": h.mem_used_pct || [], "Temperature °C": h.temp_c || [] }, type: "line", yMin: 0, yMax: 100,
    height: 180, empty: "Appears after a few router polls." });
}

function settingsHtml(s) {
  const bandTable = `<div class="table-scroll short"><table><thead><tr><th>Setting</th>${s.bands.map((b) => `<th>${esc(b)}</th>`).join("")}<th>What it means</th></tr></thead><tbody>
    ${s.band_rows.map((r) => `<tr><td>${esc(r.label)}</td>${s.bands.map((b) => `<td>${esc(r.values[b])}</td>`).join("")}<td class="muted small wrap">${esc(r.meaning)}</td></tr>`).join("")}</tbody></table></div>`;
  const list = (rows) => `<div class="table-scroll short"><table><tbody>${rows.map((r) => `<tr><td>${esc(r.label)}</td><td><b>${esc(r.value)}</b></td><td class="muted small wrap">${esc(r.meaning)}</td></tr>`).join("")}</tbody></table></div>`;
  return `<div class="card"><h3>Radio settings <span class="muted small">read-only: change these in the ASUS app or web UI</span></h3>
    <p class="muted small">These are each radio's own settings. On AiMesh firmware the radio's first network is usually the hidden mesh backhaul;
      your own networks are listed under <i>Other networks</i>.</p>${bandTable}
    ${s.networks.length ? `<h4>Other networks</h4>${tbl(["Name", "Band", "Enabled", "Hidden", "Interface"], s.networks.map((n) => `<tr><td>${esc(n.ssid)}</td><td>${esc(n.band)}</td><td>${n.enabled ? "yes" : "no"}</td><td>${n.hidden ? "yes" : "no"}</td><td class="mono">${esc(n.iface || "")}</td></tr>`))}` : ""}</div>
    <div class="grid"><div class="card"><h3>Network</h3>${list(s.network)}</div><div class="card"><h3>Administration and security</h3>${list(s.admin)}</div></div>
    <div class="card"><details><summary><b>All settings MeshLens reads</b> <span class="muted small">(${Object.keys(s.raw).length} values)</span></summary>
      <div class="table-scroll short"><table>${Object.entries(s.raw).map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td class="mono wrap">${esc(v)}</td></tr>`).join("")}</table></div></details></div>`;
}

async function renderHostPc() {
  const [h, plan] = await Promise.all([api("/api/hostpc"), api("/api/channels").catch(() => null)]);
  const l = h.link;
  const bands = {};
  (h.scan || []).forEach((b) => { const k = `${b.band} GHz ch ${b.channel}`; (bands[k] ||= { own: 0, other: 0, util: null }); b.own_unit ? bands[k].own++ : bands[k].other++;
    if (b.util_pct != null) bands[k].util = Math.max(bands[k].util ?? 0, b.util_pct); });
  $("#router-body").innerHTML = `
  <div class="card"><h3>This PC as a network sensor <button class="help" data-help="hostpc">?</button></h3>
    <p class="muted small">MeshLens uses this PC's own Wi-Fi card and network adapter to measure things the routers don't report: how busy the
      air is (each access point's own channel-utilisation report, and the neighbours it can hear) and broadcast/multicast floods on the wired network.
      ${h.enabled ? "" : '<b class="warn">Turned off in Settings → This PC.</b>'}</p>
    <div class="kv kv-wide"><div>Wi-Fi card</div><div>${l ? esc(l.adapter || "") + (l.connected ? ` · connected to <b>${esc(l.ssid)}</b> on ${esc(l.band)} GHz ch ${esc(l.channel)}, <span class="${rssiClass(l.rssi)}">${esc(l.rssi)} dBm</span>, ${esc(l.rx_mbps)}/${esc(l.tx_mbps)} Mbit/s` : " · not connected (still surveys the air)") : "no Wi-Fi card"}</div>
      <div>Last air survey</div><div>${esc(ago(h.last_scan))}</div></div></div>
  <div class="card"><h3>Wired adapter traffic</h3>
    ${tbl(["Adapter", "Download", "Upload", "Broadcast", "Multicast", "Unicast", "Discards", "Errors"], (h.adapters || []).map((a) => `<tr><td>${esc(a.adapter)}</td>
      <td>${esc(a.rx_mbps)} Mbit/s</td><td>${esc(a.tx_mbps)} Mbit/s</td>
      <td class="${pctClass(a.bcast_pps, 30, 100)}">${esc(a.bcast_pps)} /s</td><td class="${pctClass(a.mcast_pps, 50, 150)}">${esc(a.mcast_pps)} /s</td>
      <td>${esc(a.ucast_pps)} /s</td><td>${esc(a.discards)}</td><td class="${a.errors ? "bad" : ""}">${esc(a.errors)}</td></tr>`))}
    <p class="muted small">Packets per second received over the last minute. A quiet home network is under ~30 broadcast/s; hundreds point at a flooding device.</p></div>
  ${plan ? channelPlanHtml(plan) : ""}
  <div class="card"><h3>Channels in use</h3>
    ${tbl(["Channel", "Your networks", "Neighbours", "Busiest report"], Object.entries(bands).sort().map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.own}</td><td class="${v.other >= 3 ? "warn" : ""}">${v.other}</td>
      <td>${v.util != null ? `<span class="bar"><i class="${pctClass(v.util, 50, 75)}" style="width:${v.util}%"></i></span> ${v.util}%` : "–"}</td></tr>`))}</div>
  <div class="card"><h3>Every access point this PC hears (${(h.scan || []).length})</h3>
    ${tbl(["Network", "Access point", "Whose", "Band", "Ch", "Signal", "Busy", "Devices", "Standard"], (h.scan || []).map((b) => `<tr class="${b.own_unit ? "" : "muted"}">
      <td>${esc(b.ssid || "(hidden)")}</td><td class="mono small">${esc(b.bssid)}</td><td>${b.own_unit ? esc(b.unit_name || "yours") : "neighbour"}</td>
      <td>${esc(b.band)} GHz</td><td>${esc(b.channel)}</td><td class="${rssiClass(b.rssi)}">${esc(b.rssi)} dBm</td>
      <td>${b.util_pct != null ? `<span class="bar"><i class="${pctClass(b.util_pct, 50, 75)}" style="width:${b.util_pct}%"></i></span> ${b.util_pct}%` : "–"}</td>
      <td>${esc(b.stations ?? "")}</td><td class="small">${esc(b.radio || "")}</td></tr>`))}
    <p class="muted small">"Busy" is the access point's own measurement of how much of the time its channel is in use (BSS Load); not every access point reports it.</p></div>`;
}

// ---------------------------------------------------------------- ESP32 probes
const fmtBytes = (b) => b == null ? "" : b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : b >= 1e6 ? (b / 1e6).toFixed(1) + " MB" : b >= 1e3 ? Math.round(b / 1e3) + " kB" : b + " B";
const probePairing = {};

async function renderProbes() {
  const [list, st] = await Promise.all([api("/api/probes"), api("/api/status")]);
  const ps = st.probes || {};
  $("#router-body").innerHTML = `
  <div class="card"><h3>ESP32 probes <span class="muted small">radio sensors placed around the house</span> <button class="help" data-help="probes">?</button></h3>
    <p class="muted small">Probes announce themselves on the network and MeshLens finds them by itself.
      ${ps.listening ? "Listening for announcements." : `<span class="warn">${esc(ps.error || "Not listening for announcements yet.")}</span>`}
      If one doesn't appear, enter its address (shown on the probe under SETUP → ABOUT).</p>
    <form id="probe-add" class="inline"><input id="probe-ip" placeholder="Probe address, e.g. 192.168.1.50" inputmode="decimal">
      <button>Look for it</button> <span id="probe-add-msg" class="small"></span></form></div>
  ${list.length ? list.map(probeCard).join("") : `<div class="card"><p class="muted">No probes found yet. Switch one on and connect it to your Wi-Fi (on the probe: SETUP → WI-FI).</p></div>`}`;
}

function probeCard(p) {
  const l = p.link || {}, env = p.env || {};
  const state = !p.adopted ? '<span class="pill warn">not paired</span>' : p.paired ? '<span class="pill good">paired</span>' : '<span class="pill bad">paired elsewhere</span>';
  const actions = !p.adopted
    ? (probePairing[p.id] ? `<form class="inline" data-probe-confirm="${esc(p.id)}"><input name="code" placeholder="6-digit code on the probe" maxlength="6" inputmode="numeric" autocomplete="off">
        <button>Confirm</button></form> <span class="muted small">The code stays on the probe's screen for 5 minutes.</span>`
       : `<button data-probe-action="pair" data-probe="${esc(p.id)}">Pair</button> <span class="muted small">Shows a code on the probe's screen.</span>`)
    : p.paired ? `<form class="inline" data-probe-place="${esc(p.id)}"><input name="room" placeholder="Room" value="${esc(p.room || "")}" maxlength="39">
        <input name="spot" placeholder="Spot (optional)" value="${esc(p.spot || "")}" maxlength="39"><button>Save room</button></form>`
    : `<span class="muted small">It is paired with another MeshLens. On the probe: SETUP → PROBE → Forget, then pair it here.</span>`;
  const units = p.units_heard || [];
  const temp = env.temp_c != null ? `${Number(env.temp_c).toFixed(1)} °C` : "–";
  return `<div class="card"><h3>${esc(p.room || p.id)} ${p.room ? `<span class="muted small">${esc(p.id)}</span>` : ""} ${state}
      <span class="${p.online ? "good" : "muted"} small">${p.online ? "● online" : "○ last heard " + esc(ago(p.last_seen))}</span></h3>
    <div class="kv kv-wide">
      <div>Probe</div><div>${esc(p.chip || "ESP32")} · firmware ${esc(p.firmware || "?")} · ${esc(p.ip || "")}${p.mode ? ` · radio mode ${esc(p.mode)}` : ""}</div>
      <div>Its Wi-Fi</div><div>${l.connected ? `${esc(l.ssid)} on ${esc(l.band)} GHz ch ${esc(l.channel)}, <span class="${rssiClass(l.rssi)}">${esc(l.rssi)} dBm</span>` : "–"}</div>
      <div>Environment</div><div>${temp}${env.rh != null ? ` · ${esc(Math.round(env.rh))}% RH` : ""}${env.battery_v != null ? ` · battery ${esc(env.battery_v)} V${env.battery_v > 4.3 ? " (on USB)" : ""}` : ""}</div>
      <div>Last scan</div><div>${esc(ago(p.last_scan))} · ${esc(p.aps_heard)} access points heard (${esc(p.neighbours["2.4"])} neighbours on 2.4 GHz, ${esc(p.neighbours["5"])} on 5 GHz)</div>
      ${p.last_error ? `<div>Problem</div><div class="warn">${esc(p.last_error)}</div>` : ""}
    </div>
    <p>${actions} · <button class="linkish" data-probe-action="forget" data-probe="${esc(p.id)}">Forget</button></p>
    <div class="grid">
      <div><h4>Your mesh as heard here</h4>${tbl(["Unit", "Band", "Ch", "Signal", "PMF"], units.map((u) => `<tr><td>${esc(u.unit)}</td><td>${esc(u.band)} GHz</td>
        <td>${esc(u.channel ?? "")}</td><td class="${rssiClass(u.rssi)}">${esc(u.rssi)} dBm</td><td>${esc(u.pmf || "")}</td></tr>`))}
        <p class="muted small">The strongest unit here is the one devices in this spot should use. PMF is read from each unit's beacons.</p></div>
      <div><h4>Busiest devices on its channel (last hour)</h4>${tbl(["Device", "Via", "Data", "Signal"], (p.top_talkers || []).map((t) => `<tr ${devicesByMac[t.mac] ? `data-mac="${esc(t.mac)}" class="clickrow"` : ""}>
        <td>${esc(t.name)}</td><td>${esc(t.via || "")}</td><td>${esc(fmtBytes(t.bytes))}</td><td class="${rssiClass(t.rssi || null)}">${t.rssi ? esc(t.rssi) + " dBm" : ""}</td></tr>`), "clickable")}
        <p class="muted small">Heard by the probe's air monitor on the channel it is joined to; payloads are encrypted and never read.</p></div>
    </div>
    ${(p.alerts || []).length ? `<h4>Alerts (7 days)</h4>${tbl(["When", "Severity", "What"], p.alerts.map((a) => `<tr><td>${esc(new Date(a.ts * 1000).toLocaleString())}</td>
      <td class="${a.severity === "danger" ? "bad" : a.severity === "warn" ? "warn" : "muted"}">${esc(a.severity)}</td><td class="wrap">${esc(a.text)}</td></tr>`))}` : ""}
  </div>`;
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-probe-action]");
  if (!b) return;
  e.preventDefault();
  const id = b.dataset.probe, act = b.dataset.probeAction;
  if (act === "forget" && !confirm(`Forget probe ${id} in MeshLens? (To unpair the probe itself, use Forget on its screen.)`)) return;
  try {
    await post(`/api/probes/${encodeURIComponent(id)}/${act}`, {});
    if (act === "pair") probePairing[id] = true;
  } catch (err) { alert(err.message); }
  renderProbes();
});
document.addEventListener("submit", async (e) => {
  const f = e.target;
  if (f.id === "probe-add") {
    e.preventDefault();
    $("#probe-add-msg").textContent = "Looking…";
    try { const r = await post("/api/probes/add", { ip: $("#probe-ip").value }); $("#probe-add-msg").textContent = `Found ${r.id}.`; renderProbes(); }
    catch (err) { $("#probe-add-msg").textContent = err.message; }
  } else if (f.dataset.probeConfirm) {
    e.preventDefault();
    const id = f.dataset.probeConfirm;
    try { await post(`/api/probes/${encodeURIComponent(id)}/confirm`, { code: f.code.value.trim() }); delete probePairing[id]; renderProbes(); }
    catch (err) { alert(err.message); }
  } else if (f.dataset.probePlace) {
    e.preventDefault();
    try { await post(`/api/probes/${encodeURIComponent(f.dataset.probePlace)}/placement`, { room: f.room.value, spot: f.spot.value }); renderProbes(); }
    catch (err) { alert(err.message); }
  }
});

LOADERS.router = () => loadRouter();
document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-unit]");
  if (b) { routerSel = b.dataset.unit; loadRouter(); }
});
