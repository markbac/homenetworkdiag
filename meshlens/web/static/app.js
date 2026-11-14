"use strict";
// MeshLens web UI. No framework; every value from the API is escaped before it reaches the page.

const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
const ago = (ts) => { if (!ts) return "never"; const s = Date.now() / 1000 - ts; return s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`; };
const rssiClass = (r) => r == null ? "" : r >= -60 ? "good" : r >= -70 ? "" : r >= -78 ? "warn" : "bad";
const scoreClass = (s) => s == null ? "muted" : s >= 80 ? "good" : s >= 60 ? "warn" : "bad";
const bandLabel = (b) => b ? `${b} GHz` : "wired";
const KIND = { connect: "Connected", disconnect: "Disconnected", kick: "Kicked (weak signal)", roam_candidate: "Roam attempt",
  roam: "Roam result", moved_away: "Moved to another unit", leave: "Left (signal)", assoc_failed: "Association failed",
  radar: "Radar detected (DFS)", channel_change: "Channel changed", channel_replan: "Channel re-planned", unit_reboot: "Unit restarted",
  link_change: "Wired link down/up", wan_ip_change: "Internet reconnected" };
const SEV = { high: "High", medium: "Medium", low: "Low", info: "Info" };

let unitsByMac = {}, devicesByMac = {};

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts,
    headers: opts.body ? { "Content-Type": "application/json" } : undefined });
  if (res.status === 401 && !path.startsWith("/api/auth")) { showLogin(); throw new Error("login required"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const post = (p, body = {}) => api(p, { method: "POST", body: JSON.stringify(body) });

// Resolve a unit or any of its radio/BSSID/backhaul addresses; ASUS varies byte 0 per SSID, so match bytes 1-5 too.
let unitByAddr = {}, unitByTail = {};
function indexUnits(units) {
  unitByAddr = {}; unitByTail = {};
  for (const u of units) for (const m of [u.mac, ...(u.macs || [])]) { unitByAddr[m] = u; unitByTail[m.slice(3)] = u; }
}
function unitName(mac) {
  if (!mac) return "";
  const u = unitsByMac[mac] || unitByAddr[mac] || unitByTail[mac.slice(3)];
  return u ? (u.alias || u.model || u.mac) : mac;
}
// Same fallback as meshlens/names.py: an unnamed device is labelled from its OS hint and maker.
function osLabel(h) { h = (h || "").toLowerCase(); return h.includes("msft") ? "Windows PC" : h.includes("android") ? "Android device" : (h.includes("dhcpcd") || h.includes("udhcp")) ? "Linux device" : null; }
function displayName(d, mac) {
  if (!d) return mac;
  if (d.user_name || d.name) return d.user_name || d.name;
  const v = (d.vendor || "").split(",")[0].replace(/ Inc\.?$/i, "").trim(), o = osLabel(d.os_hint);
  return o && v ? `${o} (${v})` : (o || v) ? `${o || v} ${(d.mac || mac).slice(-5)}` : (d.mac || mac);
}
function devName(mac) { return displayName(devicesByMac[mac], mac); }

// ---------------------------------------------------------------- login
async function showLogin() {
  const st = await api("/api/auth/state");
  $("#tabs").hidden = true; $("#logout").hidden = true;
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = true));
  $("#login").hidden = false;
  $("#login-title").textContent = st.setup_needed ? "Create the dashboard password" : "Log in";
  $("#login-help").textContent = st.setup_needed ? "First run: choose a password (10+ characters). It protects the dashboard on your network." : "";
  $("#login-pw").autocomplete = st.setup_needed ? "new-password" : "current-password";
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    try { await post(st.setup_needed ? "/api/auth/setup" : "/api/auth/login", { password: $("#login-pw").value });
      $("#login-pw").value = ""; $("#login-error").textContent = ""; start(); }
    catch (err) { $("#login-error").textContent = err.message; }
  };
}

// ---------------------------------------------------------------- tabs
const LOADERS = { overview: () => loadOverview(), analysis: () => loadAnalysis(), devices: () => loadDevices(),
  events: () => loadEvents(), settings: () => loadSettings(), help: () => loadHelp() };

function showTab(name, anchor) {
  if (!LOADERS[name]) name = "overview";
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== name));
  history.replaceState(null, "", "#" + name);
  return Promise.resolve(LOADERS[name]()).then(() => {
    if (anchor) { const el = document.getElementById(anchor); if (el) { el.scrollIntoView({ block: "start" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1600); } }
  }).catch((e) => console.warn(e));
}

async function start() {
  const st = await api("/api/auth/state");
  if (!st.logged_in) return showLogin();
  $("#login").hidden = true; $("#tabs").hidden = false; $("#logout").hidden = false; $("#phone-btn").hidden = false;
  await refreshBase();
  showTab((location.hash || "#overview").slice(1));
}

async function refreshBase() {
  const [units, devs, status] = await Promise.all([api("/api/units"), api("/api/devices"), api("/api/status")]);
  unitsByMac = Object.fromEntries(units.map((u) => [u.mac, u])); indexUnits(units);
  devicesByMac = Object.fromEntries(devs.map((d) => [d.mac, d]));
  showUpdateBanner(status.update);
  const a = status.asus;
  $("#poll").textContent = a.error ? "Router: not connected (Settings → Status)" : a.last_poll ? `Router polled ${ago(a.last_poll)}` : "Waiting for first router poll";
  $("#poll").className = a.error ? "bad" : "muted";
  return { units, devs, status };
}

// ---------------------------------------------------------------- issues
function subjectLink(i) {
  if (i.scope === "device" && i.subject) return `<a href="#" data-mac="${esc(i.subject)}">${esc(devName(i.subject))}</a>`;
  if (i.scope === "unit" && i.subject) return esc(unitName(i.subject));
  return "";
}

function renderIssue(i, open = false) {
  const when = i.from ? `<span class="muted small">${esc(fmtTime(i.from))}${i.to && i.to !== i.from ? " – " + esc(fmtTime(i.to)) : ""}</span>` : "";
  return `<details class="issue ${esc(i.severity)}" ${open ? "open" : ""}>
    <summary><span class="sev ${esc(i.severity)}">${esc(SEV[i.severity] || i.severity)}</span> <b>${esc(i.title)}</b> ${when}</summary>
    <div class="issue-body">
      <p><span class="lbl">Seen</span> ${esc(i.evidence)} ${subjectLink(i) ? "· " + subjectLink(i) : ""}</p>
      <p><span class="lbl">Why</span> ${esc(i.cause)}</p>
      ${i.fixes && i.fixes.length ? `<div><span class="lbl">Try</span><ol>${i.fixes.map((f) => `<li>${esc(f)}</li>`).join("")}</ol></div>` : ""}
      <p class="small"><a href="#" data-help="${esc(i.help)}">More about this in Help</a></p>
    </div></details>`;
}

// ---------------------------------------------------------------- overview
async function loadOverview() {
  const [{ units }, a] = await Promise.all([refreshBase(), api("/api/analysis?hours=24")]);
  const n = a.network;
  $("#net-score").textContent = n.score ?? "–";
  $("#net-score").className = "score " + scoreClass(n.score);
  $("#net-summary").textContent = n.summary;
  const c = n.issue_counts;
  $("#net-counts").innerHTML = ["high", "medium", "low", "info"].map((k) => `<span class="sev ${k}">${c[k]} ${SEV[k].toLowerCase()}</span>`).join(" ")
    + ` <span class="muted small">${esc(n.devices_poor)} of ${esc(n.devices_seen)} Wi-Fi devices in poor shape</span>`;
  const top = a.issues.filter((i) => i.severity === "high" || i.severity === "medium").slice(0, 6);
  $("#top-issues").innerHTML = top.length ? top.map((i) => renderIssue(i)).join("") : '<p class="muted">Nothing needs attention right now.</p>';

  $("#units-table").innerHTML = "<tr><th>Unit</th><th>Model</th><th>IP</th><th>Chip</th><th>Health</th><th>Status</th></tr>" +
    units.map((u) => { const h = a.units[u.mac] || {};
      return `<tr><td>${esc(u.alias || u.model || u.mac)}</td><td>${esc(u.model)}</td><td>${esc(u.ip)}</td><td>${esc(u.chip)}</td>
      <td class="${scoreClass(h.score)}">${h.score ?? "–"}</td>
      <td class="${u.reachable ? "good" : "bad"} wrap">${u.reachable ? "reachable" : esc(u.last_error || "not reachable")}</td></tr>`; }).join("");
  const radios = await api("/api/radios");
  $("#radio-table").innerHTML = "<tr><th>Unit</th><th>Band</th><th>Tx failures</th><th>Rx errors</th><th>Temp</th><th>Noise</th></tr>" +
    radios.map((r) => `<tr><td>${esc(unitName(r.unit))}</td><td>${esc(bandLabel(r.band))}</td>
      <td class="${r.tx_fail_pct > 10 ? "bad" : r.tx_fail_pct > 5 ? "warn" : ""}">${r.tx_fail_pct ?? ""}${r.tx_fail_pct != null ? "%" : ""}</td>
      <td class="${r.rx_crc_pct > 10 ? "bad" : r.rx_crc_pct > 5 ? "warn" : ""}">${r.rx_crc_pct ?? ""}${r.rx_crc_pct != null ? "%" : ""}</td>
      <td>${r.temperature != null ? esc(r.temperature) + " °C" : ""}</td><td>${r.noise != null ? esc(r.noise) + " dBm" : ""}</td></tr>`).join("");
  const st = await api("/api/stability?hours=24");
  $("#stability-table").innerHTML = "<tr><th>Device</th><th>Health</th><th>Kicks</th><th>Roam attempts</th><th>Disconnects</th><th>Weakest signal</th><th>Units</th></tr>" +
    st.slice(0, 15).map((s) => { const h = a.devices[s.mac] || {};
      return `<tr><td><a href="#" data-mac="${esc(s.mac)}">${esc(devName(s.mac))}</a></td><td class="${scoreClass(h.score)}">${h.score ?? "–"}</td>
      <td class="${s.kicks ? "bad" : ""}">${s.kicks}</td><td>${s.roam_attempts}</td><td>${s.disconnects}</td>
      <td class="${rssiClass(s.weakest_rssi)}">${s.weakest_rssi ?? ""}</td><td>${esc(s.units.map(unitName).join(", "))}</td></tr>`; }).join("");
}

// ---------------------------------------------------------------- analysis
async function loadAnalysis() {
  await refreshBase();
  const hours = $("#an-hours").value, sev = $("#an-sev").value.split(",").filter(Boolean);
  const [a, t] = await Promise.all([api(`/api/analysis?hours=${hours}`), api("/api/trends?days=30")]);
  const shown = a.issues.filter((i) => !sev.length || sev.includes(i.severity));
  $("#an-count").textContent = `${shown.length} of ${a.issues.length} issues`;
  $("#an-issues").innerHTML = shown.length ? shown.map((i) => renderIssue(i)).join("") : '<p class="muted">No issues of this kind in this period.</p>';
  $("#an-units").innerHTML = "<tr><th>Unit</th><th>Health</th><th>Notes</th></tr>" +
    Object.entries(a.units).map(([mac, h]) => `<tr><td>${esc(unitName(mac))}</td><td class="${scoreClass(h.score)}">${h.score ?? "–"} ${h.label ? `<span class="muted small">${esc(h.label)}</span>` : ""}</td><td class="wrap muted">${esc(h.note || "")}</td></tr>`).join("");
  const devs = Object.entries(a.devices).sort((x, y) => x[1].score - y[1].score);
  $("#an-devices").innerHTML = "<thead><tr><th>Device</th><th>Health</th><th>Typical signal</th><th>Typical rate</th><th>Band</th><th>Unit</th><th>Kicks</th><th>Roam attempts</th><th>Disconnects</th></tr></thead><tbody>" +
    devs.slice(0, 100).map(([mac, h]) => `<tr data-mac="${esc(mac)}"><td>${esc(devName(mac))}</td><td class="${scoreClass(h.score)}">${h.score}</td>
      <td class="${rssiClass(h.typical_rssi)}">${h.typical_rssi != null ? Math.round(h.typical_rssi) + " dBm" : ""}</td>
      <td>${h.typical_rate != null ? Math.round(h.typical_rate) + " Mbit/s" : ""}</td><td>${esc(h.band ? h.band + " GHz" : "")}</td>
      <td>${esc(unitName(h.unit))}</td><td class="${h.kicks ? "bad" : ""}">${h.kicks}</td><td>${h.roam_attempts}</td><td>${h.disconnects}</td></tr>`).join("") + "</tbody>";
  const c = t.compare, rows = [["Kicks per day", "kicks_per_day", true], ["Roam attempts per day", "roam_attempts_per_day", true],
    ["Disconnects per day", "disconnects_per_day", true], ["Average device health", "avg_device_score", false]];
  $("#an-compare").innerHTML = `<tr><th></th><th>Previous 7 days</th><th>Last 7 days</th><th>Change</th></tr>` +
    rows.map(([label, k, lowerBetter]) => { const p = c.previous[k], q = c.current[k];
      const d = p != null && q != null ? q - p : null, better = d == null || d === 0 ? "" : (d < 0) === lowerBetter ? "good" : "bad";
      return `<tr><td>${label}</td><td>${p ?? "–"}</td><td>${q ?? "–"}</td><td class="${better}">${d == null ? "" : (d > 0 ? "+" : "") + Math.round(d * 10) / 10}</td></tr>`; }).join("") +
    `<tr><td class="muted small" colspan="4">Days with data: ${c.previous.days_with_data} / ${c.current.days_with_data}</td></tr>`;
  drawTrend($("#trend"), t.days);
}

function drawTrend(canvas, days) {
  const ctx = canvas.getContext("2d"), w = (canvas.width = canvas.clientWidth * 2), h = (canvas.height = 360);
  const css = getComputedStyle(document.body), col = (v) => css.getPropertyValue(v).trim();
  ctx.clearRect(0, 0, w, h); ctx.font = "22px system-ui";
  const series = [["kicks", "Kicks", "--bad"], ["disconnects", "Disconnects", "--warn"], ["roam_attempts", "Roam attempts", "--accent"]];
  $("#trend-legend").innerHTML = series.map(([, l, c]) => `<span><i style="background:var(${c})"></i>${l}</span>`).join("") + '<span><i class="dash"></i>Average device health (0-100, right)</span>';
  if (days.length < 2) { ctx.fillStyle = col("--muted"); ctx.fillText("Trends appear after MeshLens has run for two days (daily summaries are made hourly).", 20, h / 2); return; }
  const pad = 50, max = Math.max(1, ...days.flatMap((d) => series.map(([k]) => d[k])));
  const x = (i) => pad + (i / (days.length - 1)) * (w - 2 * pad), y = (v, m) => h - pad - (v / m) * (h - 2 * pad);
  ctx.strokeStyle = col("--line"); ctx.fillStyle = col("--muted"); ctx.lineWidth = 1;
  for (const f of [0, 0.5, 1]) { ctx.beginPath(); ctx.moveTo(pad, y(max * f, max)); ctx.lineTo(w - pad, y(max * f, max)); ctx.stroke(); ctx.fillText(String(Math.round(max * f)), 4, y(max * f, max) + 7); }
  days.forEach((d, i) => { if (i % Math.ceil(days.length / 8) === 0) ctx.fillText(d.day.slice(5), x(i) - 26, h - 12); });
  for (const [k, , c] of series) { ctx.strokeStyle = col(c); ctx.lineWidth = 3; ctx.beginPath(); days.forEach((d, i) => (i ? ctx.lineTo(x(i), y(d[k], max)) : ctx.moveTo(x(i), y(d[k], max)))); ctx.stroke(); }
  ctx.setLineDash([8, 6]); ctx.strokeStyle = col("--good"); ctx.lineWidth = 2; ctx.beginPath(); let started = false;
  days.forEach((d, i) => { if (d.avg_score == null) return; const yy = y(d.avg_score, 100); started ? ctx.lineTo(x(i), yy) : ctx.moveTo(x(i), yy); started = true; });
  ctx.stroke(); ctx.setLineDash([]);
}

// ---------------------------------------------------------------- devices
let devSort = { key: "health", dir: 1 };
const DEV_COLS = [["name", "Name"], ["health", "Health"], ["type", "Type"], ["ip", "IP"], ["vendor", "Vendor"], ["conn", "Connection"],
  ["rssi", "Signal"], ["rate", "Rate tx/rx"], ["auth", "Security"], ["seen", "Last seen"]];

function fillSelect(sel, values, keepFirst = true) {
  const el = $(sel), cur = el.value, first = keepFirst ? el.options[0].outerHTML : "";
  el.innerHTML = first + values.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
  if ([...el.options].some((o) => o.value === cur)) el.value = cur;
}

async function loadDevices() {
  const { devs, units } = await refreshBase();
  fillSelect("#dev-unit", units.map((u) => [u.mac, u.alias || u.model || u.mac]));
  fillSelect("#dev-type", [...new Set(devs.map((d) => d.user_type || d.dtype).filter(Boolean))].sort().map((t) => [t, t]));
  const q = $("#dev-search").value.toLowerCase(), online = $("#dev-online").checked, watched = $("#dev-watched").checked;
  const unit = $("#dev-unit").value, band = $("#dev-band").value, type = $("#dev-type").value, problems = $("#dev-problems").checked;
  const now = Date.now() / 1000;
  const unitOf = (d) => { const m = (d.live || {}).unit || d.unit; if (!m) return null; const u = unitsByMac[m] || unitByAddr[m] || unitByTail[m.slice(3)]; return u ? u.mac : m; };
  const bandOf = (d) => d.wired ? "wired" : ((d.live || {}).band || d.band || "wired");
  const rows = devs.filter((d) => (!online || d.live || (d.wired && now - d.last_seen < 900) || (d.last_alive && now - d.last_alive < 1800))
    && (!watched || d.watched) && (!unit || unitOf(d) === unit) && (!band || bandOf(d) === band)
    && (!type || (d.user_type || d.dtype) === type) && (!problems || (d.health != null && d.health < 80))
    && (!q || [d.user_name, d.name, d.ip, d.mac, d.vendor, d.owner, d.room, d.model].join(" ").toLowerCase().includes(q)));
  const key = (d) => { const l = d.live || {};
    return { name: displayName(d, d.mac).toLowerCase(), health: d.health ?? 101, type: d.user_type || d.dtype || "~", ip: (d.ip || "~").split(".").map((x) => x.padStart(3, "0")).join("."),
      vendor: (d.vendor || "~").toLowerCase(), conn: bandOf(d) + unitName(unitOf(d)), rssi: l.rssi ?? 0, rate: l.tx_rate ?? -1, auth: d.wifi_auth || "~", seen: -(d.last_seen || 0) }[devSort.key]; };
  rows.sort((a, b) => { const x = key(a), y = key(b); return (x < y ? -1 : x > y ? 1 : 0) * devSort.dir; });
  $("#dev-count").textContent = `${rows.length} of ${devs.length}`;
  $("#devices-table").innerHTML = `<thead><tr>${DEV_COLS.map(([k, l]) => `<th data-sort="${k}" class="${devSort.key === k ? (devSort.dir > 0 ? "asc" : "desc") : ""}">${l}</th>`).join("")}</tr></thead><tbody>` +
    rows.map((d) => { const l = d.live || {}, h = d.health;
      return `<tr data-mac="${esc(d.mac)}" class="hrow ${h == null ? "" : h >= 80 ? "h-good" : h >= 60 ? "h-warn" : "h-bad"}">
      <td>${d.watched ? "&#9733; " : ""}${esc(displayName(d, d.mac))}${d.randomised ? ' <span class="pill">private MAC</span>' : ""}</td>
      <td class="${scoreClass(h)}">${h ?? ""}</td>
      <td class="muted">${esc(d.user_type || d.dtype || "")}</td><td>${esc(d.ip)}</td><td class="small">${esc(d.vendor)}</td>
      <td>${d.wired ? "wired" : (l.band || d.band) ? `<span class="pill b${esc(String(l.band || d.band).replace(".", ""))}">${esc(bandLabel(l.band || d.band))}</span> ${esc(unitName(l.unit || d.unit))}` : '<span class="muted">LAN</span>'}</td>
      <td class="${rssiClass(l.rssi)}">${l.rssi ?? ""}</td><td>${l.tx_rate != null ? esc(`${l.tx_rate}/${l.rx_rate ?? "?"}`) : ""}</td>
      <td class="small">${esc(d.wifi_auth)}</td><td class="muted small">${esc(ago(d.last_seen))}</td></tr>`; }).join("") + "</tbody>";
}

// ---------------------------------------------------------------- device drawer
async function openDevice(mac) {
  const d = await api(`/api/devices/${encodeURIComponent(mac)}?hours=24`);
  const last = d.history[d.history.length - 1] || {};
  const s = d.stability || {}, h = d.health || {};
  $("#drawer-body").innerHTML = `
    <h2>${esc(displayName(d, d.mac))} ${h.score != null ? `<span class="score small-score ${scoreClass(h.score)}">${esc(h.score)}</span>` : ""}</h2>
    <div class="kv">
      <div>MAC</div><div class="mono">${esc(d.mac)}${d.randomised ? " (private / randomised)" : ""}</div>
      <div>IP</div><div>${esc(d.ip)}</div>
      <div>Vendor</div><div>${esc(d.vendor)} <span class="muted small">${esc(d.vendor_src)}</span></div>
      <div>Detected name</div><div>${esc(d.name)} <span class="muted small">${esc(d.name_src)}</span></div>
      <div>Connection</div><div>${d.wired ? "wired" + (d.unit ? " via " + esc(unitName(d.unit)) : "") : (last.band || d.band) ? esc(`${bandLabel(last.band || d.band)} via ${unitName(last.unit || d.unit)}${last.iface ? " (" + last.iface + ")" : ""}`) : '<span class="muted">not known to the router (found by discovery)</span>'}</div>
      <div>Signal now</div><div class="${rssiClass(last.rssi)}">${last.rssi != null ? esc(last.rssi) + " dBm" : "n/a"}</div>
      <div>Typical signal</div><div class="${rssiClass(h.typical_rssi)}">${h.typical_rssi != null ? Math.round(h.typical_rssi) + " dBm" : "n/a"}</div>
      <div>Rate tx / rx</div><div>${last.tx_rate != null ? esc(`${last.tx_rate} / ${last.rx_rate} Mbit/s`) : "n/a"}</div>
      <div>Wi-Fi security</div><div>${esc(d.wifi_auth)}</div>
      <div>Last 24 h</div><div>${s.mac ? esc(`${s.kicks} kicks, ${s.roam_attempts} roam attempts, ${s.disconnects} disconnects`) : "no Wi-Fi events"}</div>
    </div>
    ${typeof deviceExtras === "function" ? deviceExtras(d) : ""}
    ${d.issues.length ? "<h3>Issues</h3>" + d.issues.map((i, n) => renderIssue(i, n === 0)).join("") : ""}
    <p><a href="#" id="dev-events-link">Show this device's Wi-Fi events →</a></p>
    ${deviceGraphsHtml(d.mac)}
    <h3>Journey (handovers, kicks, connects) <button class="help" data-help="journey">?</button></h3>
    <ul class="timeline">${d.journey.slice(-150).reverse().map((j) => `<li class="${esc(j.kind)}"><span class="muted small">${esc(fmtTime(j.ts))}</span>
      ${esc(KIND[j.kind] || j.kind)} · ${esc(unitName(j.unit))}${j.band ? " · " + esc(bandLabel(j.band)) : ""}
      ${j.detail.rssi != null ? ` · <span class="${rssiClass(j.detail.rssi)}">${esc(j.detail.rssi)} dBm</span>` : ""}
      ${j.detail.target ? " → " + esc(unitName(j.detail.target) || j.detail.target) + (j.detail.target_rssi != null ? ` (${esc(j.detail.target_rssi)} dBm)` : "") : ""}
      ${j.detail.new_unit ? " → " + esc(unitName(j.detail.new_unit)) : ""}
      ${j.offline_s != null ? ` <span class="muted small">after ${esc(j.offline_s)} s offline</span>` : ""}</li>`).join("") || '<li class="muted">No Wi-Fi events in the last 24 h</li>'}</ul>
    <h3>Your notes (enrichment) <button class="help" data-help="enrichment">?</button></h3>
    <form id="enrich">
      <label>Name <input name="user_name" value="${esc(d.user_name)}"></label>
      <label>Owner <input name="owner" value="${esc(d.owner)}"></label>
      <label>Room <input name="room" value="${esc(d.room)}"></label>
      <label>Type <input name="user_type" value="${esc(d.user_type)}" placeholder="e.g. gaming PC, console, camera"></label>
      <label>Tags <input name="tags" value="${esc(d.tags)}" placeholder="comma separated"></label>
      <label>Notes <input name="notes" value="${esc(d.notes)}"></label>
      <label class="inline"><input name="watched" type="checkbox" ${d.watched ? "checked" : ""}> Watch this device</label>
      <button type="submit">Save</button> <span id="enrich-msg" class="muted"></span>
    </form>`;
  $("#drawer").hidden = false;
  deviceGraphs(d.mac, 24);
  $("#dev-events-link").onclick = (ev) => { ev.preventDefault(); showDeviceEvents(d.mac); };
  $("#enrich").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target); const body = Object.fromEntries(f.entries()); body.watched = f.has("watched");
    await api(`/api/devices/${encodeURIComponent(mac)}`, { method: "PATCH", body: JSON.stringify(body) });
    $("#enrich-msg").textContent = "Saved"; refreshBase();
  };
}

function drawSpark(canvas, pts) {
  const ctx = canvas.getContext("2d"), w = (canvas.width = canvas.clientWidth * 2), h = (canvas.height = 140);
  ctx.clearRect(0, 0, w, h);
  const css = getComputedStyle(document.body);
  if (pts.length < 2) { ctx.fillStyle = css.getPropertyValue("--muted"); ctx.font = "24px system-ui"; ctx.fillText("Not enough samples yet", 10, 70); return; }
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0], lo = -95, hi = -30;
  const x = (t) => ((t - t0) / Math.max(1, t1 - t0)) * (w - 10) + 5, y = (r) => h - ((Math.min(hi, Math.max(lo, r)) - lo) / (hi - lo)) * (h - 10) - 5;
  ctx.strokeStyle = css.getPropertyValue("--line"); ctx.beginPath(); ctx.moveTo(0, y(-70)); ctx.lineTo(w, y(-70)); ctx.stroke();
  ctx.strokeStyle = css.getPropertyValue("--accent"); ctx.lineWidth = 3; ctx.beginPath();
  pts.forEach(([t, r], i) => (i ? ctx.lineTo(x(t), y(r)) : ctx.moveTo(x(t), y(r)))); ctx.stroke();
}

function drawDaily(canvas, rows) {
  const ctx = canvas.getContext("2d"), w = (canvas.width = canvas.clientWidth * 2), h = (canvas.height = 140);
  const css = getComputedStyle(document.body);
  ctx.clearRect(0, 0, w, h);
  if (!rows.length) { ctx.fillStyle = css.getPropertyValue("--muted"); ctx.font = "24px system-ui"; ctx.fillText("No daily summaries yet", 10, 70); return; }
  const max = Math.max(1, ...rows.map((r) => Math.max(r.kicks, r.disconnects))), bw = (w - 10) / 30;
  const day0 = new Date(rows[rows.length - 1].day).getTime() / 86400000 - 29;
  rows.forEach((r) => { const i = new Date(r.day).getTime() / 86400000 - day0, x = 5 + i * bw;
    ctx.fillStyle = css.getPropertyValue("--bad"); ctx.fillRect(x, h - 5 - (r.kicks / max) * (h - 20), bw * 0.4, (r.kicks / max) * (h - 20));
    ctx.fillStyle = css.getPropertyValue("--warn"); ctx.fillRect(x + bw * 0.45, h - 5 - (r.disconnects / max) * (h - 20), bw * 0.4, (r.disconnects / max) * (h - 20)); });
}

// ---------------------------------------------------------------- events
const EV_CLASS = { kick: "kick", disconnect: "disconnect", leave: "disconnect", connect: "connect", roam_candidate: "roam", roam: "roam",
  moved_away: "roam", assoc_failed: "assoc_failed", radar: "unit", channel_change: "unit", channel_replan: "unit", unit_reboot: "unit",
  link_change: "unit", wan_ip_change: "unit" };

async function loadEvents() {
  const { devs, units } = await refreshBase();
  fillSelect("#ev-unit", units.map((u) => [u.mac, u.alias || u.model || u.mac]));
  $("#ev-devlist").innerHTML = devs.map((d) => `<option value="${esc(displayName(d, d.mac))}">${esc(d.mac)}</option>`).join("");
  const kind = $("#ev-kind").value, hours = $("#ev-hours").value, unit = $("#ev-unit").value, band = $("#ev-band").value;
  const q = $("#ev-dev").value.trim().toLowerCase();
  // An exact device name or MAC asks the server for just that device (so long periods aren't cut off).
  const exact = q && devs.find((d) => d.mac === q || displayName(d, d.mac).toLowerCase() === q);
  const evs = await api(`/api/events?hours=${hours}&limit=5000${kind ? "&kind=" + kind : ""}${exact ? "&mac=" + encodeURIComponent(exact.mac) : ""}`);
  const unitMatch = (m) => { if (!unit) return true; const u = unitsByMac[m] || unitByAddr[m] || unitByTail[(m || "").slice(3)]; return u && u.mac === unit; };
  const rows = evs.filter((e) => unitMatch(e.unit) && (!band || e.band === band)
    && (exact || !q || [devName(e.mac), e.mac].join(" ").toLowerCase().includes(q)));
  $("#ev-count").textContent = `${rows.length} event${rows.length === 1 ? "" : "s"}`;
  $("#events-table").innerHTML = "<thead><tr><th>Time</th><th>Device</th><th>Event</th><th>Unit</th><th>Band</th><th>Detail</th></tr></thead><tbody>" +
    rows.map((e) => `<tr class="ev ev-${EV_CLASS[e.kind] || "other"}"><td class="muted small">${esc(fmtTime(e.ts))}</td>
      <td>${e.mac ? `<a href="#" data-mac="${esc(e.mac)}">${esc(devName(e.mac))}</a>` : '<span class="muted">(the unit itself)</span>'}</td>
      <td><span class="evk ek-${EV_CLASS[e.kind] || "other"}">${esc(KIND[e.kind] || e.kind)}</span></td><td>${esc(unitName(e.unit))}</td>
      <td>${e.band ? `<span class="pill b${esc(String(e.band).replace(".", ""))}">${esc(bandLabel(e.band))}</span>` : ""}</td>
      <td class="wrap small">${e.detail.rssi != null ? `<span class="${rssiClass(e.detail.rssi)}">${esc(e.detail.rssi)} dBm</span> ` : ""}${e.detail.target ? "→ " + esc(unitName(e.detail.target)) + (e.detail.target_rssi != null ? ` (${esc(e.detail.target_rssi)} dBm) ` : " ") : ""}${e.detail.new_unit ? "→ " + esc(unitName(e.detail.new_unit)) : ""}${e.detail.status != null ? " status " + esc(e.detail.status) : ""}${unitDetail(e)}</td></tr>`).join("")
    + (rows.length ? "" : '<tr><td colspan="6" class="muted">No events match.</td></tr>') + "</tbody>";
}

function unitDetail(e) {
  const d = e.detail || {};
  if (e.kind === "radar") return `channel ${esc(d.channel)}`;
  if (e.kind === "channel_change") return d.from_channel != null ? `${esc(d.band || "")} GHz ${esc(d.from_channel)} → ${esc(d.to_channel)}${d.width ? ` (${esc(d.width)} MHz)` : ""}` : `→ channel ${esc(d.to_channel)} (radar)`;
  if (e.kind === "channel_replan") return `channel ${esc(d.channel)}, ${esc(d.bw)} MHz${d.unavailable ? `, avoiding ${esc(d.unavailable)}` : ""}`;
  if (e.kind === "unit_reboot") return d.by ? `requested by ${esc(d.by)}` : "";
  if (e.kind === "link_change") return `${esc(e.iface)}: ${esc(d.changes)} change${d.changes > 1 ? "s" : ""}, now ${esc(d.state || "?")}${d.speed ? ` ${esc(d.speed)} Mbit/s` : ""}`;
  if (e.kind === "wan_ip_change") return "new internet address";
  return "";
}

function showDeviceEvents(mac) {
  $("#ev-dev").value = devName(mac);
  $("#drawer").hidden = true;
  showTab("events");
}

// ---------------------------------------------------------------- settings
// Field types: text, number, bool, list (one per line), select, secret (write-only).
const SECTIONS = [
  { id: "dashboard", title: "Dashboard login", help: "set-dashboard", custom: renderDashboard },
  { id: "network", title: "Network", help: "set-network", note: "Your home network. Discovery (coming) scans only these subnets.", fields: [
    { k: "subnets", label: "Subnets (one per line)", type: "list", hint: "e.g. 10.0.10.0/23" },
    { k: "exclude", label: "Never scan these addresses or ranges (one per line)", type: "list" },
    { k: "dns_servers", label: "DNS servers to test (one per line; empty = the router)", type: "list" }] },
  { id: "router", title: "ASUS router", help: "set-router", secretLabel: "SSH password", note: "MeshLens only runs read-only commands. Nodes are found automatically from the router's mesh list.", fields: [
    { k: "host", label: "Router address", type: "text" }, { k: "port", label: "SSH port", type: "number" },
    { k: "username", label: "Username", type: "text" },
    { k: "poll_seconds", label: "Quick poll every (seconds)", type: "number", hint: "clients, signal, radio health, new log lines" },
    { k: "full_poll_seconds", label: "Full poll every (seconds)", type: "number", hint: "settings, leases, device names" },
    { k: "timeout_seconds", label: "SSH timeout (seconds)", type: "number" }] },
  { id: "pihole", title: "Pi-hole", help: "set-pihole", test: true, secretLabel: "App password (v6) or API token (v5)", note: "Read-only: DNS queries per device, blocked domains. The collector itself is still to come; you can store and test the login now.", fields: [
    { k: "enabled", label: "Use Pi-hole", type: "bool" }, { k: "url", label: "Address", type: "text", hint: "e.g. http://10.0.10.53" },
    { k: "version", label: "Pi-hole version", type: "select", opts: [["6", "v6 (2025 and later)"], ["5", "v5"]] },
    { k: "verify_tls", label: "Check HTTPS certificate", type: "bool" }] },
  { id: "homeassistant", title: "Home Assistant", help: "set-homeassistant", test: true, secretLabel: "Long-lived access token", note: "Read-only: device names, rooms, and which smart devices are offline. Collector still to come.", fields: [
    { k: "enabled", label: "Use Home Assistant", type: "bool" }, { k: "url", label: "Address", type: "text", hint: "e.g. http://homeassistant.local:8123" },
    { k: "verify_tls", label: "Check HTTPS certificate", type: "bool" }] },
  { id: "unifi", title: "UniFi controller", help: "set-unifi", test: true, secretLabel: "Password of the View Only admin", note: "Read-only: switch ports, link speeds, PoE and which device is on which port. Collector still to come.", fields: [
    { k: "enabled", label: "Use UniFi", type: "bool" }, { k: "url", label: "Controller address", type: "text", hint: "local controller: https://127.0.0.1:8443" },
    { k: "username", label: "Username (local View Only admin, no 2FA)", type: "text" }, { k: "site", label: "Site", type: "text" },
    { k: "verify_tls", label: "Check HTTPS certificate (off for the local self-signed one)", type: "bool" }] },
  { id: "devices", title: "Switches and other devices", help: "set-devices", custom: renderDeviceLogins },
  { id: "devolo", title: "Powerline (devolo)", help: "set-devolo", note: "Read-only via the devolo device API; any one adapter reports the whole powerline network. No password needed.", fields: [
    { k: "enabled", label: "Read devolo adapters", type: "bool" },
    { k: "adapters", label: "Adapter addresses (one per line; empty = devolo devices found on the network)", type: "list" },
    { k: "poll_seconds", label: "Read every (seconds)", type: "number" },
    { k: "slow_mbps", label: "Report links slower than (Mbit/s)", type: "number" }, { k: "bad_mbps", label: "…as high severity below (Mbit/s)", type: "number" }] },
  { id: "discovery", title: "Discovery and ports", help: "set-discovery", note: "Finds devices on the subnets under Network: paced ping sweep, ARP table, names (DNS, NetBIOS, mDNS, UPnP) and a TCP port scan. Nothing outside your subnets is touched.", fields: [
    { k: "enabled", label: "Run discovery on a schedule", type: "bool" },
    { k: "sweep_minutes", label: "Find devices every (minutes)", type: "number" },
    { k: "services_minutes", label: "Look up names and services every (minutes)", type: "number" },
    { k: "port_scan_hours", label: "Scan ports every (hours, 0 = only when asked)", type: "number" },
    { k: "port_set", label: "Ports to scan", type: "select", opts: [["top100", "Common home-network ports (about 85)"], ["top1000", "Ports 1-1024 plus common ones (slower)"]] },
    { k: "max_rate", label: "At most this many probes per second", type: "number" }] },
  { id: "monitor", title: "Monitors and speed tests", help: "set-monitor", note: "Pings the router, mesh units, the first ISP router, internet targets and watched devices; speed tests measure bufferbloat too.", fields: [
    { k: "enabled", label: "Run the latency monitor", type: "bool" },
    { k: "interval_seconds", label: "Ping every (seconds)", type: "number" },
    { k: "internet_targets", label: "Internet targets (one per line)", type: "list" },
    { k: "extra_targets", label: "Other targets, e.g. a game server (one per line)", type: "list" },
    { k: "include_nodes", label: "Ping each mesh unit", type: "bool" }, { k: "include_isp_hop", label: "Ping the ISP's first router", type: "bool" },
    { k: "include_watched", label: "Ping watched devices", type: "bool" },
    { k: "speedtest_hours", label: "Test from this PC every (hours, 0 = only when asked)", type: "number", hint: "each test moves a few hundred MB" },
    { k: "router_speedtest", label: "Run speed tests on the router (uses the router's own Ookla tool; switching this on accepts Ookla's licence and privacy terms: speedtest.net/about/terms and speedtest.net/about/privacy)", type: "bool" },
    { k: "router_speedtest_hours", label: "Test on the router every (hours, 0 = only when asked)", type: "number", hint: "at full line speed each test moves about 0.5-1 GB" },
    { k: "speedtest_seconds", label: "Seconds per direction", type: "number" },
    { k: "speedtest_server", label: "Speed test server", type: "text" },
    { k: "expected_down_mbps", label: "Your plan's download (Mbit/s, 0 = unknown)", type: "number" },
    { k: "expected_up_mbps", label: "Your plan's upload (Mbit/s, 0 = unknown)", type: "number" }] },
  { id: "hostpc", title: "This PC as a sensor", help: "hostpc", note: "Uses this PC's Wi-Fi card to survey the air (works while it is not connected) and its network adapter to watch for broadcast floods. Read-only.", fields: [
    { k: "enabled", label: "Use this PC as a sensor", type: "bool" }] },
  { id: "analysis", title: "Analysis thresholds", help: "set-analysis", note: "When MeshLens raises an issue. Defaults suit a typical home mesh.", fields: [
    { k: "kick_loop_count", label: "Kick loop: kicks", type: "number" }, { k: "kick_loop_minutes", label: "…within minutes", type: "number" },
    { k: "roam_storm_count", label: "Roam storm: attempts", type: "number" }, { k: "roam_storm_seconds", label: "…within seconds", type: "number" },
    { k: "flapping_count", label: "Flapping: disconnects", type: "number" }, { k: "flapping_minutes", label: "…within minutes", type: "number" },
    { k: "weak_rssi_dbm", label: "Weak signal below (dBm)", type: "number" }, { k: "sticky_margin_db", label: "Sticky: other unit stronger by (dB)", type: "number" },
    { k: "slow_rate_mbps_24", label: "Slow link on 2.4 GHz below (Mbit/s)", type: "number" }, { k: "slow_rate_mbps_5", label: "Slow link on 5/6 GHz below (Mbit/s)", type: "number" },
    { k: "radio_tx_fail_warn", label: "Radio Tx failures: warn at (%)", type: "number" }, { k: "radio_tx_fail_bad", label: "…high at (%)", type: "number" },
    { k: "radio_rx_err_warn", label: "Radio Rx errors: warn at (%)", type: "number" }, { k: "radio_rx_err_bad", label: "…high at (%)", type: "number" },
    { k: "radio_temp_warn", label: "Radio too hot at (°C)", type: "number" }, { k: "mass_drop_devices", label: "Mass drop: devices within a minute", type: "number" },
    { k: "crowded_clients_24", label: "Crowded 2.4 GHz radio: devices", type: "number" },
    { k: "flood_pps", label: "Broadcast/multicast flood at (packets/s at this PC)", type: "number" },
    { k: "channel_busy_pct", label: "Channel busy at (% from the access point's report)", type: "number" },
    { k: "crowded_neighbours", label: "Crowded channel: neighbouring networks", type: "number" },
    { k: "unit_cpu_pct", label: "Mesh unit CPU busy at (%)", type: "number" }, { k: "unit_mem_pct", label: "Mesh unit memory full at (%)", type: "number" },
    { k: "unit_temp_c", label: "Mesh unit too hot at (°C)", type: "number" }, { k: "conntrack_pct", label: "Connection table full at (%)", type: "number" }, { k: "watched_offline_hours", label: "Watched device missing after (hours)", type: "number" }] },
  { id: "retention", title: "Data kept", help: "set-data", note: "Older data is deleted hourly.", fields: [
    { k: "raw_days", label: "Detailed data (days)", type: "number" }, { k: "summary_days", label: "Daily summaries (days)", type: "number" }] },
  { id: "web", title: "Web interface", help: "set-web", note: "Changes take effect after MeshLens restarts (tray icon → Quit, then start it again).", fields: [
    { k: "bind", label: "Listen on", type: "text", hint: "0.0.0.0 = whole LAN, 127.0.0.1 = this PC only" }, { k: "port", label: "Port", type: "number" }] },
  { id: "export", title: "Export data", help: "export", custom: renderExport },
  { id: "updates", title: "Updates", help: "updates", custom: renderUpdates },
  { id: "status", title: "Status", help: "troubleshooting", custom: renderStatus },
];
let settingsData = null, currentSection = "dashboard";

async function loadSettings(sectionId) {
  settingsData = await api("/api/settings");
  if (sectionId) currentSection = sectionId;
  $("#set-nav").innerHTML = SECTIONS.map((s) => `<button data-set="${s.id}" class="${s.id === currentSection ? "on" : ""}">${esc(s.title)}${s.test && settingsData[s.id].enabled ? ' <span class="dot"></span>' : ""}</button>`).join("");
  const sec = SECTIONS.find((s) => s.id === currentSection);
  const head = `<h3>${esc(sec.title)} <button class="help" data-help="${sec.help}">?</button></h3>${sec.note ? `<p class="muted small">${esc(sec.note)}</p>` : ""}`;
  $("#set-body").innerHTML = `<div class="card">${head}<div id="sec-content"></div></div>`;
  if (sec.custom) return sec.custom($("#sec-content"));
  renderSectionForm($("#sec-content"), sec);
}

function fieldHtml(f, v) {
  const id = `f-${f.k}`, hint = f.hint ? `<span class="muted small">${esc(f.hint)}</span>` : "";
  if (f.type === "bool") return `<label class="inline"><input type="checkbox" name="${f.k}" ${v ? "checked" : ""}> ${esc(f.label)}</label>`;
  if (f.type === "list") return `<label>${esc(f.label)} ${hint}<textarea name="${f.k}" rows="3">${esc((v || []).join("\n"))}</textarea></label>`;
  if (f.type === "select") return `<label>${esc(f.label)}<select name="${f.k}" id="${id}">${f.opts.map(([o, l]) => `<option value="${esc(o)}" ${String(v) === o ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
  return `<label>${esc(f.label)} ${hint}<input name="${f.k}" id="${id}" ${f.type === "number" ? 'type="number" step="any"' : ""} value="${esc(v)}" autocomplete="off"></label>`;
}

function renderSectionForm(el, sec) {
  const vals = settingsData[sec.id], hasSecret = !!sec.secretLabel;
  const set = settingsData.secret_set[sec.id];
  el.innerHTML = `<form id="sec-form">${sec.fields.map((f) => fieldHtml(f, vals[f.k])).join("")}
    ${hasSecret ? `<label>${esc(sec.secretLabel)} <span class="pill">${set ? "stored" : "not set"}</span>
      <input name="secret" type="password" autocomplete="new-password" placeholder="${set ? "leave blank to keep" : ""}"></label>
      <label class="inline"><input name="clear_secret" type="checkbox"> Remove the stored ${esc(sec.secretLabel.toLowerCase())}</label>
      <p class="muted small">Kept in the Windows Credential Manager on the MeshLens PC, tied to this address. Never shown again or sent anywhere else.</p>` : ""}
    <button type="submit">Save</button> ${sec.test ? '<button type="button" id="sec-test" class="secondary">Test connection</button>' : ""}
    <span id="sec-msg" class="muted"></span></form>`;
  const form = $("#sec-form");
  form.onsubmit = async (e) => {
    e.preventDefault();
    const body = {};
    for (const f of sec.fields) {
      const inp = form.elements[f.k];
      body[f.k] = f.type === "bool" ? inp.checked : f.type === "list" ? inp.value.split("\n").map((x) => x.trim()).filter(Boolean) : inp.value;
    }
    if (hasSecret) { if (form.secret.value) body.secret = form.secret.value; if (form.clear_secret.checked) body.clear_secret = true; }
    try { await post(`/api/settings/${sec.id}`, body); await loadSettings(); $("#sec-msg").textContent = "Saved"; $("#sec-msg").className = "good"; }
    catch (err) { $("#sec-msg").textContent = err.message; $("#sec-msg").className = "error"; }
  };
  if (sec.test) $("#sec-test").onclick = async () => {
    $("#sec-msg").textContent = "Testing…"; $("#sec-msg").className = "muted";
    try { const r = await post(`/api/settings/test/${sec.id}`, form.secret.value ? { secret: form.secret.value } : {});
      $("#sec-msg").textContent = r.detail; $("#sec-msg").className = r.ok ? "good" : "error"; }
    catch (err) { $("#sec-msg").textContent = err.message; $("#sec-msg").className = "error"; }
  };
}

function renderDashboard(el) {
  el.innerHTML = `<form id="pw-form">
    <label>Current password <input name="current" type="password" autocomplete="current-password" required></label>
    <label>New password (10+ characters) <input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
    <label>New password again <input name="again" type="password" autocomplete="new-password" required></label>
    <button type="submit">Change password</button> <span id="pw-msg"></span>
    <p class="muted small">Changing it logs out every other browser and phone. Router and service passwords are separate and not affected.</p></form>`;
  $("#pw-form").onsubmit = async (e) => {
    e.preventDefault(); const f = e.target;
    if (f.new.value !== f.again.value) { $("#pw-msg").textContent = "The new passwords differ"; $("#pw-msg").className = "error"; return; }
    try { await post("/api/auth/password", { current: f.current.value, new: f.new.value }); f.reset(); $("#pw-msg").textContent = "Changed"; $("#pw-msg").className = "good"; }
    catch (err) { $("#pw-msg").textContent = err.message; $("#pw-msg").className = "error"; }
  };
}

const KIND_LABEL = { unifi_switch: "UniFi switch", tplink_easy_smart: "TP-Link Easy Smart switch", devolo: "devolo powerline",
  managed_switch: "Other managed switch", access_point: "Access point", other: "Other" };
let deviceRows = [];

function renderDeviceLogins(el) {
  deviceRows = settingsData.devices.map((d) => ({ ...d, orig_ident: d.ident }));
  const sugg = settingsData.suggested_devices;
  el.innerHTML = `<p class="muted small">Logins for switches, powerline adapters and other network gear, so MeshLens can read port and link
    details (read-only; collectors for these are still to come). UniFi switches are read through the UniFi controller instead.
    Passwords go to the Windows Credential Manager.</p>
    <div id="dl-rows"></div>
    <button type="button" id="dl-add" class="secondary">+ Add device</button>
    ${sugg.length ? `<h4>Found on your network</h4><p class="muted small">Wired devices whose maker usually makes network equipment.</p>
      <table>${sugg.map((s, i) => `<tr><td>${esc(s.name)}</td><td>${esc(s.host)}</td><td class="muted">${esc(s.vendor)}</td>
      <td><button type="button" class="secondary" data-sugg="${i}">Add</button></td></tr>`).join("")}</table>` : ""}
    <p><button type="button" id="dl-save">Save devices</button> <span id="dl-msg"></span></p>`;
  const draw = () => {
    $("#dl-rows").innerHTML = deviceRows.map((d, i) => d.removed ? "" : `<div class="dl-row" data-i="${i}">
      <input data-k="name" placeholder="Name" value="${esc(d.name)}">
      <select data-k="kind">${settingsData.device_kinds.map((k) => `<option value="${k}" ${k === d.kind ? "selected" : ""}>${esc(KIND_LABEL[k] || k)}</option>`).join("")}</select>
      <input data-k="host" placeholder="Address" value="${esc(d.host)}">
      <input data-k="username" placeholder="Username" value="${esc(d.username)}" autocomplete="off">
      <input data-k="password" type="password" placeholder="${d.password_set ? "password stored" : "password"}" autocomplete="new-password">
      <input data-k="notes" placeholder="Notes" value="${esc(d.notes)}">
      <button type="button" class="link" data-rm="${i}">Remove</button></div>`).join("") || '<p class="muted">No devices yet.</p>';
  };
  draw();
  el.oninput = (e) => { const row = e.target.closest(".dl-row"); if (row && e.target.dataset.k) deviceRows[row.dataset.i][e.target.dataset.k] = e.target.value; };
  el.onclick = async (e) => {
    const t = e.target;
    if (t.id === "dl-add") { deviceRows.push({ name: "", kind: "tplink_easy_smart", host: "", username: "", notes: "" }); draw(); }
    if (t.dataset.rm) { deviceRows[t.dataset.rm].removed = true; draw(); }
    if (t.dataset.sugg) { const s = sugg[t.dataset.sugg], v = s.vendor.toLowerCase();
      deviceRows.push({ name: s.name, host: s.host, username: "", notes: s.vendor,
        kind: v.includes("devolo") ? "devolo" : v.includes("ubiquiti") ? "unifi_switch" : v.includes("tp-link") ? "tplink_easy_smart" : "managed_switch" });
      t.closest("tr").remove(); draw(); }
    if (t.id === "dl-save") {
      try { const r = await post("/api/settings/devices", { devices: deviceRows }); settingsData = r; renderDeviceLogins(el);
        $("#dl-msg").textContent = "Saved"; $("#dl-msg").className = "good"; }
      catch (err) { $("#dl-msg").textContent = err.message; $("#dl-msg").className = "error"; }
    }
  };
}

// ---------------------------------------------------------------- phone
async function showPhone() {
  $("#phone-modal").hidden = false;
  $("#phone-body").innerHTML = '<p class="muted">Finding the addresses that work…</p>';
  const c = await api("/api/connect");
  if (c.local_only) {
    $("#phone-body").innerHTML = '<p class="warn">The dashboard only listens on this PC. Settings → Web interface → set <b>Listen on</b> to 0.0.0.0 and restart MeshLens.</p>';
    return;
  }
  $("#phone-body").innerHTML = c.urls.length ? `<img class="qr" src="/api/connect/qr.svg?i=0" alt="QR code for ${esc(c.urls[0].url)}">` +
    c.urls.map((u, i) => `<div class="url-row"><a href="${esc(u.url)}">${esc(u.url)}</a>${i === 0 ? ' <span class="pill">in the QR code</span>' : ""}
      <div class="muted small">${esc(u.how)}</div>${i ? `<button class="link small" data-qr="${i}">show QR for this one</button>` : ""}</div>`).join("") +
    "<p class=\"muted small\">If a phone cannot connect: check it is on the home Wi-Fi (not mobile data or the guest network), and that Windows allowed MeshLens on private networks (the installer option).</p>"
    : "<p class=\"bad\">Could not work out this PC's address on the home network.</p>";
  $("#phone-body").onclick = (ev) => { const b = ev.target.closest("[data-qr]"); if (b) $("#phone-body .qr").src = `/api/connect/qr.svg?i=${b.dataset.qr}`; };
}

// ---------------------------------------------------------------- export
async function renderExport(el) {
  const sets = await api("/api/export/datasets");
  const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const now = new Date();
  el.innerHTML = `<form id="exp-form">
    <div class="toolbar"><label>From <input type="datetime-local" name="from" value="${iso(new Date(now - 86400000))}"></label>
      <label>To <input type="datetime-local" name="to" value="${iso(now)}"></label>
      <span class="small">Quick: <button type="button" class="link" data-range="1">last day</button> · <button type="button" class="link" data-range="7">last week</button>
        · <button type="button" class="link" data-range="31">last month</button> · <button type="button" class="link" data-range="365">last year</button></span></div>
    <div class="exp-sets">${sets.map((s) => `<label class="inline"><input type="checkbox" name="d" value="${esc(s.key)}" ${["events", "latency", "speed"].includes(s.key) ? "checked" : ""}> ${esc(s.label)}</label>`).join("")}</div>
    <p><label class="inline">Format <select name="format"><option value="csv">CSV (Excel)</option><option value="json">JSON</option><option value="zip">ZIP of CSVs</option></select></label>
      <button type="submit">Download</button> <span id="exp-msg" class="muted small"></span></p>
    <p class="muted small">Detailed data is kept for ${esc(settingsData.retention.raw_days)} days and daily summaries for ${esc(settingsData.retention.summary_days)} days.
      Several datasets: CSV comes as a ZIP with one CSV each; JSON comes as one file with every dataset. Device and unit names are added next to MAC addresses.</p></form>`;
  const f = $("#exp-form");
  el.querySelectorAll("[data-range]").forEach((b) => (b.onclick = () => { const n = new Date(); f.to.value = iso(n); f.from.value = iso(new Date(n - b.dataset.range * 86400000)); }));
  f.onsubmit = (ev) => {
    ev.preventDefault();
    const d = [...f.querySelectorAll("input[name=d]:checked")].map((x) => x.value);
    if (!d.length) { $("#exp-msg").textContent = "Choose at least one dataset."; return; }
    const url = `/api/export?d=${d.join(",")}&from=${encodeURIComponent(f.from.value)}&to=${encodeURIComponent(f.to.value)}&format=${f.format.value}`;
    const a = document.createElement("a"); a.href = url; a.click();
    $("#exp-msg").textContent = "Preparing the download…";
  };
}

// ---------------------------------------------------------------- updates
function showUpdateBanner(u) {
  const b = $("#update-banner");
  if (!u || (!u.available && !u.state)) { b.hidden = true; return; }
  b.hidden = false;
  b.innerHTML = u.state ? `<b>Updating MeshLens:</b> ${esc(u.state)}${u.error ? ` · <span class="bad">${esc(u.error)}</span>` : ""}`
    : `<b>MeshLens ${esc(u.latest)} is available</b> <span class="muted">(you have ${esc(u.current)})</span>
       ${u.self_update ? '<button id="banner-install">Install update</button>' : '<span class="muted small">running from source: update with git pull</span>'}
       <a href="#" data-goto="settings" data-set="updates" class="small">what's new</a>${u.error ? ` <span class="bad small">${esc(u.error)}</span>` : ""}`;
  const btn = $("#banner-install");
  if (btn) btn.onclick = () => installUpdate(btn);
}

async function installUpdate(btn) {
  if (btn) btn.disabled = true;
  try { await post("/api/update/install"); } catch (e) { alert(e.message); }
  // Poll until the installer takes over; MeshLens restarts and the page reconnects.
  const t = setInterval(async () => { try { const st = await api("/api/status"); showUpdateBanner(st.update); } catch (e) { /* restarting */ } }, 2000);
  setTimeout(() => clearInterval(t), 180000);
}

async function renderUpdates(el) {
  const st = await api("/api/status"), u = st.update, cfg = settingsData.updates;
  el.innerHTML = `<div class="kv"><div>This version</div><div>${esc(u.current)}</div>
      <div>Latest release</div><div>${u.latest ? esc(u.latest) + (u.available ? ' <span class="pill good">update available</span>' : ' <span class="muted">(up to date)</span>') : '<span class="muted">not checked yet</span>'}</div>
      <div>Last checked</div><div>${esc(ago(u.checked))}${u.error ? ` · <span class="bad">${esc(u.error)}</span>` : ""}</div></div>
    <p><button id="upd-check" class="secondary">Check now</button>
      ${u.available && u.self_update ? '<button id="upd-install">Install update</button>' : ""}
      ${u.available && !u.self_update ? '<span class="muted">This copy runs from source: update it with <code>git pull</code>.</span>' : ""}
      ${u.url ? ` <a href="${esc(u.url)}" target="_blank" rel="noopener noreferrer">release page ↗</a>` : ""}</p>
    ${u.notes ? `<h4>What's new in ${esc(u.latest)}</h4><pre class="notes">${esc(u.notes)}</pre>` : ""}
    <form id="upd-form">
      <label class="inline"><input type="checkbox" name="check_automatically" ${cfg.check_automatically ? "checked" : ""}> Check for updates automatically</label>
      <label>Check every (hours) <input name="check_hours" type="number" value="${esc(cfg.check_hours)}"></label>
      <label class="inline"><input type="checkbox" name="include_prereleases" ${cfg.include_prereleases ? "checked" : ""}> Include test (pre-release) versions</label>
      <button type="submit">Save</button> <span id="upd-msg" class="muted"></span></form>
    <p class="muted small">Updates come only from the project's GitHub releases and are checked against the release's published SHA-256
      checksum before installing. Windows asks once for permission; MeshLens then closes, updates and starts again.
      Your settings, history and passwords are kept.</p>`;
  $("#upd-check").onclick = async () => { $("#upd-check").disabled = true; await post("/api/update/check"); renderUpdates(el); refreshBase(); };
  if ($("#upd-install")) $("#upd-install").onclick = () => installUpdate($("#upd-install"));
  $("#upd-form").onsubmit = async (e) => { e.preventDefault(); const f = e.target;
    try { await post("/api/settings/updates", { check_automatically: f.check_automatically.checked, check_hours: Number(f.check_hours.value), include_prereleases: f.include_prereleases.checked });
      settingsData = await api("/api/settings"); $("#upd-msg").textContent = "Saved"; } catch (err) { $("#upd-msg").textContent = err.message; } };
}

async function renderStatus(el) {
  const st = await api("/api/status");
  el.innerHTML = `<div class="kv"><div>Version</div><div>${esc(st.version)}</div><div>Running for</div><div>${esc(Math.round(st.uptime_s / 60))} min</div>
    <div>Collection</div><div>${st.paused ? "paused (tray menu)" : "running"}</div>
    <div>Router password</div><div>${st.router.password_set ? "stored" : '<span class="bad">not set</span>'}</div>
    <div>Last router poll</div><div>${esc(ago(st.asus.last_poll))}</div>
    <div>Last error</div><div class="${st.asus.error ? "bad" : ""}">${esc(st.asus.error || "none")}</div>
    <div>Settings file</div><div class="mono">${esc(settingsData.config_file)}</div></div>
    <h4>Integrations</h4><table><tr><th>Service</th><th>Enabled</th><th>Password / token</th><th>Collector</th></tr>
    ${Object.entries(st.integrations).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.enabled ? "yes" : "no"}</td><td>${v.secret_set ? "stored" : "not set"}</td><td class="muted">${esc(v.collector)}</td></tr>`).join("")}</table>
    <h4>Collector detail</h4><pre class="small">${esc(JSON.stringify(st.asus, null, 2))}</pre>
    <button id="collect">Collect now</button> <span id="collect-msg" class="muted"></span>`;
  $("#collect").onclick = () => post("/api/collect").then(() => ($("#collect-msg").textContent = "Collecting… refresh in a minute"));
}

// ---------------------------------------------------------------- help
let helpLoaded = false;
async function loadHelp() {
  if (helpLoaded) return;
  const res = await fetch("/static/help.html", { credentials: "same-origin" });
  $("#help-body").innerHTML = await res.text();   // our own static file
  helpLoaded = true;
}

// ---------------------------------------------------------------- wiring
document.addEventListener("click", (e) => {
  const help = e.target.closest("[data-help]");
  if (help) { e.preventDefault(); $("#drawer").hidden = true; showTab("help", help.dataset.help); return; }
  const go = e.target.closest("[data-goto]");
  if (go) { e.preventDefault(); if (go.dataset.set) currentSection = go.dataset.set; showTab(go.dataset.goto); return; }
  const set = e.target.closest("[data-set]");
  if (set) { loadSettings(set.dataset.set); return; }
  const a = e.target.closest("[data-mac]");
  if (a && a.dataset.mac) { e.preventDefault(); openDevice(a.dataset.mac); return; }
  const t = e.target.closest("#tabs button");
  if (t) showTab(t.dataset.tab);
});
document.addEventListener("DOMContentLoaded", () => {
  $("#drawer-close").onclick = () => ($("#drawer").hidden = true);
  $("#logout").onclick = async () => { await post("/api/auth/logout"); showLogin(); };
  $("#phone-btn").onclick = showPhone;
  $("#phone-close").onclick = () => ($("#phone-modal").hidden = true);
  $("#phone-modal").onclick = (ev) => { if (ev.target.id === "phone-modal") $("#phone-modal").hidden = true; };
  ["#dev-search", "#dev-online", "#dev-watched", "#dev-unit", "#dev-band", "#dev-type", "#dev-problems"].forEach((s) => $(s).addEventListener("input", loadDevices));
  ["#ev-kind", "#ev-hours", "#ev-unit", "#ev-band"].forEach((s) => $(s).addEventListener("change", loadEvents));
  $("#ev-dev").addEventListener("change", loadEvents);
  $("#ev-dev").addEventListener("search", loadEvents);
  $("#devices-table").addEventListener("click", (ev) => { const th = ev.target.closest("th[data-sort]"); if (!th) return;
    ev.stopPropagation(); devSort = { key: th.dataset.sort, dir: devSort.key === th.dataset.sort ? -devSort.dir : 1 }; loadDevices(); });
  ["#an-hours", "#an-sev"].forEach((s) => $(s).addEventListener("change", loadAnalysis));
  start();
  setInterval(() => { const t = document.querySelector("#tabs button.on"); if (t && !$("#tabs").hidden && $("#drawer").hidden) ({ overview: loadOverview, devices: loadDevices })[t.dataset.tab]?.(); }, 60000);
});
