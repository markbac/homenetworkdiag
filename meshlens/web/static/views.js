"use strict";
// Topology, Tests (monitors, speed tests) and discovery views. Uses helpers from app.js.

const LINK_TYPE = { wan: "Internet line", ethernet: "Ethernet", powerline: "Powerline", wireless: "Wi-Fi backhaul",
  "wireless (preferred parent)": "Wi-Fi backhaul (from its settings)", "ethernet or unknown": "Ethernet or unknown", unknown: "unknown", wifi: "Wi-Fi" };
const KIND_ICON = { internet: "🌐", router: "◎", node: "◉", powerline: "⚡", device: "•" };

function linkText(l) {
  if (!l || !l.type) return "";
  if (l.type === "wireless" || l.type === "wifi") {
    const parts = [LINK_TYPE[l.type], l.band ? `${l.band} GHz` : "", l.rssi != null ? `<span class="${rssiClass(l.rssi)}">${esc(l.rssi)} dBm</span>` : "",
      l.tx_rate ? `${esc(Math.round(l.tx_rate))} Mbit/s` : ""];
    return parts.filter(Boolean).join(" · ");
  }
  if (l.type === "powerline") {
    const cls = l.slowest == null ? "" : l.slowest < 20 ? "bad" : l.slowest < 50 ? "warn" : "good";
    return `Powerline <span class="${cls}">↓ ${esc(l.down_mbps ?? "?")} / ↑ ${esc(l.up_mbps ?? "?")} Mbit/s</span>`;
  }
  return esc(LINK_TYPE[l.type] || l.type) + (l.via ? ` via ${esc(l.via)}` : "");
}

