"use strict";
// Network diagram: a drawn tree of internet → router → mesh nodes / powerline, with every device as a dot.
// Colours are resolved from CSS variables at draw time, so the exported SVG/PNG looks the same as the page.

const MapView = (() => {
  const CW = 220, GAP = 40, VGAP = 110, DOT = 16, COLS = 12;
  let scale = 1, last = null;

  const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
  const e = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const sigColor = (r) => r == null ? css("--muted") : r >= -60 ? css("--good") : r >= -70 ? css("--c5") : r >= -78 ? css("--warn") : css("--bad");
  const scoreColor = (s) => s == null ? css("--muted") : s >= 80 ? css("--good") : s >= 60 ? css("--warn") : css("--bad");
  const ICON = { internet: "🌐", router: "◎", node: "◉", powerline: "⚡" };

  function linkStyle(l) {
    if (!l || !l.type) return { color: css("--line"), width: 2, dash: "" };
    if (l.type === "wireless") { const r = l.bands && l.bands.length ? l.bands[0].rssi : l.rssi; return { color: sigColor(r), width: 4, dash: "2 6", cap: "round" }; }
    if (l.type === "powerline") { const s = l.slowest; return { color: s == null ? css("--muted") : s < 20 ? css("--bad") : s < 50 ? css("--warn") : css("--good"), width: 4, dash: "10 6" }; }
    if (l.type === "wan") return { color: css("--accent"), width: 4, dash: "" };
    return { color: css("--muted"), width: 3, dash: "" };
  }

  function linkLabel(l) {
    if (!l || !l.type) return "";
    if (l.type === "wireless" && l.bands) return l.bands.map((b) => `${b.band} GHz ${b.rssi ?? "?"} dBm`).join(" · ");
    if (l.type === "wireless") return `${l.band || "?"} GHz ${l.rssi ?? ""} dBm`;
    if (l.type === "powerline") return `powerline ↓${l.down_mbps ?? "?"} ↑${l.up_mbps ?? "?"} Mbit/s`;
    if (l.type === "wan") return "internet line";
    return l.type === "ethernet" ? "Ethernet" + (l.via ? ` via ${l.via}` : "") : l.type;
  }

  function stats(n, t) {
    const sm = t.summary || {}, devs = n._devs, out = [];
    if (n.kind === "internet") {
      const sp = sm.speed;
      if (sp) out.push(`↓ ${sp.down_mbps} ↑ ${sp.up_mbps} Mbit/s · bufferbloat ${sp.grade || "?"}`);
      for (const l of (sm.latency || []).filter((x) => x.kind === "internet").slice(0, 2)) out.push(`${l.label}: ${l.avg_ms ?? "–"} ms · loss ${l.loss_pct ?? 0}%`);
      if (sm.wan_now && sm.wan_now.Download != null) out.push(`now ↓ ${sm.wan_now.Download} ↑ ${sm.wan_now.Upload} Mbit/s`);
      return out;
    }
    if (n.kind === "router" || n.kind === "node") {
      const by = {};
      devs.forEach((d) => { const k = d.link.type === "wifi" ? `${d.link.band} GHz` : "wired"; by[k] = (by[k] || 0) + 1; });
      const order = ["2.4 GHz", "5 GHz", "6 GHz", "wired"].filter((k) => by[k]);
      out.push(order.length ? order.map((k) => `${k} ${by[k]}`).join(" · ") : "no devices");
      const bad = (sm.radios || {})[n.id] ? sm.radios[n.id].filter((r) => r.tx_fail_pct != null).sort((a, b) => b.tx_fail_pct - a.tx_fail_pct)[0] : null;
      if (bad) out.push(`radio errors: worst ${bad.band} GHz ${bad.tx_fail_pct}%`);
      if (n.kind === "router") { const l = (sm.latency || []).find((x) => x.kind === "router"); if (l) out.push(`ping from MeshLens ${l.avg_ms ?? "–"} ms`); }
      if (n.reachable === 0) out.push("not readable");
      return out;
    }
    if (n.kind === "powerline") out.push(devs.length ? `${devs.length} device${devs.length > 1 ? "s" : ""} behind it` : "");
    return out.filter(Boolean);
  }

  function build(t) {
    const byId = {}, kids = {};
    t.nodes.forEach((n) => { byId[n.id] = { ...n, _devs: [], _kids: [] }; });
    Object.values(byId).forEach((n) => {
      if (!n.parent || !byId[n.parent]) return;
      (n.kind === "device" ? byId[n.parent]._devs : byId[n.parent]._kids).push(n);
    });
    Object.values(byId).forEach((n) => {   // one computer with several connections = one dot
      const g = {};
      n._devs.forEach((d) => { const k = /^([0-9a-f]{2}:){5}[0-9a-f]{2}$/i.test(d.label) ? d.mac : d.label.toLowerCase();
        g[k] ? g[k].count++ : (g[k] = { ...d, count: 1 }); });
      n._devs = Object.values(g).sort((a, b) => (b.link.rssi ?? -200) - (a.link.rssi ?? -200));
    });
    return byId;
  }

  const CWX = 340, ROW = 19;          // an expanded card is wider and lists every device on a row
  const expanded = new Set();

  function measure(n, t) {
    n._stats = stats(n, t);
    n._open = expanded.has(n.id) && n._devs.length > 0;
    n._cw = n._open ? CWX : CW;
    n._tog = n._devs.length ? 20 : 0;
    n._rows = n._open ? n._devs.length : Math.ceil(n._devs.length / COLS);
    n._h = 52 + n._stats.length * 16 + n._tog + (n._rows ? (n._open ? n._rows * ROW + 8 : n._rows * DOT + 10) : 0);
    n._kids.forEach((k) => measure(k, t));
    n._w = Math.max(n._cw, n._kids.reduce((a, k) => a + k._w, 0) + GAP * Math.max(0, n._kids.length - 1));
  }

  function place(n, x0, y) {
    n._x = x0 + n._w / 2 - n._cw / 2; n._y = y;
    let x = x0 + (n._w - (n._kids.reduce((a, k) => a + k._w, 0) + GAP * Math.max(0, n._kids.length - 1))) / 2;
    n._kids.forEach((k) => { place(k, x, y + n._h + VGAP); x += k._w + GAP; });
  }

  const trunc = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

  function svg(root, t) {
    const all = [];
    (function walk(n) { all.push(n); n._kids.forEach(walk); })(root);
    const W = root._w + 40, H = Math.max(...all.map((n) => n._y + n._h)) + 40;
    const font = "font-family:system-ui,'Segoe UI',sans-serif";
    let s = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="-20 -20 ${W} ${H}" width="${W * scale}" height="${H * scale}" style="${font};background:${css("--card")}">`;
    for (const n of all) for (const k of n._kids) {
      const st = linkStyle(k.link), x1 = n._x + n._cw / 2, y1 = n._y + n._h, x2 = k._x + k._cw / 2, y2 = k._y, my = (y1 + y2) / 2;
      s += `<path d="M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}" fill="none" stroke="${st.color}" stroke-width="${st.width}" stroke-dasharray="${st.dash}" stroke-linecap="${st.cap || "butt"}"/>`;
      const lab = linkLabel(k.link);
      // Label at 75% of the way to the child, so siblings' labels sit apart (under their own child).
      const bt = 0.75, u = 1 - bt;
      const bx = u ** 3 * x1 + 3 * u * u * bt * x1 + 3 * u * bt * bt * x2 + bt ** 3 * x2;
      const by = u ** 3 * y1 + 3 * u * u * bt * my + 3 * u * bt * bt * my + bt ** 3 * y2;
      if (lab) { const lw = lab.length * 6.4 + 12, lx = bx, ly = by;
        s += `<rect x="${lx - lw / 2}" y="${ly - 11}" width="${lw}" height="20" rx="10" fill="${css("--card")}" stroke="${st.color}"/><text x="${lx}" y="${ly + 4}" text-anchor="middle" font-size="11" fill="${css("--text")}">${e(lab)}</text>`; }
    }
    for (const n of all) {
      const border = n.kind === "router" ? css("--accent") : n.reachable === 0 ? css("--bad") : css("--line");
      s += `<g class="mnode${n._devs.length ? " expandable" : ""}" ${n._devs.length ? `data-node="${e(n.id)}"` : ""}><rect x="${n._x}" y="${n._y}" width="${n._cw}" height="${n._h}" rx="12" fill="${css("--chip")}" stroke="${border}" stroke-width="${n.kind === "router" ? 2.5 : 1.5}" ${n.kind === "powerline" ? 'stroke-dasharray="6 4"' : ""}/>`;
      s += `<text x="${n._x + 12}" y="${n._y + 22}" font-size="15" font-weight="700" fill="${css("--text")}">${ICON[n.kind] || ""} ${e(n.label)}</text>`;
      const h = n.health && n.health.score != null ? n.health.score : null;
      if (h != null) s += `<rect x="${n._x + n._cw - 44}" y="${n._y + 8}" width="34" height="20" rx="10" fill="${scoreColor(h)}"/><text x="${n._x + n._cw - 27}" y="${n._y + 22}" font-size="12" font-weight="700" text-anchor="middle" fill="#fff">${h}</text>`;
      s += `<text x="${n._x + 12}" y="${n._y + 40}" font-size="11" fill="${css("--muted")}">${e([n.model, n.ip].filter(Boolean).join(" · "))}</text>`;
      n._stats.forEach((line, i) => { s += `<text x="${n._x + 12}" y="${n._y + 58 + i * 16}" font-size="12" fill="${css("--text")}">${e(line)}</text>`; });
      const ty = n._y + 52 + n._stats.length * 16;
      if (n._tog) s += `<text x="${n._x + 12}" y="${ty + 14}" font-size="12" font-weight="600" fill="${css("--accent")}" class="mtoggle">${n._open ? "▾ hide device list" : `▸ ${n._devs.length} device${n._devs.length > 1 ? "s" : ""}: show list`}</text>`;
      const dy = ty + n._tog + 4;
      n._devs.forEach((d, i) => {
        const fill = d.link.type === "wifi" ? sigColor(d.link.rssi) : css("--muted");
        const ring = d.health != null && d.health < 60 ? css("--bad") : d.watched ? css("--accent") : "none";
        const tip = `<title>${e(d.label)}${d.count > 1 ? ` (${d.count} connections)` : ""}\n${e([d.ip, d.dtype, d.vendor].filter(Boolean).join(" · "))}\n${e(d.link.type === "wifi" ? `${d.link.band} GHz ${d.link.rssi ?? "?"} dBm` : d.link.type)}${d.health != null ? `\nhealth ${d.health}` : ""}</title>`;
        if (n._open) {
          const y = dy + i * ROW, l = d.link;
          const right = l.type === "wifi" ? `${l.band} GHz · ${l.rssi ?? "?"} dBm${l.tx_rate ? ` · ${Math.round(l.tx_rate)} Mbit/s` : ""}` : (l.via ? "wired via " + l.via : l.type);
          s += `<g class="mdot mrow" data-mac="${e(d.mac)}"><rect x="${n._x + 6}" y="${y}" width="${n._cw - 12}" height="${ROW - 1}" rx="4" fill="transparent"/>` +
            `<circle cx="${n._x + 18}" cy="${y + ROW / 2}" r="5" fill="${fill}" stroke="${ring}" stroke-width="2"/>` +
            `<text x="${n._x + 30}" y="${y + 13}" font-size="12" fill="${css("--text")}">${d.watched ? "★ " : ""}${e(trunc(d.label, 26))}${d.count > 1 ? ` ×${d.count}` : ""}</text>` +
            `<text x="${n._x + n._cw - 12}" y="${y + 13}" font-size="11" text-anchor="end" fill="${l.type === "wifi" ? sigColor(l.rssi) : css("--muted")}">${e(right)}</text>${tip}</g>`;
        } else {
          const cx = n._x + 14 + (i % COLS) * DOT + 2, cy = dy + Math.floor(i / COLS) * DOT + 6;
          s += `<g class="mdot" data-mac="${e(d.mac)}"><circle cx="${cx}" cy="${cy}" r="6" fill="${fill}" stroke="${ring}" stroke-width="2.5"/>${tip}</g>`;
        }
      });
      s += "</g>";
    }
    return s + "</svg>";
  }

  async function load() {
    const t = await api("/api/topology");
    const first = !last;
    relayout(t);
    if (!last) { $("#topo-map").innerHTML = '<p class="muted">No data yet: the map appears after the first router poll.</p>'; return; }
    if (first) zoom(0); else draw();     // fit the window the first time
  }

  function relayout(t) {
    const byId = build(t), root = byId.internet;
    if (!root) { last = null; return; }
    measure(root, t); place(root, 0, 0);
    last = { root, t, byId };
  }

  function toggle(id) { expanded.has(id) ? expanded.delete(id) : expanded.add(id); relayout(last.t); draw(); }
  function expandAll(on) {
    expanded.clear();
    if (on && last) Object.values(last.byId).forEach((n) => { if (n.kind !== "device" && n._devs.length) expanded.add(n.id); });
    relayout(last.t); draw();
  }

  function draw() {
    if (!last) return;
    const box = $("#topo-map");
    box.innerHTML = svg(last.root, last.t);
    box.onclick = (ev) => {
      const d = ev.target.closest(".mdot");
      if (d) return openDevice(d.dataset.mac);
      const n = ev.target.closest("[data-node]");
      if (n) toggle(n.dataset.node);
    };
  }

  function zoom(f) { scale = f === 0 ? Math.min(1, ($("#topo-map").clientWidth - 10) / (last ? last.root._w + 40 : 1000)) : Math.max(0.3, Math.min(2.5, scale * f)); draw(); }

  function download(kind) {
    const markup = $("#topo-map svg").outerHTML, name = `meshlens-map-${new Date().toISOString().slice(0, 10)}`;
    const save = (url, ext) => { const a = document.createElement("a"); a.href = url; a.download = `${name}.${ext}`; a.click(); };
    const blob = new Blob([markup], { type: "image/svg+xml" }), url = URL.createObjectURL(blob);
    if (kind === "svg") return save(url, "svg");
    const img = new Image();
    img.onload = () => { const c = document.createElement("canvas"); c.width = img.width * 2; c.height = img.height * 2;
      const ctx = c.getContext("2d"); ctx.scale(2, 2); ctx.drawImage(img, 0, 0); c.toBlob((b) => save(URL.createObjectURL(b), "png")); };
    img.src = url;
  }

  return { load, zoom, download, expandAll };
})();

document.addEventListener("DOMContentLoaded", () => {
  let view = "diagram";
  try { view = localStorage.getItem("meshlens.mapView") || "diagram"; } catch (err) { /* private mode */ }
  const setView = (v) => {
    view = v;
    try { localStorage.setItem("meshlens.mapView", v); } catch (err) { /* private mode */ }
    $("#topo-map-wrap").hidden = v !== "diagram"; $("#topo-list-wrap").hidden = v === "diagram";
    document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
    if (v === "diagram") MapView.load();
  };
  document.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => setView(b.dataset.view)));
  $("#map-in").onclick = () => MapView.zoom(1.25); $("#map-out").onclick = () => MapView.zoom(0.8); $("#map-fit").onclick = () => MapView.zoom(0);
  $("#map-expand").onclick = () => MapView.expandAll(true); $("#map-collapse").onclick = () => MapView.expandAll(false);
  $("#map-png").onclick = () => MapView.download("png"); $("#map-svg").onclick = () => MapView.download("svg");
  const orig = LOADERS.topology;
  LOADERS.topology = async () => { await orig(); setView(view); };
});
