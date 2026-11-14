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
  $("#gr-wan-note").textContent = g.traffic.wan_iface ? `Router interface ${g.traffic.wan_iface}, averaged per poll.` : "";
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
    colors: { Download: "--c1", Upload: "--c3" }, empty: "No traffic counters for this device." });
  C.strip($("#dg-where"), g.spans.map((s) => ({ ...s, label: `${s.unit} ${s.band ? s.band + " GHz" : ""}`.trim() })),
    now - hours * 3600, now, (l, i) => C.color(i));
  C.time($("#dg-events"), { series: g.events, type: "bars", stacked: true,
    colors: { Kicks: "--bad", "Roam attempts": "--c2", Disconnects: "--warn", "Failed joins": "--c6" }, empty: "No Wi-Fi events." });
  if (g.ping.length) C.time($("#dg-ping"), { series: { Latency: g.ping.map((p) => [p[0], p[1]]) }, type: "line", unit: "ms" });
  else $("#dg-ping").innerHTML = '<div class="chart-empty">Tick <b>Watch this device</b> to ping it every few seconds.</div>';
  C.time($("#dg-daily"), { series: { "Health score": g.daily.map((d) => [Date.parse(d.day) / 1000, d.score]) }, type: "line", yMin: 0, yMax: 100,
    empty: "Daily scores appear after the first day." });
}

function deviceGraphsHtml(mac) {
  return `<h3>Graphs <select id="dg-hours" class="small-sel"><option value="6">6 h</option><option value="24" selected>24 h</option><option value="168">7 days</option></select></h3>
    <div id="dev-graphs" data-mac="${esc(mac)}">
      <h4>Signal</h4><div id="dg-signal" class="chart-box"></div>
      <h4>Where it was connected</h4><div id="dg-where" class="chart-box"></div>
      <h4>Link speed</h4><div id="dg-rate" class="chart-box"></div>
      <h4>Data used</h4><div id="dg-traffic" class="chart-box"></div>
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