async function loadTopology() {
  await refreshBase();
  const [t, p] = await Promise.all([api("/api/topology"), api("/api/powerline")]);
  const showDev = $("#topo-devices").checked, onlyWeak = $("#topo-weak").checked;
  const kids = {};
  for (const n of t.nodes) (kids[n.parent || ""] ||= []).push(n);
  const infra = (n) => n.kind !== "device";
  const devOk = (n) => !onlyWeak || (n.health != null && n.health < 80) || (n.link.rssi != null && n.link.rssi < -70);
  const render = (n) => {
    const children = kids[n.id] || [];
    const sub = children.filter(infra), devs = children.filter((c) => !infra(c) && devOk(c));
    const h = n.health && n.health.score != null ? ` <span class="pill ${scoreClass(n.health.score)}">${esc(n.health.score)}</span>` : "";
    const meta = [n.model, n.ip].filter(Boolean).map(esc).join(" · ");
    const devList = showDev && devs.length ? `<details class="topo-devs" ${devs.length <= 8 ? "open" : ""}><summary>${devs.length} device${devs.length > 1 ? "s" : ""}
        <span class="muted small">${devs.filter((d) => d.link.type === "wifi").length} Wi-Fi · ${devs.filter((d) => d.link.type !== "wifi").length} wired/other</span></summary>
        <div class="chips">${devs.sort((a, b) => (a.link.rssi ?? 0) - (b.link.rssi ?? 0)).map((d) => `<a href="#" class="chip" data-mac="${esc(d.mac)}" title="${esc([d.ip, d.vendor, d.dtype].filter(Boolean).join(" · "))}">
          ${d.watched ? "★ " : ""}${esc(d.label)} <span class="muted small">${linkText(d.link)}</span>${d.health != null && d.health < 60 ? ' <span class="bad">●</span>' : ""}</a>`).join("")}</div></details>` : "";
    return `<li><div class="tnode ${esc(n.kind)} ${n.reachable === 0 ? "down" : ""}"><span class="ticon">${KIND_ICON[n.kind] || "•"}</span>
        <div><b>${esc(n.label)}</b>${h}<div class="muted small">${meta}</div>${n.parent ? `<div class="tlink small">${linkText(n.link)}</div>` : ""}</div></div>
        ${devList}${sub.length ? `<ul>${sub.map(render).join("")}</ul>` : ""}</li>`;
  };
  const root = t.nodes.find((n) => n.kind === "internet");
  $("#topo-tree").innerHTML = root ? `<ul class="tree">${render(root)}</ul>` : '<p class="muted">No data yet: the map appears after the first router poll.</p>';
  $("#topo-meta").textContent = `${t.nodes.filter((n) => n.kind === "device").length} devices online · built ${fmtTime(t.generated)}`;

  if (p.adapters.length) {
    const name = Object.fromEntries(p.adapters.map((a) => [a.mac, a.name || a.product || a.mac]));
    $("#plc-body").className = "";
    $("#plc-body").innerHTML = `<table><tr><th>Adapter</th><th>Model</th><th>Address</th><th>Firmware</th><th>Devices behind it</th></tr>
      ${p.adapters.map((a) => `<tr><td>${esc(a.name)} ${a.attached_to_router ? '<span class="pill">wired to router</span>' : ""}</td><td>${esc(a.product)}</td>
        <td>${esc(a.ip)}</td><td class="muted small">${esc((a.firmware || "").split("_")[0])}</td><td>${a.attached_to_router ? '<span class="muted">whole network</span>' : esc(a.bridged.length)}</td></tr>`).join("")}</table>
      <h4>Links (PHY rate, real throughput is roughly half)</h4>
      <table><tr><th>Between</th><th>One way</th><th>Other way</th><th>Slowest</th></tr>
      ${p.links.map((l) => `<tr><td>${esc(l.a_name)} ↔ ${esc(l.b_name)}</td><td>${esc(l.a_to_b)} Mbit/s</td><td>${esc(l.b_to_a)} Mbit/s</td>
        <td class="${l.slowest < 20 ? "bad" : l.slowest < 50 ? "warn" : "good"}">${esc(l.slowest)} Mbit/s</td></tr>`).join("")}</table>
      <p class="muted small">Read ${esc(ago(p.status.last_poll))} from ${esc(p.status.asked || "?")}${p.status.error ? ` · <span class="bad">${esc(p.status.error)}</span>` : ""}</p>`;
  } else {
    $("#plc-body").innerHTML = `No powerline adapters read yet. ${p.status.error ? esc(p.status.error) : ""} Add an adapter address under Settings → Powerline if they aren't found.`;
  }
}

// ---------------------------------------------------------------- tests
let monTimer = null;
async function loadTests() {
  await refreshBase();
  const live = await api("/api/monitor/live");
  $("#mon-live").innerHTML = "<tr><th>Target</th><th>What</th><th>Last</th><th>Average</th><th>Worst (p95)</th><th>Jitter</th><th>Loss</th><th>Last 5 min</th></tr>" +
    (live.map((t, i) => { const loss = t.sent ? (100 * t.lost) / t.sent : 0;
      return `<tr><td>${esc(t.label)}</td><td class="muted small">${esc(t.kind)}</td><td>${t.last_ms != null ? esc(t.last_ms) + " ms" : '<span class="bad">lost</span>'}</td>
      <td>${t.avg_ms != null ? esc(t.avg_ms) + " ms" : ""}</td><td class="${t.p95_ms > 100 ? "bad" : t.p95_ms > 40 ? "warn" : ""}">${t.p95_ms != null ? esc(t.p95_ms) + " ms" : ""}</td>
      <td class="${t.jitter_ms > 30 ? "bad" : t.jitter_ms > 10 ? "warn" : ""}">${t.jitter_ms != null ? esc(t.jitter_ms) + " ms" : ""}</td>
      <td class="${loss >= 2 ? "bad" : loss > 0 ? "warn" : ""}">${loss.toFixed(1)}%</td><td><canvas class="mini" data-i="${i}"></canvas></td></tr>`; }).join("")
      || '<tr><td colspan="8" class="muted">Monitor starting…</td></tr>');
  document.querySelectorAll("canvas.mini").forEach((c) => drawMini(c, live[c.dataset.i].series));
  const sel = $("#mon-target"), cur = sel.value;
  sel.innerHTML = live.map((t) => `<option value="${esc(t.target)}">${esc(t.label)}</option>`).join("");
  if (cur && live.some((t) => t.target === cur)) sel.value = cur;
  await loadMonitorHistory();
  await loadSpeed();
  clearInterval(monTimer);
  monTimer = setInterval(() => { if (!$("#tests").hidden && $("#drawer").hidden) loadTestsLive(); else clearInterval(monTimer); }, 10000);
}
async function loadTestsLive() {
  const live = await api("/api/monitor/live");
  document.querySelectorAll("canvas.mini").forEach((c) => live[c.dataset.i] && drawMini(c, live[c.dataset.i].series));
}

