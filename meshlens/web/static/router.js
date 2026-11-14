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
    + `<button data-unit="pc" class="${routerSel === "pc" ? "on" : ""}">🖥 This PC</button>`;
  if (routerSel === "pc") return renderHostPc();
  return renderUnit(routerSel);
}

async function renderUnit(mac) {
  const [d, host] = await Promise.all([api(`/api/units/${encodeURIComponent(mac)}/detail`), api("/api/hostpc").catch(() => ({ scan: [] }))]);
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

  <div class="grid">
    <div class="card"><h3>Ports</h3>
      ${tbl(["Port", "Link", "Speed", "Max"], d.ports.map((p) => `<tr><td>${esc(p.port)}</td>
        <td class="${p.up ? "good" : "muted"}">${p.up ? "connected" : "not connected"}</td>
        <td class="${p.below_max ? "warn" : ""}">${p.link_mbps ? esc(p.link_mbps >= 1000 ? p.link_mbps / 1000 + " Gbit/s" : p.link_mbps + " Mbit/s") : ""}</td>
        <td class="muted">${esc(p.max_mbps >= 1000 ? p.max_mbps / 1000 + " Gbit/s" : p.max_mbps + " Mbit/s")}</td></tr>`))}
      <p class="muted small">Amber speed = the link runs below what the port can do (cable, or the other end is slower).</p></div>
    <div class="card"><h3>Devices on this unit now (${d.clients.length})</h3>
      ${tbl(["Device", "Band", "Signal", "Rate"], d.clients.map((c) => `<tr data-mac="${esc(c.mac)}" class="clickrow"><td>${esc(c.name)}</td>
        <td>${esc(c.band ? c.band + " GHz" : "")}</td><td class="${rssiClass(c.rssi)}">${esc(c.rssi ?? "")}</td><td>${esc(c.tx_rate ?? "")}</td></tr>`), "clickable")}</div>
  </div>

  ${d.settings ? settingsHtml(d.settings) : `<div class="card"><h3>Settings</h3><p class="muted">${u.role === "router" ? "Read at the next full poll (every 15 minutes)." : "Read at the next full poll."}</p></div>`}

  <div class="card"><details><summary><b>Raw identity values</b> <span class="muted small">(as reported by the unit)</span></summary>
    <div class="table-scroll short"><table>${Object.entries(id).map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td class="mono wrap">${esc(v)}</td></tr>`).join("")}</table></div></details></div>`;
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
  const h = await api("/api/hostpc");
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

LOADERS.router = () => loadRouter();
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-unit]");
  if (b) { routerSel = b.dataset.unit; loadRouter(); }
});
