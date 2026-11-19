"use strict";
// Guidance: step-by-step links and "check it worked" per issue, where to go to fix it, grouping repeated issues,
// the setup checklist, hidden issues and changes you marked (with before/after).

/** Help anchors with exact steps, per rule. */
const HOWTO = {
  radar_dfs: ["howto-fixed-channel"], dfs_channel: ["howto-fixed-channel"], channel_plan: ["howto-fixed-channel", "howto-24-channel"],
  channel_hopping: ["howto-fixed-channel"], channel_busy: ["howto-24-channel"], crowded_channel: ["howto-24-channel"],
  stuck_on_24: ["howto-smart-connect", "howto-bind"], outdoor_wander: ["howto-bind"], sticky_client: ["howto-bind"],
  pmf_mismatch: ["howto-pmf"], pmf_mismatch_air: ["howto-pmf"], join_failures: ["howto-pmf", "howto-bind"],
  firmware_mismatch: ["howto-firmware"], firmware_update: ["howto-firmware"], unit_unreachable: ["howto-firmware"],
  network_loop: ["howto-one-path", "howto-ethernet-backhaul"], kick_loop: ["howto-roaming"], aggressive_roaming: ["howto-roaming"],
  igmp_snooping_off: ["howto-igmp"], bufferbloat: ["howto-qos"], qos_off: ["howto-qos"], dual_homed: ["howto-pi-wifi-off"],
  watch_suggest: ["howto-watch"], probe_new: ["probes"], probe_unpaired: ["probes"],
};
const HOWTO_TITLE = {
  "howto-fixed-channel": "set a fixed 5 GHz channel", "howto-24-channel": "set the 2.4 GHz channel", "howto-smart-connect": "Smart Connect rule",
  "howto-bind": "bind a device to a unit", "howto-pmf": "set PMF", "howto-firmware": "update firmware", "howto-one-path": "remove a loop",
  "howto-ethernet-backhaul": "Ethernet backhaul", "howto-roaming": "roaming assistant", "howto-igmp": "IGMP snooping", "howto-qos": "turn on QoS",
  "howto-pi-wifi-off": "turn off a Pi's Wi-Fi", "howto-watch": "watch devices", probes: "pair a probe",
};

/** How to tell the fix worked, per rule. */
const VERIFY = {
  radar_dfs: "No new radar events and 5 GHz stays on its channel (Router → main router → Channel plan). Radar is rare: give it a week.",
  channel_plan: "The Channel plan shows your block as 'now' and 'best'; 'channel busy' readings on the Router pages drop.",
  stuck_on_24: "On the device's page, 'Where it was connected' shows 5 GHz again and it stays there.",
  outdoor_wander: "The device stops appearing on the outdoor unit; its disconnect count falls over a day.",
  pmf_mismatch: "After the next full poll (15 min) this issue disappears; failed joins (status 30/53) fall over a day.",
  pmf_mismatch_air: "The probe's 'Your mesh as heard here' table shows the same PMF for every unit.",
  join_failures: "No new failed joins for this device over a day.",
  firmware_mismatch: "Router pages show the new firmware on every unit; this issue clears at the next full poll.",
  network_loop: "Ping loss to the router drops to normal (under 1%) and no new sightings in the next hour.",
  dual_homed: "Within 30 minutes only one address (the cable) is seen for the device and this issue clears.",
  kick_loop: "Fewer than 3 kicks an hour for the device over the next day.",
  flapping: "Disconnects for the device fall below 6 an hour.",
  bufferbloat: "The next speed tests (Tests tab) grade A or B.",
  speed_low: "The next few speed tests reach at least 60% of your plan.",
  mass_drop: "No more mass drops from the unit over a day.",
  watch_suggest: "The Setup checklist shows 'Key devices watched' ticked.",
};

/** Where to go to act on an issue. */
function issueGoto(i) {
  if (/^probe_/.test(i.rule)) return { label: "Router → Probes", attr: 'data-router-unit="probes"' };
  if (["radar_dfs", "channel_plan", "dfs_channel", "channel_hopping"].includes(i.rule)) return { label: "Channel plan", attr: 'data-router-unit="main"' };
  if (["channel_busy", "crowded_channel"].includes(i.rule)) return { label: "This PC (air survey)", attr: 'data-router-unit="pc"' };
  if (i.rule === "watch_suggest") return { label: "Setup checklist", attr: 'data-goto="settings" data-set="checklist"' };
  if (i.scope === "unit" && i.subject && unitsByMac[String(i.subject).split("-")[0]]) return { label: unitName(String(i.subject).split("-")[0]), attr: `data-router-unit="${esc(String(i.subject).split("-")[0])}"` };
  return null;
}