function drawMini(canvas, series) {
  const ctx = canvas.getContext("2d"), w = (canvas.width = 240), h = (canvas.height = 48), css = getComputedStyle(document.body);
  ctx.clearRect(0, 0, w, h);
  const vals = series.map((s) => s[1]).filter((v) => v != null), max = Math.max(5, ...vals);
  series.forEach(([, v], i) => { const x = (i / Math.max(1, series.length - 1)) * (w - 2) + 1;
    if (v == null) { ctx.fillStyle = css.getPropertyValue("--bad"); ctx.fillRect(x - 1, 0, 2, h); return; }
    const bh = Math.max(2, (v / max) * (h - 4)); ctx.fillStyle = css.getPropertyValue("--accent"); ctx.fillRect(x - 1, h - bh, 2, bh); });
}

async function loadMonitorHistory() {
  const target = $("#mon-target").value; if (!target) return;
  const hist = await api(`/api/monitor/history?hours=${$("#mon-hours").value}&target=${encodeURIComponent(target)}`);
  const rows = (hist[0] || {}).rows || [];
  const c = $("#mon-chart"), ctx = c.getContext("2d"), w = (c.width = c.clientWidth * 2), h = (c.height = 360), css = getComputedStyle(document.body), col = (v) => css.getPropertyValue(v).trim();
  ctx.clearRect(0, 0, w, h); ctx.font = "22px system-ui";
  if (rows.length < 2) { ctx.fillStyle = col("--muted"); ctx.fillText("Collecting: history appears after a couple of minutes.", 20, h / 2); return; }
  const pad = 50, t0 = rows[0][0], t1 = rows[rows.length - 1][0], max = Math.max(5, ...rows.map((r) => Math.min(r[2] ?? 0, 1000)));
  const x = (t) => pad + ((t - t0) / Math.max(60, t1 - t0)) * (w - 2 * pad), y = (v) => h - pad - (Math.min(v, max) / max) * (h - 2 * pad);
  ctx.strokeStyle = col("--line"); ctx.fillStyle = col("--muted");
  for (const f of [0, 0.5, 1]) { ctx.beginPath(); ctx.moveTo(pad, y(max * f)); ctx.lineTo(w - pad, y(max * f)); ctx.stroke(); ctx.fillText(`${Math.round(max * f)} ms`, 0, y(max * f) - 4); }
  for (let i = 0; i < 5; i++) { const t = t0 + ((t1 - t0) * i) / 4; ctx.fillText(new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), x(t) - 28, h - 12); }
  rows.forEach((r) => { if (r[4] > 0) { ctx.fillStyle = col("--bad"); ctx.globalAlpha = Math.min(1, 0.25 + r[4] / r[5]); ctx.fillRect(x(r[0]) - 2, pad, 4, h - 2 * pad); ctx.globalAlpha = 1; } });
  for (const [idx, c2, lw] of [[2, "--warn", 1.5], [1, "--accent", 3]]) {
    ctx.strokeStyle = col(c2); ctx.lineWidth = lw; ctx.beginPath(); let on = false;
    rows.forEach((r) => { if (r[idx] == null) { on = false; return; } on ? ctx.lineTo(x(r[0]), y(r[idx])) : ctx.moveTo(x(r[0]), y(r[idx])); on = true; }); ctx.stroke(); }
}

