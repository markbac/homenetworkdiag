"use strict";
// MeshLens web UI. No framework; every value from the API is escaped before it reaches the page.

const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
const ago = (ts) => { if (!ts) return "never"; const s = Date.now() / 1000 - ts; return s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`; };
const rssiClass = (r) => r == null ? "" : r >= -60 ? "good" : r >= -70 ? "" : r >= -78 ? "warn" : "bad";
const bandLabel = (b) => b ? `${b} GHz` : "wired";
const KIND = { connect: "Connected", disconnect: "Disconnected", kick: "Kicked (weak signal)", roam_candidate: "Roam attempt",
  roam: "Roam result", moved_away: "Moved to another unit", leave: "Left (signal)", assoc_failed: "Association failed" };

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
function devName(mac) { const d = devicesByMac[mac]; return d ? (d.user_name || d.name || mac) : mac; }

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
      $("#login-pw").value = ""; start(); }
    catch (err) { $("#login-error").textContent = err.message; }
  };
}

// ---------------------------------------------------------------- tabs
function showTab(name) {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== name));
  location.hash = name;
  ({ overview: loadOverview, devices: loadDevices, events: loadEvents, settings: loadSettings })[name]?.();
}

async function start() {
  const st = await api("/api/auth/state");
  if (!st.logged_in) return showLogin();
  $("#login").hidden = true; $("#tabs").hidden = false; $("#logout").hidden = false;
  await refreshBase();
  showTab((location.hash || "#overview").slice(1));
}

async function refreshBase() {
  const [units, devs, status] = await Promise.all([api("/api/units"), api("/api/devices"), api("/api/status")]);
  unitsByMac = Object.fromEntries(units.map((u) => [u.mac, u])); indexUnits(units);
  devicesByMac = Object.fromEntries(devs.map((d) => [d.mac, d]));
  $("#poll").textContent = status.asus.last_poll ? `Router polled ${ago(status.asus.last_poll)}` : "Waiting for first router poll";
  return { units, devs, status };
}

// ---------------------------------------------------------------- overview
async function loadOverview() {
  const { units } = await refreshBase();
  $("#units-table").innerHTML = "<tr><th>Unit</th><th>Model</th><th>IP</th><th>Chip</th><th>Status</th></tr>" +
    units.map((u) => `<tr><td>${esc(u.alias || u.model || u.mac)}</td><td>${esc(u.model)}</td><td>${esc(u.ip)}</td><td>${esc(u.chip)}</td>
      <td class="${u.reachable ? "good" : "bad"}">${u.reachable ? "reachable" : esc(u.last_error || "not reachable")}</td></tr>`).join("");
  const radios = await api("/api/radios");
  $("#radio-table").innerHTML = "<tr><th>Unit</th><th>Band</th><th>Tx failures</th><th>Rx errors</th><th>Temp</th><th>Noise</th></tr>" +
    radios.map((r) => `<tr><td>${esc(unitName(r.unit))}</td><td>${esc(bandLabel(r.band))}</td>
      <td class="${r.tx_fail_pct > 10 ? "bad" : r.tx_fail_pct > 5 ? "warn" : ""}">${r.tx_fail_pct ?? ""}${r.tx_fail_pct != null ? "%" : ""}</td>
      <td class="${r.rx_crc_pct > 10 ? "bad" : r.rx_crc_pct > 5 ? "warn" : ""}">${r.rx_crc_pct ?? ""}${r.rx_crc_pct != null ? "%" : ""}</td>
      <td>${r.temperature != null ? esc(r.temperature) + " °C" : ""}</td><td>${r.noise != null ? esc(r.noise) + " dBm" : ""}</td></tr>`).join("");
  const fs = await api("/api/findings?hours=24");
  $("#findings").innerHTML = fs.length ? fs.slice(0, 30).map((f) => `<div class="finding ${esc(f.severity)}">
      <b><a href="#" data-mac="${esc(f.mac)}">${esc(devName(f.mac))}</a></b> <span class="muted small">${esc(fmtTime(f.from))} to ${esc(fmtTime(f.to))}</span><br>${esc(f.text)}</div>`).join("")
    : '<p class="muted">No kick loops, roam storms or flapping devices.</p>';
  const st = await api("/api/stability?hours=24");
  $("#stability-table").innerHTML = "<tr><th>Device</th><th>Kicks</th><th>Roam attempts</th><th>Disconnects</th><th>Weakest signal</th><th>Units</th></tr>" +
    st.slice(0, 15).map((s) => `<tr data-mac="${esc(s.mac)}"><td><a href="#" data-mac="${esc(s.mac)}">${esc(devName(s.mac))}</a></td>
      <td class="${s.kicks ? "bad" : ""}">${s.kicks}</td><td>${s.roam_attempts}</td><td>${s.disconnects}</td>
      <td class="${rssiClass(s.weakest_rssi)}">${s.weakest_rssi ?? ""}</td><td>${esc(s.units.map(unitName).join(", "))}</td></tr>`).join("");
}

// ---------------------------------------------------------------- devices
async function loadDevices() {
  const { devs } = await refreshBase();
  const q = $("#dev-search").value.toLowerCase(), online = $("#dev-online").checked, watched = $("#dev-watched").checked;
  const rows = devs.filter((d) => (!online || d.live || (d.wired && Date.now() / 1000 - d.last_seen < 900))
    && (!watched || d.watched)
    && (!q || [d.user_name, d.name, d.ip, d.mac, d.vendor, d.owner, d.room].join(" ").toLowerCase().includes(q)));
  $("#dev-count").textContent = `${rows.length} of ${devs.length}`;
  $("#devices-table").innerHTML = "<thead><tr><th>Name</th><th>IP</th><th>Vendor</th><th>Connection</th><th>Signal</th><th>Rate tx/rx</th><th>Security</th><th>Last seen</th></tr></thead><tbody>" +
    rows.map((d) => { const l = d.live || {};
      return `<tr data-mac="${esc(d.mac)}"><td>${d.watched ? "&#9733; " : ""}${esc(d.user_name || d.name || d.mac)}${d.randomised ? ' <span class="pill">private MAC</span>' : ""}</td>
      <td>${esc(d.ip)}</td><td>${esc(d.vendor)}</td>
      <td>${d.wired ? "wired" : esc(`${bandLabel(l.band || d.band)} · ${unitName(l.unit || d.unit)}`)}</td>
      <td class="${rssiClass(l.rssi)}">${l.rssi ?? ""}</td><td>${l.tx_rate != null ? esc(`${l.tx_rate}/${l.rx_rate ?? "?"}`) : ""}</td>
      <td>${esc(d.wifi_auth)}</td><td class="muted">${esc(ago(d.last_seen))}</td></tr>`; }).join("") + "</tbody>";
}

// ---------------------------------------------------------------- device drawer
async function openDevice(mac) {
  const d = await api(`/api/devices/${encodeURIComponent(mac)}?hours=24`);
  const last = d.history[d.history.length - 1] || {};
  const s = d.stability || {};
  $("#drawer-body").innerHTML = `
    <h2>${esc(d.user_name || d.name || d.mac)}</h2>
    <div class="kv">
      <div>MAC</div><div class="mono">${esc(d.mac)}${d.randomised ? " (private / randomised)" : ""}</div>
      <div>IP</div><div>${esc(d.ip)}</div>
      <div>Vendor</div><div>${esc(d.vendor)} <span class="muted small">${esc(d.vendor_src)}</span></div>
      <div>Detected name</div><div>${esc(d.name)} <span class="muted small">${esc(d.name_src)}</span></div>
      <div>Connection</div><div>${d.wired ? "wired" : esc(`${bandLabel(last.band || d.band)} via ${unitName(last.unit || d.unit)}${last.iface ? " (" + last.iface + ")" : ""}`)}</div>
      <div>Signal now</div><div class="${rssiClass(last.rssi)}">${last.rssi != null ? esc(last.rssi) + " dBm" : "n/a"}</div>
      <div>Rate tx / rx</div><div>${last.tx_rate != null ? esc(`${last.tx_rate} / ${last.rx_rate} Mbit/s`) : "n/a"}</div>
      <div>Wi-Fi security</div><div>${esc(d.wifi_auth)}</div>
      <div>Last 24 h</div><div>${s.mac ? esc(`${s.kicks} kicks, ${s.roam_attempts} roam attempts, ${s.disconnects} disconnects`) : "no Wi-Fi events"}</div>
    </div>
    <h3>Signal, last 24 h</h3><canvas class="spark" id="spark"></canvas>
    ${d.findings.length ? "<h3>Findings</h3>" + d.findings.map((f) => `<div class="finding ${esc(f.severity)}">${esc(f.text)} <span class="muted small">${esc(fmtTime(f.to))}</span></div>`).join("") : ""}
    <h3>Journey (handovers, kicks, connects)</h3>
    <ul class="timeline">${d.journey.slice(-150).reverse().map((j) => `<li class="${esc(j.kind)}"><span class="muted small">${esc(fmtTime(j.ts))}</span>
      ${esc(KIND[j.kind] || j.kind)} · ${esc(unitName(j.unit))}${j.band ? " · " + esc(bandLabel(j.band)) : ""}
      ${j.detail.rssi != null ? ` · <span class="${rssiClass(j.detail.rssi)}">${esc(j.detail.rssi)} dBm</span>` : ""}
      ${j.detail.target ? " → " + esc(unitName(j.detail.target) || j.detail.target) + (j.detail.target_rssi != null ? ` (${esc(j.detail.target_rssi)} dBm)` : "") : ""}
      ${j.detail.new_unit ? " → " + esc(unitName(j.detail.new_unit)) : ""}
      ${j.offline_s != null ? ` <span class="muted small">after ${esc(j.offline_s)} s offline</span>` : ""}</li>`).join("") || '<li class="muted">No Wi-Fi events in the last 24 h</li>'}</ul>
    <h3>Your notes (enrichment)</h3>
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
  drawSpark($("#spark"), d.history.map((h) => [h.ts, h.rssi]).filter((p) => p[1] != null));
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

// ---------------------------------------------------------------- events
async function loadEvents() {
  await refreshBase();
  const kind = $("#ev-kind").value, hours = $("#ev-hours").value;
  const evs = await api(`/api/events?hours=${hours}&limit=1000${kind ? "&kind=" + kind : ""}`);
  $("#events-table").innerHTML = "<tr><th>Time</th><th>Device</th><th>Event</th><th>Unit</th><th>Band</th><th>Detail</th></tr>" +
    evs.map((e) => `<tr><td class="muted">${esc(fmtTime(e.ts))}</td><td><a href="#" data-mac="${esc(e.mac)}">${esc(devName(e.mac))}</a></td>
      <td>${esc(KIND[e.kind] || e.kind)}</td><td>${esc(unitName(e.unit))}</td><td>${esc(e.iface ? bandLabel(e.band) : "")}</td>
      <td class="wrap small">${e.detail.rssi != null ? esc(e.detail.rssi) + " dBm " : ""}${e.detail.target ? "→ " + esc(unitName(e.detail.target)) + " " : ""}${e.detail.new_unit ? "→ " + esc(unitName(e.detail.new_unit)) : ""}${e.detail.status != null ? " status " + esc(e.detail.status) : ""}</td></tr>`).join("");
}

// ---------------------------------------------------------------- settings
async function loadSettings() {
  const st = await api("/api/status");
  const f = $("#router-form");
  f.host.value = st.router.host; f.port.value = st.router.port; f.username.value = st.router.username;
  $("#pw-state").textContent = st.router.password_set ? "set" : "not set";
  $("#status-pre").textContent = JSON.stringify(st.asus, null, 2);
  f.onsubmit = async (e) => {
    e.preventDefault();
    const body = { host: f.host.value, port: Number(f.port.value), username: f.username.value, clear_password: f.clear_password.checked };
    if (f.password.value) body.password = f.password.value;
    const r = await post("/api/settings/router", body);
    f.password.value = ""; f.clear_password.checked = false;
    $("#pw-state").textContent = r.password_set ? "set" : "not set"; $("#router-msg").textContent = "Saved, collecting...";
  };
}

// ---------------------------------------------------------------- wiring
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-mac]");
  if (a && a.dataset.mac) { e.preventDefault(); openDevice(a.dataset.mac); }
  const t = e.target.closest("nav button");
  if (t) showTab(t.dataset.tab);
});
document.addEventListener("DOMContentLoaded", () => {
  $("#drawer-close").onclick = () => ($("#drawer").hidden = true);
  $("#logout").onclick = async () => { await post("/api/auth/logout"); showLogin(); };
  $("#collect").onclick = () => post("/api/collect").then(() => ($("#router-msg").textContent = "Collecting..."));
  ["#dev-search", "#dev-online", "#dev-watched"].forEach((s) => $(s).addEventListener("input", loadDevices));
  ["#ev-kind", "#ev-hours"].forEach((s) => $(s).addEventListener("change", loadEvents));
  start();
  setInterval(() => { const t = document.querySelector("nav button.on"); if (t && !$("#tabs").hidden && $("#drawer").hidden) ({ overview: loadOverview, devices: loadDevices })[t.dataset.tab]?.(); }, 60000);
});
