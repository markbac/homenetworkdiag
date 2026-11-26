"use strict";
// Printable network report: everything MeshLens knows on one long page, laid out for A4 so the browser's
// "Save as PDF" makes a tidy document. Uses Charts (charts.js) and MapView.svgFor (map.js). No editing here.

const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString([], { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
const ago = (ts) => { if (!ts) return "never"; const s = Date.now() / 1000 - ts; return s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`; };
const dur = (s) => s == null ? "–" : s < 3600 ? `${Math.round(s / 60)} min` : s < 172800 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} days`;
const rssiClass = (r) => r == null ? "" : r >= -60 ? "good" : r >= -70 ? "" : r >= -78 ? "warn" : "bad";
const scoreClass = (s) => s == null ? "muted" : s >= 80 ? "good" : s >= 60 ? "warn" : "bad";
const pctClass = (p) => p == null ? "" : p > 10 ? "bad" : p > 5 ? "warn" : "";
const SEV = { high: "High", medium: "Medium", low: "Low", info: "Info" };
const PERIOD = { 24: "last 24 hours", 168: "last 7 days", 720: "last 30 days" };

async function api(path) {
  const res = await fetch(path, { credentials: "same-origin" });
  if (res.status === 401) throw Object.assign(new Error("login required"), { login: true });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const soft = (p, dflt) => api(p).catch(() => dflt);

// ---------------------------------------------------------------- names (same rules as the dashboard)
let units = [], devsByMac = {}, unitByAddr = {}, unitByTail = {};
function indexUnits() {
  unitByAddr = {}; unitByTail = {};
  for (const u of units) for (const m of [u.mac, ...(u.macs || [])]) { unitByAddr[m] = u; unitByTail[m.slice(3)] = u; }
}
const unitOf = (mac) => mac ? (unitByAddr[mac] || unitByTail[mac.slice(3)]) : null;
const unitName = (mac) => { const u = unitOf(mac); return u ? (u.alias || u.model || u.mac) : (mac || ""); };
function osLabel(h) { h = (h || "").toLowerCase(); return h.includes("msft") ? "Windows PC" : h.includes("android") ? "Android device" : (h.includes("dhcpcd") || h.includes("udhcp")) ? "Linux device" : null; }
function displayName(d, mac) {
  if (!d) return mac;
  if (d.user_name || d.name) return d.user_name || d.name;
  const v = (d.vendor || "").split(",")[0].replace(/ Inc\.?$/i, "").trim(), o = osLabel(d.os_hint);
  return o && v ? `${o} (${v})` : (o || v) ? `${o || v} ${(d.mac || mac).slice(-5)}` : (d.mac || mac);
}
const devName = (mac) => displayName(devsByMac[mac], mac);
const MAC_RE = /\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b/g;
/** Addresses in text get the unit's or device's name beside them. */
const named = (text) => esc(text).replace(MAC_RE, (raw) => {
  const m = raw.toLowerCase(), u = unitOf(m), d = devsByMac[m];
  const n = u ? (u.alias || u.model) : d ? displayName(d, m) : null;
  return n ? `<b>${esc(n)}</b> <span class="mono small">${raw}</span>` : `<span class="mono">${raw}</span>`;
});

function groupIssues(list) {
  const by = new Map();
  for (const i of list) {
    const k = `${i.rule}|${i.subject}`;
    if (!by.has(k)) by.set(k, { ...i, times: [] });
    by.get(k).times.push([i.from, i.to]);
  }
  return [...by.values()];
}

const table = (head, rows, cls = "") => rows.length
  ? `<table class="rp-table ${cls}"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.join("")}</tr>`).join("")}</tbody></table>`
  : '<p class="muted">None.</p>';
const td = (v, cls = "") => `<td${cls ? ` class="${cls}"` : ""}>${v ?? ""}</td>`;
const kv = (pairs) => `<div class="kv kv-wide">${pairs.filter((p) => p[1] != null && p[1] !== "").map(([k, v]) => `<div>${esc(k)}</div><div>${v}</div>`).join("")}</div>`;

// ---------------------------------------------------------------- sections
function cover(st, a, top, chk, speeds, hours) {
  const n = a.network, c = n.issue_counts, nodes = top.nodes || [];
  const online = nodes.filter((x) => x.kind === "device");
  const wifi = online.filter((x) => (x.link || {}).type === "wifi").length;
  const sp = speeds[0];
  const todo = chk.filter((x) => x.done === false);
  return `<section class="rp-cover">
    <div class="rp-title"><span class="logo">&#9678;</span> MeshLens network report</div>
    <p class="muted">Generated ${esc(fmtTime(Date.now() / 1000))} · covering the ${esc(PERIOD[hours] || hours + " h")} · MeshLens ${esc(st.version || "")}</p>
    <div class="health-card rp-health"><div class="score ${scoreClass(n.score)}">${esc(n.score ?? "–")}</div>
      <div><h3>Network health</h3><p>${esc(n.summary)}</p>
      <p>${["high", "medium", "low", "info"].map((k) => `<span class="sev ${k}">${esc(c[k])} ${SEV[k].toLowerCase()}</span>`).join(" ")}
        <span class="muted small">${esc(n.devices_poor)} of ${esc(n.devices_seen)} Wi-Fi devices in poor shape${n.hidden ? ` · ${esc(n.hidden)} issues hidden` : ""}</span></p></div></div>
    <div class="rp-stats">
      <div><b>${units.length}</b><span>mesh units</span></div>
      <div><b>${online.length}</b><span>devices online</span></div>
      <div><b>${wifi}</b><span>on Wi-Fi</span></div>
      <div><b>${online.length - wifi}</b><span>wired</span></div>
      <div><b>${Object.keys(devsByMac).length}</b><span>devices ever seen</span></div>
      ${sp ? `<div><b>${esc(Math.round(sp.down_mbps))}/${esc(Math.round(sp.up_mbps))}</b><span>Mbit/s last speed test (${esc(ago(sp.ts))})</span></div>` : ""}
    </div>
    <h3>Contents</h3>
    <ol class="rp-toc"><li>Issues</li><li>Router and mesh units</li><li>Network diagram</li><li>Graphs</li><li>Devices</li>
      <li>Stability, speed tests and changes</li><li>Powerline and probes</li></ol>
    ${todo.length ? `<h3>Setup not finished</h3><ul class="small">${todo.map((x) => `<li>${esc(x.label)} <span class="muted">${esc(x.detail || "")}</span></li>`).join("")}</ul>` : ""}
  </section>`;
}

function issuesSection(a, low) {
  const want = low ? ["high", "medium", "low", "info"] : ["high", "medium"];
  const list = groupIssues(a.issues.filter((i) => !i.hidden && want.includes(i.severity)));
  const order = { high: 0, medium: 1, low: 2, info: 3 };
  list.sort((x, y) => order[x.severity] - order[y.severity] || (y.to || 0) - (x.to || 0));
  const subj = (i) => i.scope === "device" && i.subject ? ` · ${esc(devName(i.subject))}` : i.scope === "unit" && i.subject ? ` · ${esc(unitName(i.subject))}` : "";
  return `<section class="rp-section"><h2>1. Issues <span class="muted small">${low ? "all severities" : "high and medium"}</span></h2>
    ${list.length ? list.map((i) => `<div class="rp-issue ${esc(i.severity)}">
      <div><span class="sev ${esc(i.severity)}">${esc(SEV[i.severity] || i.severity)}</span> <b>${named(i.title)}</b>
        ${i.times.length > 1 ? `<span class="pill">×${i.times.length}</span>` : ""} ${i.status === "new" ? '<span class="pill new">new</span>' : ""}</div>
      <p class="small muted">${esc(fmtTime(i.from))}${i.to && i.to !== i.from ? " – " + esc(fmtTime(i.to)) : ""}${subj(i)}</p>
      <p><span class="lbl">Seen</span> ${named(i.evidence)}</p>
      <p><span class="lbl">Why</span> ${esc(i.cause)}</p>
      ${i.fixes && i.fixes.length ? `<div><span class="lbl">Try</span><ol>${i.fixes.map((f) => `<li>${esc(f)}</li>`).join("")}</ol></div>` : ""}
    </div>`).join("") : '<p class="muted">Nothing needs attention in this period.</p>'}
  </section>`;
}

function unitSection(details, a, radios) {
  const parts = details.map(({ u, d }) => {
    const h = (a.units || {})[u.mac] || {};
    const fw = d && d.identity ? `${d.identity.firmver || ""}.${d.identity.buildno || ""}_${(d.identity.extendno || "").split("-")[0]}` : (u.firmware || "").split("-")[0];
    // One row per band and use (the ASUS units have several interfaces per band: guest networks, backhaul, ...).
    const ifaces = Object.values(((d && d.ifaces) || []).reduce((acc, f) => {
      const k = `${f.band}|${f.role || f.type}|${f.channel}|${f.width}`;
      const r = acc[k] || (acc[k] = { ...f, names: [], clients: 0 });
      r.names.push(f.iface); r.clients += f.clients || 0;
      return acc;
    }, {})).sort((x, y) => String(x.band).localeCompare(String(y.band)) || String(x.role).localeCompare(String(y.role)));
    const rad = (d && d.radios && d.radios.length) ? d.radios : radios.filter((r) => r.unit === u.mac);
    const links = Object.entries((d && d.links) || {}).filter(([n, l]) => l.speed != null && !n.includes("."))
      .map(([n, l]) => [td(esc(n)), td(esc(l.state), l.state === "up" ? "good" : "muted"), td(esc(l.speed) + " Mbit/s", l.speed && l.speed < 1000 ? "warn" : ""), td(esc(l.changes))]);
    const bh = (d && d.backhaul) || u.backhaul || [];
    return `<div class="card rp-unit"><h3>${esc(u.alias || u.model || u.mac)} <span class="muted small">${esc(u.role === "router" ? "router" : "mesh node")}</span>
        ${h.score != null ? `<span class="score small-score ${scoreClass(h.score)}">${esc(h.score)}</span>` : ""}</h3>
      ${kv([["Model", esc(u.model)], ["IP address", esc(u.ip)], ["MAC", `<span class="mono">${esc(u.mac)}</span>`], ["Firmware", esc(fw)],
        ["Chip", esc(u.chip)], ["Up for", esc(dur(d && d.uptime_s))], ["Load", esc(((d && d.load) || []).join(" / "))],
        ["Status", u.reachable ? '<span class="good">reachable</span>' : `<span class="bad">${esc(u.last_error || "not reachable")}</span>`],
        ["Last read", esc(ago(u.last_seen))],
        ["Backhaul", bh.length ? esc(bh.map((b) => [b.type || b.kind, b.band ? b.band + " GHz" : "", b.rssi != null ? b.rssi + " dBm" : "", b.parent ? "to " + unitName(b.parent) : ""].filter(Boolean).join(" ")).join("; ")) : ""],
        ["Health notes", esc((h.notes || h.reasons || []).join("; "))]])}
      ${ifaces.length ? `<h4>Wi-Fi networks</h4>${table(["Band", "Use", "Channel", "Width", "Devices", "Noise", "Interfaces"],
        ifaces.map((f) => [td(f.band ? esc(f.band) + " GHz" : ""), td(esc(f.role || f.type)), td(esc(f.channel)),
          td(f.width ? esc(f.width) + " MHz" : ""), td(esc(f.clients)), td(f.noise != null ? esc(f.noise) + " dBm" : ""),
          td(esc(f.names.join(", ")), "small muted")]), "compact")}` : ""}
      ${rad.length ? `<h4>Radio health <span class="muted small">since the unit started</span></h4>${table(["Band", "Interface", "Tx failures", "Rx errors", "Temperature", "Noise"],
        rad.map((r) => [td(r.band ? esc(r.band) + " GHz" : ""), td(esc(r.iface)), td(r.tx_fail_pct != null ? esc(r.tx_fail_pct) + "%" : "", pctClass(r.tx_fail_pct)),
          td(r.rx_crc_pct != null ? esc(r.rx_crc_pct) + "%" : "", pctClass(r.rx_crc_pct)), td(r.temperature != null ? esc(Math.round(r.temperature)) + " °C" : ""),
          td(r.noise != null ? esc(r.noise) + " dBm" : "")]), "compact")}` : ""}
      ${links.length ? `<h4>Wired ports</h4>${table(["Port", "State", "Speed", "Link changes"], links, "compact")}` : ""}
    </div>`;
  });
  return `<section class="rp-section"><h2>2. Router and mesh units</h2>${parts.join("")}</section>`;
}

function mapSection(t, expand) {
  const svg = MapView.svgFor(t, { expand });
  return `<section class="rp-section rp-wide"><h2>3. Network diagram <span class="muted small">built ${esc(fmtTime(t.generated))}</span></h2>
    ${svg ? `<div class="rp-map">${svg}</div>
    <p class="small muted">Dots are devices, coloured by signal: <span class="good">●</span> good (−60 dBm or better), <span style="color:var(--c5)">●</span> fair,
      <span class="warn">●</span> weak (below −70), <span class="bad">●</span> poor (below −78), <span class="muted">●</span> wired. A red ring means device health below 60.
      Lines show how each unit connects; the label gives the band and signal or wired speed.</p>` : '<p class="muted">No topology yet.</p>'}
  </section>`;
}

const GRAPHS = [
  ["clients", "Devices connected, by unit"], ["bands", "Devices by band"], ["events", "Wi-Fi events"], ["wan", "Internet traffic"],
  ["wifi", "Wi-Fi traffic per radio"], ["latency", "Latency"], ["loss", "Packet loss"], ["heat", "Latency by hour of day"],
  ["rssi", "Signal of devices now"], ["radio", "Radio errors"], ["air", "How busy the air is (this PC's Wi-Fi survey)"],
  ["lan", "LAN broadcast noise"], ["ucpu", "Unit CPU (busiest core)"], ["umem", "Unit memory"], ["utemp", "Unit temperature"], ["plc", "Powerline speed"],
  ["switch", "Traffic inside the house, per switch port"]];
const size = (b) => { if (b == null) return "–"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (b >= 1000 && i < u.length - 1) { b /= 1000; i++; } return `${b >= 100 || i === 0 ? Math.round(b) : b.toFixed(1)} ${u[i]}`; };

function graphsSkeleton() {
  return `<section class="rp-section"><h2>4. Graphs</h2><div class="rp-graphs">${GRAPHS.map(([k, label]) =>
    `<div class="rp-graph"><h4>${esc(label)}</h4><div id="rg-${k}" class="chart-box"></div></div>`).join("")}</div>
    <p id="rg-wan-note" class="muted small"></p><div id="rg-top"></div></section>`;
}

function drawGraphs(g) {
  const C = Charts, el = (k) => $(`#rg-${k}`), H = 190;
  C.time(el("clients"), { series: g.clients.by_unit, type: "area", stacked: true, unit: "devices", height: H });
  C.time(el("bands"), { series: g.clients.by_band, type: "line", unit: "devices", height: H });
  C.time(el("events"), { series: g.events, type: "bars", stacked: true, height: H,
    colors: { Kicks: "--bad", "Roam attempts": "--c2", Disconnects: "--warn", "Failed joins": "--c6" } });
  C.time(el("wan"), { series: g.traffic.wan, type: "area", unit: "Mbit/s", height: H, colors: { Download: "--c1", Upload: "--c3" } });
  C.time(el("wifi"), { series: g.traffic.wifi, type: "line", unit: "Mbit/s", height: H });
  C.time(el("latency"), { series: g.latency.avg, type: "line", unit: "ms", height: H, refLines: [{ y: 100, label: "100 ms" }] });
  C.time(el("loss"), { series: { "Packet loss": g.latency.loss_pct }, type: "bars", unit: "%", height: H, colors: { "Packet loss": "--bad" }, empty: "No packet loss recorded." });
  const hm = g.heatmap, HEAT = (ms) => ms < 25 ? "--good" : ms < 50 ? "--c5" : ms < 100 ? "--warn" : "--bad";
  C.heatmap(el("heat"), { rows: hm.days, cols: 24, colLabels: [...Array(24).keys()].map((h) => `${h}:00`),
    cell: (r, c) => { const v = hm.grid[r][c]; return { v: v.p95, tip: "" }; }, scale: (v) => C.css(HEAT(v)) });
  C.bars(el("rssi"), g.rssi.map((b) => ({ label: `${b.from} to ${b.to} dBm`, short: `${b.from}`, value: b.count,
    color: b.from >= -60 ? "--good" : b.from >= -70 ? "--c5" : b.from >= -78 ? "--warn" : "--bad" })), { height: H, empty: "No Wi-Fi devices connected." });
  C.time(el("radio"), { series: g.radio_errors, type: "line", unit: "%", height: H, refLines: [{ y: 10, label: "10%" }] });
  C.time(el("air"), { series: g.air, type: "line", unit: "%", yMin: 0, yMax: 100, height: H, empty: "No survey from this PC." });
  C.time(el("lan"), { series: g.lan_noise, type: "line", unit: "/s", height: H });
  const U = g.units || {};
  C.time(el("ucpu"), { series: U.core || {}, type: "line", unit: "%", yMin: 0, yMax: 100, height: H });
  C.time(el("umem"), { series: U.mem || {}, type: "line", unit: "%", yMin: 0, yMax: 100, height: H });
  C.time(el("utemp"), { series: U.temp || {}, type: "line", unit: "°C", height: H });
  C.time(el("plc"), { series: g.powerline, type: "line", unit: "Mbit/s", height: H, empty: "No powerline adapters read in this period." });
  const sw = g.switches || {};
  C.time(el("switch"), { series: sw.series || {}, type: "line", unit: sw.unit || "Mbit/s", height: H, gap: 900, legend: true });
  $("#rg-wan-note").textContent = g.traffic.wan_note ? `Internet traffic: ${g.traffic.wan_note}` : "";
  const t = g.top_users || {};
  $("#rg-top").innerHTML = (t.rows || []).length ? `<h3>Top internet users <span class="muted small">estimate, from the router's connection table</span></h3>
    <table class="rp-table compact"><thead><tr><th>Device</th><th>Address</th><th>Download</th><th>Upload</th><th>Share</th></tr></thead><tbody>
    ${t.rows.map((r) => `<tr><td>${esc(r.name)}</td><td class="mono">${esc(r.ip || "")}</td><td>${size(r.down_bytes)}</td><td>${size(r.up_bytes)}</td><td>${r.share_pct ?? "–"}%</td></tr>`).join("")}
    </tbody></table><p class="muted small">${t.devices} devices, ${size(t.total_bytes)} in total. Connections that open and close within one poll are missed.</p>` : "";
  // Graphs with nothing to show are left out of the printout, so the PDF has no empty boxes.
  document.querySelectorAll(".rp-graph").forEach((b) => b.classList.toggle("rp-empty", !b.querySelector("svg")));
}

function devicesSection(devs, a, which) {
  const now = Date.now() / 1000;
  const isOnline = (d) => d.live || (d.wired && now - d.last_seen < 900) || (d.last_alive && now - d.last_alive < 1800);
  const keep = devs.filter((d) => which === "all" || (which === "online" ? isOnline(d) : now - (d.last_seen || 0) < 7 * 86400));
  const where = (d) => { const l = d.live || {}, u = l.unit || d.unit;
    return d.wired ? (u ? `wired · ${unitName(u)}` : "wired") : (l.band || d.band) ? `${l.band || d.band} GHz · ${unitName(u)}` : "LAN (found by scan)"; };
  const groups = {};
  keep.forEach((d) => { const u = (d.live || {}).unit || d.unit, k = u ? unitName(u) : "Other / not via the mesh"; (groups[k] = groups[k] || []).push(d); });
  const order = Object.keys(groups).sort((x, y) => groups[y].length - groups[x].length);
  const label = { online: "online now", all: "all ever seen", 7: "seen in the last 7 days" }[which];
  return `<section class="rp-section"><h2>5. Devices <span class="muted small">${keep.length} ${esc(label)}, grouped by the unit they use</span></h2>
    ${order.map((k) => `<h3>${esc(k)} <span class="muted small">${groups[k].length}</span></h3>` + table(
      ["Device", "Type", "IP", "MAC", "Maker", "Connection", "Signal", "Rate tx/rx", "Wi-Fi", "Health", "Last seen"],
      groups[k].sort((x, y) => displayName(x, x.mac).localeCompare(displayName(y, y.mac))).map((d) => { const l = d.live || {};
        return [td(`${d.watched ? "★ " : ""}${esc(displayName(d, d.mac))}${d.randomised ? ' <span class="pill">private MAC</span>' : ""}`),
          td(esc(d.user_type || d.dtype), "muted"), td(esc(d.ip)), td(esc(d.mac), "mono"), td(esc((d.vendor || "").split(",")[0]), "small"),
          td(esc(where(d)), "small"), td(l.rssi != null ? esc(l.rssi) : "", rssiClass(l.rssi)), td(l.tx_rate != null ? esc(`${l.tx_rate}/${l.rx_rate ?? "?"}`) : ""),
          td(esc(d.wifi_gen ? d.wifi_gen + (d.wifi_nss ? ` ${d.wifi_nss}×${d.wifi_nss}` : "") : ""), "small"),
          td(esc(d.health ?? ""), scoreClass(d.health)), td(isOnline(d) ? '<span class="good">online</span>' : esc(ago(d.last_seen)), "small")]; }), "compact rp-devs")).join("")}
  </section>`;
}

function historySection(stab, a, speeds, changes, hours) {
  const since = Date.now() / 1000 - hours * 3600;
  const ch = changes.filter((c) => c.ts >= since);
  return `<section class="rp-section"><h2>6. Stability, speed tests and changes</h2>
    <h3>Least stable devices</h3>
    ${table(["Device", "Health", "Kicks", "Roam attempts", "Disconnects", "Weakest signal", "Units used"],
      stab.slice(0, 20).map((s) => { const h = (a.devices || {})[s.mac] || {};
        return [td(esc(devName(s.mac))), td(esc(h.score ?? "–"), scoreClass(h.score)), td(esc(s.kicks), s.kicks ? "bad" : ""), td(esc(s.roam_attempts)),
          td(esc(s.disconnects)), td(esc(s.weakest_rssi ?? ""), rssiClass(s.weakest_rssi)), td(esc(s.units.map(unitName).join(", ")), "small")]; }), "compact")}
    <h3>Speed tests</h3>
    ${table(["When", "From", "Download", "Upload", "Idle latency", "Under load", "Grade", "Server"],
      speeds.slice(0, 15).map((s) => [td(esc(fmtTime(s.ts)), "small"), td(esc(s.location || s.trigger), "small"),
        td(s.error ? `<span class="bad">${esc(s.error)}</span>` : esc(s.down_mbps) + " Mbit/s"), td(s.error ? "" : esc(s.up_mbps) + " Mbit/s"),
        td(s.idle_ms != null ? esc(s.idle_ms) + " ms" : ""), td(s.down_loaded_ms != null ? esc(s.down_loaded_ms) + " ms" : ""), td(esc(s.grade)),
        td(esc((s.server || "").slice(0, 60)), "small muted")]), "compact")}
    <h3>Changes in this period</h3>
    ${table(["When", "Change", "Effect"], ch.map((c) => [td(esc(fmtTime(c.ts)), "small"), td(esc(c.note), "wrap"),
      td(c.impact && c.impact.summary ? esc(c.impact.summary) : "", "wrap small")]), "compact")}
  </section>`;
}

function extrasSection(plc, probes) {
  const ad = (plc && plc.adapters) || [];
  return `<section class="rp-section"><h2>7. Powerline and probes</h2>
    <h3>Powerline adapters</h3>
    ${ad.length ? table(["Adapter", "Model", "Address", "Firmware", "Devices behind it"], ad.map((x) => [td(esc(x.name) + (x.attached_to_router ? ' <span class="pill">wired to router</span>' : "")),
      td(esc(x.product)), td(esc(x.ip)), td(esc((x.firmware || "").split("_")[0]), "small muted"), td(x.attached_to_router ? "whole network" : esc((x.bridged || []).length))]), "compact")
      + "<h4>Links (PHY rate; real throughput is roughly half)</h4>" + table(["Between", "One way", "Other way", "Slowest"], (plc.links || []).map((l) =>
        [td(`${esc(l.a_name)} ↔ ${esc(l.b_name)}`), td(esc(l.a_to_b) + " Mbit/s"), td(esc(l.b_to_a) + " Mbit/s"), td(esc(l.slowest) + " Mbit/s", l.slowest < 20 ? "bad" : l.slowest < 50 ? "warn" : "good")]), "compact")
      : '<p class="muted">No powerline adapters read.</p>'}
    <h3>Radio probes</h3>
    ${table(["Probe", "Room", "State", "Its Wi-Fi", "Environment", "Last scan"], probes.map((p) => { const l = p.link || {}, env = p.env || {};
      return [td(esc(p.id) + `<div class="small muted">${esc(p.chip || "ESP32")} · fw ${esc(p.firmware || "?")} · ${esc(p.ip || "")}</div>`), td(esc([p.room, p.spot].filter(Boolean).join(", "))),
        td(p.online ? '<span class="good">online</span>' : "last heard " + esc(ago(p.last_seen))),
        td(l.connected ? `${esc(l.band)} GHz ch ${esc(l.channel)}, <span class="${rssiClass(l.rssi)}">${esc(l.rssi)} dBm</span>` : "–"),
        td(`${env.temp_c != null ? Number(env.temp_c).toFixed(1) + " °C" : "–"}${env.rh != null ? ` · ${Math.round(env.rh)}% RH` : ""}`),
        td(`${esc(ago(p.last_scan))}${p.aps_heard != null ? ` · ${esc(p.aps_heard)} APs heard` : ""}`, "small")]; }), "compact")}
  </section>`;
}

// ---------------------------------------------------------------- build
async function build() {
  const hours = +$("#rp-hours").value, which = $("#rp-devs").value;
  $("#rp-print").disabled = true;
  $("#rp-status").textContent = "Collecting everything… (the 30-day period takes a little longer)";
  try {
    const [st, a, top, devs, us, radios, chk, speeds, stab, changes, plc, probes, g] = await Promise.all([
      api("/api/status"), api(`/api/analysis?hours=${hours}`), api("/api/topology"), api("/api/devices"), api("/api/units"),
      soft("/api/radios", []), soft("/api/checklist", []), soft(`/api/speedtests?days=${Math.max(30, Math.ceil(hours / 24))}`, []),
      soft(`/api/stability?hours=${hours}`, []), soft("/api/changes", []), soft("/api/powerline", null), soft("/api/probes", []),
      api(`/api/graphs?hours=${hours}`)]);
    units = us; indexUnits();
    devsByMac = Object.fromEntries(devs.map((d) => [d.mac, d]));
    window.MESHLENS_MARKS = changes.filter((c) => c.ts >= Date.now() / 1000 - hours * 3600).map((c) => ({ t: c.ts, label: c.note }));
    const details = await Promise.all(units.map(async (u) => ({ u, d: await soft(`/api/units/${encodeURIComponent(u.mac)}/detail`, null) })));
    speeds.sort((x, y) => y.ts - x.ts);
    $("#report").innerHTML = cover(st, a, top, chk, speeds, hours) + issuesSection(a, $("#rp-low").checked) + unitSection(details, a, radios)
      + mapSection(top, $("#rp-expand").checked) + graphsSkeleton() + devicesSection(devs, a, which)
      + historySection(stab, a, speeds, changes, hours) + extrasSection(plc, probes);
    drawGraphs(g);
    document.title = `MeshLens report ${new Date().toISOString().slice(0, 10)}`;   // the PDF's default file name
    $("#rp-status").textContent = "Ready.";
    $("#rp-print").disabled = false;
  } catch (err) {
    $("#report").innerHTML = err.login
      ? '<div class="card"><p>You need to log in first. <a href="/">Open MeshLens</a>, log in, then come back to this page.</p></div>'
      : `<div class="card"><p class="bad">Could not build the report: ${esc(err.message)}</p></div>`;
    $("#rp-status").textContent = "";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  ["#rp-hours", "#rp-devs", "#rp-low", "#rp-expand"].forEach((s) => $(s).addEventListener("change", build));
  $("#rp-print").onclick = () => window.print();
  build();
});