async function loadSpeed() {
  const tests = await api("/api/speedtests?days=30");
  const ok = tests.filter((t) => !t.error), last = tests[tests.length - 1];
  $("#speed-last").innerHTML = last ? `<div class="speed-cards">
      <div><div class="big">${esc(last.down_mbps ?? "–")}</div><div class="muted small">Mbit/s down</div></div>
      <div><div class="big">${esc(last.up_mbps ?? "–")}</div><div class="muted small">Mbit/s up</div></div>
      <div><div class="big">${esc(last.idle_ms ?? "–")}</div><div class="muted small">ms idle</div></div>
      <div><div class="big">${esc(last.down_loaded_ms ?? "–")} / ${esc(last.up_loaded_ms ?? "–")}</div><div class="muted small">ms while downloading / uploading</div></div>
      <div><div class="big grade g-${esc((last.grade || "").replace("+", "p"))}">${esc(last.grade || "–")}</div><div class="muted small">bufferbloat</div></div>
    </div><p class="muted small">${esc(fmtTime(last.ts))} · ${esc(last.trigger)} · ${esc(last.server)}${last.error ? ` · <span class="bad">${esc(last.error)}</span>` : ""}</p>`
    : '<p class="muted">No speed tests yet. Scheduled tests run every few hours (Settings → Monitors and speed tests).</p>';
  const c = $("#speed-chart"), ctx = c.getContext("2d"), w = (c.width = c.clientWidth * 2), h = (c.height = 300), css = getComputedStyle(document.body), col = (v) => css.getPropertyValue(v).trim();
  ctx.clearRect(0, 0, w, h); ctx.font = "22px system-ui";
  if (ok.length >= 2) {
    const pad = 50, max = Math.max(1, ...ok.flatMap((t) => [t.down_mbps || 0, t.up_mbps || 0])), t0 = ok[0].ts, t1 = ok[ok.length - 1].ts;
    const x = (t) => pad + ((t - t0) / Math.max(1, t1 - t0)) * (w - 2 * pad), y = (v) => h - pad - (v / max) * (h - 2 * pad);
    ctx.fillStyle = col("--muted"); [0, 0.5, 1].forEach((f) => ctx.fillText(`${Math.round(max * f)}`, 0, y(max * f)));
    for (const [k, cc] of [["down_mbps", "--accent"], ["up_mbps", "--good"]]) { ctx.strokeStyle = col(cc); ctx.lineWidth = 3; ctx.beginPath();
      ok.forEach((t, i) => (i ? ctx.lineTo(x(t.ts), y(t[k] || 0)) : ctx.moveTo(x(t.ts), y(t[k] || 0)))); ctx.stroke(); }
  } else { ctx.fillStyle = col("--muted"); ctx.fillText("The chart appears after two speed tests.", 20, h / 2); }
  $("#speed-table").innerHTML = "<tr><th>When</th><th>Down</th><th>Up</th><th>Idle</th><th>Loaded (down / up)</th><th>Bufferbloat</th><th></th></tr>" +
    tests.slice(-20).reverse().map((t) => `<tr><td class="muted">${esc(fmtTime(t.ts))}</td><td>${esc(t.down_mbps ?? "")}</td><td>${esc(t.up_mbps ?? "")}</td>
      <td>${esc(t.idle_ms ?? "")}</td><td>${esc(t.down_loaded_ms ?? "")} / ${esc(t.up_loaded_ms ?? "")}</td><td>${esc(t.grade || "")}</td>
      <td class="small ${t.error ? "bad" : "muted"}">${esc(t.error || t.trigger)}</td></tr>`).join("");
}

