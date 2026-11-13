"use strict";
// Small SVG chart library for MeshLens: time-series lines/areas/stacked bars, histogram, heatmap, timeline strip.
// Colours come from CSS variables so light and dark themes both work. Hover shows a tooltip.

const Charts = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
  const PALETTE = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8"];
  const color = (i) => css(PALETTE[i % PALETTE.length]);
  const e = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtT = (t, span) => new Date(t * 1000).toLocaleString([], span > 2 * 86400
    ? { day: "2-digit", month: "short" } : span > 86400 ? { weekday: "short", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" });
  const fmtV = (v, unit) => v == null ? "–" : `${Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 10) / 10}${unit ? " " + unit : ""}`;

  function nice(max) {
    if (max <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(max))), f = max / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }

  function empty(el, msg) { el.innerHTML = `<div class="chart-empty">${e(msg || "No data for this period yet.")}</div>`; }

  function tooltip(el) {
    let tip = el.querySelector(".chart-tip");
    if (!tip) { tip = document.createElement("div"); tip.className = "chart-tip"; tip.hidden = true; el.appendChild(tip); }
    return tip;
  }

  function legend(names, colors, hidden, onToggle) {
    const d = document.createElement("div");
    d.className = "legend";
    d.innerHTML = names.map((n, i) => `<span class="lg ${hidden.has(n) ? "off" : ""}" data-n="${e(n)}"><i style="background:${colors[i]}"></i>${e(n)}</span>`).join("");
    d.onclick = (ev) => { const s = ev.target.closest(".lg"); if (s) onToggle(s.dataset.n); };
    return d;
  }

  /** Time series. opts: {series: {name: [[t, v], ...]}, type: "line"|"area"|"bars", stacked, unit, height,
   *  yMin, yMax, invert (for dBm: higher is better but negative), refLines: [{y, label}], colors: {name: cssVar}} */
  function time(el, opts) {
    const hidden = el._hidden || (el._hidden = new Set());
    const all = Object.entries(opts.series || {}).filter(([, pts]) => pts && pts.length);
    if (!all.length) return empty(el, opts.empty);
    const names = all.map(([n]) => n);
    const cols = names.map((n, i) => opts.colors && opts.colors[n] ? css(opts.colors[n]) : color(i));
    const shown = all.filter(([n]) => !hidden.has(n));
    const W = Math.max(280, el.clientWidth || 600), H = opts.height || 220, L = 48, R = 12, T = 10, B = 26;
    const ts = all.flatMap(([, p]) => p.map((x) => x[0]));
    const t0 = Math.min(...ts), t1 = Math.max(...ts), span = Math.max(60, t1 - t0);
    let lo, hi;
    const stacked = opts.stacked && (opts.type === "bars" || opts.type === "area");
    if (stacked) {
      const sums = {};
      shown.forEach(([, p]) => p.forEach(([t, v]) => (sums[t] = (sums[t] || 0) + (v || 0))));
      lo = 0; hi = nice(Math.max(1, ...Object.values(sums)));
    } else {
      const vs = shown.flatMap(([, p]) => p.map((x) => x[1]).filter((v) => v != null));
      lo = opts.yMin ?? Math.min(0, ...vs); hi = opts.yMax ?? (vs.length ? nice(Math.max(...vs)) : 1);
      if (opts.yMin == null && vs.length && Math.min(...vs) < 0) { lo = Math.floor(Math.min(...vs) / 10) * 10; hi = opts.yMax ?? Math.min(0, Math.ceil(Math.max(...vs) / 10) * 10 + 10); }
    }
    const x = (t) => L + ((t - t0) / span) * (W - L - R), y = (v) => T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B);
    let s = `<svg xmlns="${NS}" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" class="chart">`;
    for (let i = 0; i <= 4; i++) {
      const v = lo + ((hi - lo) * i) / 4, yy = y(v);
      s += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" stroke="${css("--line")}"/><text x="${L - 6}" y="${yy + 4}" text-anchor="end" class="ax">${fmtV(v, "")}</text>`;
    }
    for (let i = 0; i <= 5; i++) { const t = t0 + (span * i) / 5; s += `<text x="${x(t)}" y="${H - 6}" text-anchor="middle" class="ax">${e(fmtT(t, span))}</text>`; }
    for (const r of opts.refLines || []) {
      if (r.y < lo || r.y > hi) continue;
      s += `<line x1="${L}" x2="${W - R}" y1="${y(r.y)}" y2="${y(r.y)}" stroke="${css(r.color || "--warn")}" stroke-dasharray="5 4"/><text x="${W - R - 4}" y="${y(r.y) - 4}" text-anchor="end" class="ax">${e(r.label)}</text>`;
    }
    if (opts.type === "bars") {
      const steps = [...new Set(shown.flatMap(([, p]) => p.map((q) => q[0])))].sort((a, b) => a - b);
      const bw = Math.max(1, ((W - L - R) / Math.max(1, steps.length)) * 0.8), base = {};
      shown.forEach(([n, p]) => { const ci = cols[names.indexOf(n)];
        p.forEach(([t, v]) => { if (!v) return; const b0 = base[t] || 0; base[t] = b0 + v;
          s += `<rect x="${x(t) - bw / 2}" y="${y(b0 + v)}" width="${bw}" height="${Math.max(0.5, y(b0) - y(b0 + v))}" fill="${ci}"/>`; }); });
    } else {
      const base = {};
      shown.forEach(([n, p]) => {
        const ci = cols[names.indexOf(n)];
        let d = "", prev = null, area = "";
        const pts = p.map(([t, v]) => { if (v == null) return [t, null]; const b0 = stacked ? (base[t] || 0) : 0; if (stacked) base[t] = b0 + v; return [t, b0 + v, b0]; });
        pts.forEach(([t, v]) => { if (v == null) { prev = null; return; } d += `${prev == null || t - prev > (opts.gap || span / 20) ? "M" : "L"}${x(t).toFixed(1)},${y(v).toFixed(1)}`; prev = t; });
        if (opts.type === "area") {
          const ok = pts.filter((q) => q[1] != null);
          if (ok.length > 1) area = `M${x(ok[0][0])},${y(ok[0][2] || 0)}` + ok.map((q) => `L${x(q[0])},${y(q[1])}`).join("") + [...ok].reverse().map((q) => `L${x(q[0])},${y(q[2] || 0)}`).join("") + "Z";
          s += `<path d="${area}" fill="${ci}" opacity="0.25"/>`;
        }
        s += `<path d="${d}" fill="none" stroke="${ci}" stroke-width="2" stroke-linejoin="round"/>`;
        if (p.length < 40) p.forEach(([t, v]) => { if (v != null && !stacked) s += `<circle cx="${x(t)}" cy="${y(v)}" r="2.5" fill="${ci}"/>`; });
      });
    }
    s += `<line class="cross" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="${css("--muted")}" visibility="hidden"/><rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" class="hit"/></svg>`;
    el.innerHTML = s;
    if (names.length > 1 || opts.legend) el.appendChild(legend(names, cols, hidden, (n) => { hidden.has(n) ? hidden.delete(n) : hidden.add(n); time(el, opts); }));
    const svg = el.querySelector("svg"), tip = tooltip(el), cross = svg.querySelector(".cross");
    svg.querySelector(".hit").addEventListener("mousemove", (ev) => {
      const r = svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W, t = t0 + ((px - L) / (W - L - R)) * span;
      const rows = shown.map(([n, p]) => { let best = p[0]; for (const q of p) if (Math.abs(q[0] - t) < Math.abs(best[0] - t)) best = q; return [n, best]; });
      const tt = rows.length ? rows[0][1][0] : t;
      cross.setAttribute("x1", x(tt)); cross.setAttribute("x2", x(tt)); cross.setAttribute("visibility", "visible");
      tip.hidden = false;
      tip.innerHTML = `<b>${e(new Date(tt * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }))}</b>` +
        rows.map(([n, q]) => `<div><i style="background:${cols[names.indexOf(n)]}"></i>${e(n)}: ${e(fmtV(q[1], opts.unit))}</div>`).join("");
      const left = (x(tt) / W) * r.width;
      tip.style.left = `${Math.min(left + 12, r.width - tip.offsetWidth - 4)}px`; tip.style.top = "8px";
    });
    svg.querySelector(".hit").addEventListener("mouseleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
  }

  /** Histogram: items [{label, value, color}] */
  function bars(el, items, opts = {}) {
    if (!items.some((i) => i.value)) return empty(el, opts.empty);
    const W = Math.max(280, el.clientWidth || 500), H = opts.height || 200, L = 34, B = 30, T = 10;
    const max = nice(Math.max(...items.map((i) => i.value))), bw = (W - L - 8) / items.length;
    let s = `<svg xmlns="${NS}" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" class="chart">`;
    for (let i = 0; i <= 4; i++) { const v = (max * i) / 4, yy = T + (1 - v / max) * (H - T - B);
      s += `<line x1="${L}" x2="${W}" y1="${yy}" y2="${yy}" stroke="${css("--line")}"/><text x="${L - 4}" y="${yy + 4}" text-anchor="end" class="ax">${Math.round(v)}</text>`; }
    items.forEach((it, i) => { const h = (it.value / max) * (H - T - B), xx = L + i * bw;
      s += `<rect x="${xx + 2}" y="${H - B - h}" width="${bw - 4}" height="${h}" fill="${css(it.color || "--c1")}"><title>${e(it.label)}: ${it.value}</title></rect>`;
      if (i % Math.ceil(items.length / 10) === 0) s += `<text x="${xx + bw / 2}" y="${H - 10}" text-anchor="middle" class="ax">${e(it.short || it.label)}</text>`; });
    el.innerHTML = s + "</svg>";
  }

  /** Heatmap: rows x cols cells {v, tip}; scale(v) -> css colour */
  function heatmap(el, { rows, cols, cell, scale, colLabels }) {
    const W = Math.max(320, el.clientWidth || 700), L = 40, T = 18, cw = (W - L - 4) / cols, ch = 22, H = T + rows.length * ch + 4;
    let s = `<svg xmlns="${NS}" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" class="chart">`;
    for (let c = 0; c < cols; c += 3) s += `<text x="${L + c * cw + cw / 2}" y="12" text-anchor="middle" class="ax">${e(colLabels ? colLabels[c] : c)}</text>`;
    rows.forEach((r, ri) => {
      s += `<text x="${L - 6}" y="${T + ri * ch + 15}" text-anchor="end" class="ax">${e(r)}</text>`;
      for (let c = 0; c < cols; c++) { const v = cell(ri, c);
        s += `<rect x="${L + c * cw + 1}" y="${T + ri * ch + 1}" width="${cw - 2}" height="${ch - 2}" rx="3" fill="${v.v == null ? css("--chip") : scale(v.v)}"><title>${e(v.tip)}</title></rect>`; }
    });
    el.innerHTML = s + "</svg>";
  }

  /** Timeline strip of spans [{from, to, label, color}] between t0 and t1. */
  function strip(el, spans, t0, t1, colorOf) {
    if (!spans.length) return empty(el, "No connection data for this period.");
    const W = Math.max(280, el.clientWidth || 600), H = 46, span = Math.max(60, t1 - t0), x = (t) => ((t - t0) / span) * W;
    const labels = [...new Set(spans.map((s) => s.label))];
    let s = `<svg xmlns="${NS}" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" class="chart">`;
    spans.forEach((sp) => { s += `<rect x="${x(sp.from)}" y="4" width="${Math.max(2, x(sp.to + 60) - x(sp.from))}" height="22" fill="${colorOf(sp.label, labels.indexOf(sp.label))}"><title>${e(sp.label)}: ${e(new Date(sp.from * 1000).toLocaleTimeString())} – ${e(new Date(sp.to * 1000).toLocaleTimeString())}</title></rect>`; });
    for (let i = 0; i <= 4; i++) { const t = t0 + (span * i) / 4; s += `<text x="${Math.min(W - 30, Math.max(20, x(t)))}" y="${H - 4}" text-anchor="middle" class="ax">${e(fmtT(t, span))}</text>`; }
    el.innerHTML = s + "</svg>";
    el.appendChild(legend(labels, labels.map((l, i) => colorOf(l, i)), new Set(), () => {}));
  }

  return { time, bars, heatmap, strip, color, css };
})();
