"use strict";
// Graphs tab and per-device graphs (uses Charts from charts.js and helpers from app.js).

const HEAT = (ms) => ms < 25 ? "--good" : ms < 50 ? "--c5" : ms < 100 ? "--warn" : "--bad";

async function loadGraphs() {
  await refreshBase();
  const hours = $("#gr-hours").value;
  const g = await api(`/api/graphs?hours=${hours}`);
  const C = Charts;
  C.time($("#gr-clients"), { series: g.clients.by_unit, type: "area", stacked: true, unit: "devices",
    empty: "Appears after a few router polls." });
  C.time($("#gr-bands"), { series: g.clients.by_band, type: "line", unit: "devices" });
  C.time($("#gr-events"), { series: g.events, type: "bars", stacked: true, unit: "",
    colors: { Kicks: "--bad", "Roam attempts": "--c2", Disconnects: "--warn", "Failed joins": "--c6" } });
  C.time($("#gr-wan"), { series: g.traffic.wan, type: "area", unit: "Mbit/s", colors: { Download: "--c1", Upload: "--c3" },
    empty: "Internet traffic appears after two router polls." });
  $("#gr-wan-note").textContent = g.traffic.wan_note || (g.traffic.wan_iface ? `Router interface ${g.traffic.wan_iface}, averaged per poll.` : "");
  renderTopUsers($("#gr-top"), g.top_users);
  renderSwitchTraffic(g.switches || {});
  C.time($("#gr-wifi"), { series: g.traffic.wifi, type: "line", unit: "Mbit/s" });
  C.time($("#gr-latency"), { series: g.latency.avg, type: "line", unit: "ms", refLines: [{ y: 100, label: "100 ms" }] });
  C.time($("#gr-loss"), { series: { "Packet loss (router + internet)": g.latency.loss_pct }, type: "bars", unit: "%",
    colors: { "Packet loss (router + internet)": "--bad" }, empty: "No packet loss recorded." });
  const hm = g.heatmap;
  C.heatmap($("#gr-heat"), { rows: hm.days, cols: 24, colLabels: [...Array(24).keys()].map((h) => `${h}:00`),
    cell: (r, c) => { const v = hm.grid[r][c];
      return { v: v.p95, tip: v.p95 == null ? `${hm.days[r]} ${c}:00 – no data` : `${hm.days[r]} ${c}:00 – typical worst ${v.p95} ms, loss ${v.loss_pct ?? 0}% (${v.minutes} min)` }; },
    scale: (v) => C.css(HEAT(v)) });
  C.bars($("#gr-rssi"), g.rssi.map((b) => ({ label: `${b.from} to ${b.to} dBm`, short: `${b.from}`, value: b.count,
    color: b.from >= -60 ? "--good" : b.from >= -70 ? "--c5" : b.from >= -78 ? "--warn" : "--bad" })), { empty: "No Wi-Fi devices connected right now." });
  C.time($("#gr-radio"), { series: g.radio_errors, type: "line", unit: "%", refLines: [{ y: 10, label: "10% – interference" }] });
  C.time($("#gr-air"), { series: g.air, type: "line", unit: "%", yMin: 0, yMax: 100, refLines: [{ y: 50, label: "50% busy" }],
    empty: "Appears once this PC's Wi-Fi card has surveyed the air (every 2 minutes)." });
  C.time($("#gr-lan"), { series: g.lan_noise, type: "line", unit: "/s", empty: "Appears after a couple of minutes." });
  const U = g.units || {};
  C.time($("#gr-ucpu"), { series: U.core || {}, type: "line", unit: "%", yMin: 0, yMax: 100, refLines: [{ y: 95, label: "95%" }] });
  C.time($("#gr-umem"), { series: U.mem || {}, type: "line", unit: "%", yMin: 0, yMax: 100, refLines: [{ y: 90, label: "90%" }] });
  C.time($("#gr-utemp"), { series: U.temp || {}, type: "line", unit: "°C", refLines: [{ y: 85, label: "85 °C" }] });
  C.time($("#gr-plc"), { series: g.powerline, type: "line", unit: "Mbit/s", refLines: [{ y: 50, label: "50 Mbit/s" }],
    empty: "No powerline adapters read in this period." });
}