function issueGuidance(i) {
  const how = (HOWTO[i.rule] || []).map((a) => `<a href="#" data-help="${esc(a)}">${esc(HOWTO_TITLE[a] || a)}</a>`).join(" · ");
  const go = issueGoto(i);
  return (how ? `<p class="small"><span class="lbl">Steps</span> ${how}</p>` : "")
    + (VERIFY[i.rule] ? `<p class="small"><span class="lbl">Check</span> ${esc(VERIFY[i.rule])}</p>` : "")
    + (go ? `<p class="small"><a href="#" ${go.attr}>Go to ${esc(go.label)} →</a></p>` : "");
}

/** One entry per rule + subject: the latest, with how many times it was raised and when. */
function groupIssues(list) {
  const by = new Map();
  for (const i of list) {
    const k = `${i.rule}|${i.subject}`;
    if (!by.has(k)) by.set(k, { ...i, times: [] });
    by.get(k).times.push([i.from, i.to]);
  }
  return [...by.values()];
}

function loadingHtml(text = "Working it out…") { return `<p class="muted loading"><span class="spinner"></span> ${esc(text)}</p>`; }

// ---------------------------------------------------------------- Settings: setup checklist
async function renderChecklist(el) {
  el.innerHTML = loadingHtml("Checking…");
  const items = await api("/api/checklist");
  const done = items.filter((x) => x.done === true).length, need = items.filter((x) => x.done !== null).length;
  el.innerHTML = `<p>${done} of ${need} done.</p><table>${items.map((x) => `<tr>
      <td>${x.done === true ? '<span class="good">✔</span>' : x.done === false ? '<span class="warn">●</span>' : '<span class="muted">–</span>'}</td>
      <td><b>${esc(x.label)}</b><div class="muted small wrap">${esc(x.detail || "")}</div></td>
      <td>${x.goto === "watch-suggested" ? (x.suggested.length ? `<button data-watch-suggested="${esc(x.suggested.join(","))}">Watch suggested (${x.suggested.length})</button>` : "")
        : x.goto === "probes" ? '<a href="#" data-router-unit="probes">Probes →</a>'
        : x.goto === "router" ? '<a href="#" data-goto="router">Router →</a>'
        : x.goto.startsWith("settings:") ? `<a href="#" data-set="${esc(x.goto.split(":")[1])}">Open →</a>` : ""}</td></tr>`).join("")}</table>
    <p class="muted small">Optional items show a dash. Everything here can be changed later.</p>`;
}

// ---------------------------------------------------------------- Settings: hidden issues
async function renderHiddenIssues(el) {
  const list = await api("/api/issues/hidden");
  el.innerHTML = list.length ? `<table><thead><tr><th>Issue</th><th>For</th><th>Hidden</th><th></th></tr></thead><tbody>${list.map((h) => `<tr>
      <td>${esc(h.title || h.rule)} <span class="muted small">${esc(h.rule)}</span></td>
      <td>${/^[0-9a-f]{2}(:[0-9a-f]{2}){5}$/.test(h.subject) && !unitsByMac[h.subject] ? `<a href="#" data-mac="${esc(h.subject)}">${esc(devName(h.subject))}</a>`
        : esc(unitsByMac[h.subject] ? unitName(h.subject) : h.subject)}</td>
      <td class="muted small">${esc(ago(h.hidden_at))}</td>
      <td><button class="linkish" data-hide-rule="${esc(h.rule)}" data-hide-subject="${esc(h.subject)}" data-hidden="1">Show this again</button></td></tr>`).join("")}</tbody></table>`
    : '<p class="muted">Nothing hidden. Use "Hide this issue" on any issue to keep a known, harmless one out of the counts.</p>';
}