// ---------------------------------------------------------------- device drawer extras (ports, services, names)
function deviceExtras(d) {
  const names = Object.entries(d.names || {}).map(([k, v]) => `${esc(v)} <span class="muted small">${esc(k)}</span>`).join(", ");
  const ports = d.ports || [];
  return `<h3>Discovery <button class="help" data-help="discovery">?</button></h3>
    <div class="kv"><div>Type</div><div>${esc(d.user_type || d.dtype || "unknown")}${d.model ? " · " + esc(d.model) : ""}</div>
      <div>Names seen</div><div>${names || '<span class="muted">none yet</span>'}</div>
      <div>Services</div><div>${(d.services || []).map((s) => `<span class="pill">${esc(s)}</span>`).join(" ") || '<span class="muted">none advertised</span>'}</div>
      <div>Last answered</div><div>${esc(ago(d.last_alive))}</div>
      <div>Ports scanned</div><div>${esc(ago(d.ports_scanned))} <button class="secondary small-btn" id="dev-scan" data-scan="${esc(d.mac)}">Scan ports now</button></div></div>
    ${ports.length ? `<table><tr><th>Port</th><th>Service</th><th>Details</th></tr>${ports.map((p) => `<tr><td>${esc(p.port)}/${esc(p.proto)}</td><td>${esc(p.service || "")}</td>
      <td class="wrap small">${p.title ? esc(p.title) + " " : ""}${p.banner ? `<span class="muted">${esc(p.banner)}</span>` : ""}
      ${[80, 81, 443, 8080, 8443, 8000, 8123, 5000, 5001, 9443, 8888, 3000, 9000].includes(p.port) && d.ip ? ` <a href="${esc((p.port === 443 || p.port === 8443 || p.port === 5001 || p.port === 9443 ? "https" : "http") + "://" + d.ip + ":" + p.port + "/")}" target="_blank" rel="noopener noreferrer">open ↗</a>` : ""}
      ${p.risk ? `<div class="warn">${esc(p.risk)}</div>` : ""}</td></tr>`).join("")}</table>` : '<p class="muted small">No open ports found (or not scanned yet).</p>'}
    ${(d.inventory || []).length ? `<h4>Changes</h4><ul class="small">${d.inventory.slice(0, 10).map((e) => `<li>${esc(fmtTime(e.ts))} · ${esc(e.kind.replace(/_/g, " "))} ${e.detail.port ? esc(e.detail.port) : ""} ${esc(e.detail.service || "")}</li>`).join("")}</ul>` : ""}`;
}

// ---------------------------------------------------------------- registration
LOADERS.topology = () => loadTopology();
LOADERS.tests = () => loadTests();
document.addEventListener("DOMContentLoaded", () => {
  ["#topo-devices", "#topo-weak"].forEach((s) => $(s).addEventListener("change", loadTopology));
  $("#mon-hours").addEventListener("change", loadMonitorHistory);
  $("#mon-target").addEventListener("change", loadMonitorHistory);
  $("#speed-run").onclick = async () => {
    $("#speed-run").disabled = true; $("#speed-msg").textContent = "Running (about 30 s): idle latency, then download, then upload…";
    try { const r = await post("/api/speedtest"); $("#speed-msg").textContent = r.error ? r.error : `Done: ${r.down_mbps} / ${r.up_mbps} Mbit/s, bufferbloat ${r.grade}`; }
    catch (e) { $("#speed-msg").textContent = e.message; }
    $("#speed-run").disabled = false; loadSpeed();
  };
  for (const [id, what, msg] of [["#scan-sweep", "sweep", "Finding devices (about 10 s)…"], ["#scan-services", "services", "Listening for names and services (about 20 s)…"],
    ["#scan-ports", "ports", "Scanning ports on every device seen today (a few minutes)…"]])
    $(id).onclick = async () => { await post("/api/discovery/run", { what }); $("#scan-msg").textContent = msg + " Refresh to see results."; };
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-scan]");
    if (b) { b.disabled = true; b.textContent = "Scanning… reopen in a minute"; await post("/api/discovery/run", { what: "ports", mac: b.dataset.scan }); }
  });
});