async function deviceGraphs(mac, hours = 24) {
  const box = $("#dev-graphs");
  if (!box) return;
  const g = await api(`/api/devices/${encodeURIComponent(mac)}/graphs?hours=${hours}`);
  const now = Date.now() / 1000, C = Charts;
  C.time($("#dg-signal"), { series: { Signal: g.signal }, type: "line", unit: "dBm", yMin: -90, yMax: -30,
    refLines: [{ y: -70, label: "-70 weak" }], empty: "No Wi-Fi signal readings (wired, or not connected)." });
  C.time($("#dg-rate"), { series: { "To device": g.tx_rate, "From device": g.rx_rate }, type: "line", unit: "Mbit/s" });
  C.time($("#dg-traffic"), { series: { Download: g.down_mbps, Upload: g.up_mbps }, type: "area", unit: "Mbit/s",
    colors: { Download: "--c1", Upload: "--c3" }, empty: "No Wi-Fi traffic counters for this device." });
  const inet = g.internet || {};
  C.time($("#dg-inet"), { series: (inet.rates || {}), type: "area", unit: "Mbit/s", colors: { Download: "--c1", Upload: "--c3" },
    empty: "No internet traffic recorded for this device in this period." });
  $("#dg-inet-note").textContent = inet.down_bytes || inet.up_bytes
    ? `${sizeLabel(inet.down_bytes)} down, ${sizeLabel(inet.up_bytes)} up in this period (estimate).` : "";
  const apps = g.apps || [];
  $("#dg-apps-h").hidden = !apps.length;
  $("#dg-apps").innerHTML = apps.length ? `<table class="small"><thead><tr><th>App</th><th>Category</th><th>Download</th><th>Upload</th></tr></thead><tbody>
    ${apps.map((a) => `<tr><td>${esc(a.app)}</td><td class="muted">${esc(a.category || "")}</td><td>${sizeLabel(a.bytes_in)}</td><td>${sizeLabel(a.bytes_out)}</td></tr>`).join("")}</tbody></table>` : "";
  if (g.lan) {
    $("#dg-lan-h").hidden = false;
    C.time($("#dg-lan"), { series: g.lan.series, type: "line", unit: g.lan.unit, gap: 900, empty: "No traffic on its switch port in this period." });
    $("#dg-lan-note").textContent = g.lan.ports.map((p) => `${p.switch} port ${p.port}: ${sizeLabel(p.rx_bytes)} from it, ${sizeLabel(p.tx_bytes)} to it`
      + (p.devices.length > 1 ? ` (shared with ${p.devices.length - 1} other device${p.devices.length > 2 ? "s" : ""})` : "")).join("; ");
  }
  C.strip($("#dg-where"), g.spans.map((s) => ({ ...s, label: `${s.unit} ${s.band ? s.band + " GHz" : ""}`.trim() })),
    now - hours * 3600, now, (l, i) => C.color(i));
  C.time($("#dg-events"), { series: g.events, type: "bars", stacked: true,
    colors: { Kicks: "--bad", "Roam attempts": "--c2", Disconnects: "--warn", "Failed joins": "--c6" }, empty: "No Wi-Fi events." });
  if (g.ping.length) C.time($("#dg-ping"), { series: { Latency: g.ping.map((p) => [p[0], p[1]]) }, type: "line", unit: "ms" });
  else $("#dg-ping").innerHTML = '<div class="chart-empty">Tick <b>Watch this device</b> to ping it every few seconds.</div>';
  C.time($("#dg-daily"), { series: { "Health score": g.daily.map((d) => [Date.parse(d.day) / 1000, d.score]) }, type: "line", yMin: 0, yMax: 100,
    empty: "Daily scores appear after the first day." });
}

function sizeLabel(b) {
  if (b == null) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1000 && i < u.length - 1) { b /= 1000; i++; }
  return `${b >= 100 || i === 0 ? Math.round(b) : b.toFixed(1)} ${u[i]}`;
}