// ---------------------------------------------------------------- changes you made (before / after)
const IMPACT_ROWS = [
  ["router_loss_pct", "Ping loss to the router", "%", true], ["router_spikes_per_h", "Router ping spikes over 200 ms", "/h", true],
  ["internet_loss_pct", "Ping loss to the internet", "%", true], ["internet_spikes_per_h", "Internet ping spikes over 200 ms", "/h", true],
  ["disconnects_per_h", "Wi-Fi disconnects and kicks", "/h", true], ["devices_on_24", "Devices on 2.4 GHz (average)", "", true],
  ["speed_down", "Speed test download (this PC)", " Mbit/s", false], ["speed_up", "Speed test upload (this PC)", " Mbit/s", false],
  ["grade", "Bufferbloat grade", "", null],
];

function impactHtml(c) {
  const im = c.impact;
  if (!im) return "";
  const cell = (v, u) => v == null ? '<span class="muted">–</span>' : esc(v) + u;
  const better = (k, lowerBetter, b, a) => {
    if (b == null || a == null || lowerBetter === null) return "";
    if (a === b) return "";
    const good = lowerBetter ? a < b : a > b;
    return `<span class="${good ? "good" : "bad"}">${good ? "better" : "worse"}</span>`;
  };
  return `<table class="small"><thead><tr><th></th><th>${esc(im.hours)} h before</th><th>${esc(im.hours)} h after</th><th></th></tr></thead><tbody>
    ${IMPACT_ROWS.map(([k, label, unit, lower]) => `<tr><td>${esc(label)}</td><td>${cell(im.before[k], unit)}</td><td>${cell(im.after[k], unit)}</td>
      <td>${better(k, lower, im.before[k], im.after[k])}</td></tr>`).join("")}</tbody></table>
    ${im.hours < 24 ? `<p class="muted small">Compares equal windows; it uses up to 24 h each side as time passes. Speed tests run every few hours, so they may be missing at first.</p>` : ""}`;
}

async function renderChanges(el) {
  const list = await api("/api/changes");
  el.innerHTML = `<p><button data-mark-change>Mark a change now</button>
      <span class="muted small">Say what you changed (a channel, a cable, a setting). MeshLens marks it on every graph and compares before with after.</span></p>
    ${list.length ? list.map((c) => `<div class="card inner"><h4>${esc(new Date(c.ts * 1000).toLocaleString())}: ${esc(c.note)}
        <button class="linkish small" data-change-delete="${esc(c.id)}">delete</button></h4>${impactHtml(c)}</div>`).join("")
      : '<p class="muted">No changes marked yet.</p>'}`;
}

async function markChange() {
  const note = prompt("What did you change? (e.g. 'Set 5 GHz to channel 36', 'Unplugged powerline from office switch')");
  if (!note || !note.trim()) return;
  try { await post("/api/changes", { note: note.trim() }); } catch (err) { alert(err.message); return; }
  await refreshMarks();
  if (currentSection === "changes" && !$("#settings").hidden) loadSettings("changes");
}

/** Change markers shown on every time chart. */
let MESHLENS_MARKS = [];
async function refreshMarks() {
  try { MESHLENS_MARKS = (await api("/api/changes")).map((c) => ({ t: c.ts, label: c.note })); } catch { MESHLENS_MARKS = []; }
}

// ---------------------------------------------------------------- wiring
document.addEventListener("click", async (e) => {
  const m = e.target.closest("[data-mark-change]");
  if (m) { e.preventDefault(); return markChange(); }
  const del = e.target.closest("[data-change-delete]");
  if (del) {
    e.preventDefault();
    if (!confirm("Delete this marked change?")) return;
    await post(`/api/changes/${del.dataset.changeDelete}/delete`, {});
    await refreshMarks();
    return loadSettings("changes");
  }
  const w = e.target.closest("[data-watch-suggested]");
  if (w) {
    e.preventDefault();
    w.disabled = true;
    await post("/api/devices/watch", { macs: w.dataset.watchSuggested.split(","), watched: true });
    return loadSettings("checklist");
  }
  const r = e.target.closest("[data-router-unit]");
  if (r) {
    e.preventDefault();
    $("#drawer").hidden = true;
    const u = r.dataset.routerUnit;
    routerSel = u === "main" ? (Object.values(unitsByMac).find((x) => x.role === "router") || {}).mac || null : u;
    showTab("router");
  }
});
document.addEventListener("DOMContentLoaded", () => { refreshMarks(); });