function renderTopUsers(el, t) {
  if (!t || !t.rows || !t.rows.length) {
    el.innerHTML = '<div class="chart-empty">Appears after two router polls with internet traffic.</div>';
    return;
  }
  el.innerHTML = `<table class="small"><thead><tr><th>Device</th><th>Download</th><th>Upload</th><th>Share</th></tr></thead><tbody>
    ${t.rows.map((r) => `<tr><td>${r.mac ? `<a href="#" data-mac="${esc(r.mac)}">${esc(r.name)}</a>` : esc(r.name)}
      <span class="muted mono small">${esc(r.ip || "")}</span></td><td>${sizeLabel(r.down_bytes)}</td><td>${sizeLabel(r.up_bytes)}</td>
      <td><span class="sharebar"><i style="width:${Math.min(100, r.share_pct || 0)}%"></i></span> ${r.share_pct ?? "–"}%</td></tr>`).join("")}
    </tbody></table><p class="muted small">${t.devices} devices, ${sizeLabel(t.total_bytes)} in total. Connections that open and close
    within one poll are missed, so figures are a little low; the order is reliable.</p>`;
}

function renderSwitchTraffic(sw) {
  const C = Charts;
  C.time($("#gr-switch"), { series: sw.series || {}, type: "line", unit: sw.unit || "Mbit/s", gap: 900, legend: true,
    empty: "Add a UniFi controller or a TP-Link Easy Smart switch login in Settings to see traffic between devices at home." });
  const ports = sw.ports || [];
  $("#gr-switch-ports").innerHTML = ports.length ? `<table class="small"><thead><tr><th>Switch</th><th>Port</th><th>Devices on it</th>
    <th>Received</th><th>Sent</th><th>Errors</th></tr></thead><tbody>${ports.map((p) => `<tr><td>${esc(p.switch)}</td><td>${esc(p.port)}${p.name ? ` <span class="muted">${esc(p.name)}</span>` : ""}</td>
      <td>${(p.devices || []).map((d) => `<a href="#" data-mac="${esc(d.mac)}">${esc(d.name)}</a>`).join(", ") || '<span class="muted">–</span>'}</td>
      <td>${p.rx_bytes != null ? sizeLabel(p.rx_bytes) : `${esc(p.rx_packets ?? "–")} pkts`}</td>
      <td>${p.tx_bytes != null ? sizeLabel(p.tx_bytes) : `${esc(p.tx_packets ?? "–")} pkts`}</td>
      <td class="${p.errors ? "bad" : ""}">${esc(p.errors ?? 0)}</td></tr>`).join("")}</tbody></table>
      <p class="muted small">Totals for the period. ${esc(sw.note || "")}</p>` : "";
}

function deviceGraphsHtml(mac) {
  return `<h3>Graphs <select id="dg-hours" class="small-sel"><option value="6">6 h</option><option value="24" selected>24 h</option><option value="168">7 days</option></select></h3>
    <div id="dev-graphs" data-mac="${esc(mac)}">
      <h4>Signal</h4><div id="dg-signal" class="chart-box"></div>
      <h4>Where it was connected <span class="muted small">unit and band</span> <button class="help" data-help="band-timeline">?</button></h4><div id="dg-where" class="chart-box"></div>
      <h4>Link speed</h4><div id="dg-rate" class="chart-box"></div>
      <h4>Data used over Wi-Fi</h4><div id="dg-traffic" class="chart-box"></div>
      <h4>Internet use <span class="muted small">estimate</span> <button class="help" data-help="top-users">?</button></h4><div id="dg-inet" class="chart-box"></div>
      <p id="dg-inet-note" class="muted small"></p>
      <div id="dg-apps-h" hidden><h4>Apps <span class="muted small">from ASUS Traffic Analyzer</span> <button class="help" data-help="traffic-analyzer">?</button></h4>
        <div id="dg-apps"></div></div>
      <div id="dg-lan-h" hidden><h4>Traffic at its switch port <span class="muted small">inside the house</span> <button class="help" data-help="lan-traffic">?</button></h4>
        <div id="dg-lan" class="chart-box"></div><p id="dg-lan-note" class="muted small"></p></div>
      <h4>Wi-Fi events</h4><div id="dg-events" class="chart-box"></div>
      <h4>Ping latency</h4><div id="dg-ping" class="chart-box"></div>
      <h4>Health, last 30 days</h4><div id="dg-daily" class="chart-box"></div>
    </div>`;
}

LOADERS.graphs = () => loadGraphs();
document.addEventListener("DOMContentLoaded", () => {
  $("#gr-hours").addEventListener("change", loadGraphs);
  document.addEventListener("change", (ev) => {
    if (ev.target.id === "dg-hours") deviceGraphs($("#dev-graphs").dataset.mac, ev.target.value);
  });
});
