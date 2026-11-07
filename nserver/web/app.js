"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (n) => Number(n || 0).toLocaleString("en-US");

let overview = null;
let tab = "overview";
let statsDays = 0;   // 1 hour
let chartMode = "requests";
let lastStats = null;
let logAfter = 0;
let logRows = [];
let logKind = "all";

const COPY_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="5" y="5" width="8.5" height="8.5" rx="1.5"/><path d="M10.5 5V3.5A1.5 1.5 0 0 0 9 2H3.5A1.5 1.5 0 0 0 2 3.5V9a1.5 1.5 0 0 0 1.5 1.5H5"/></svg>';

// ---- formatting ----

function size(n) {
  n = Number(n || 0);
  for (const u of ["B", "KB", "MB", "GB"]) {
    if (n < 1024 || u === "GB") return u === "B" ? `${Math.round(n)} B` : `${n.toFixed(1)} ${u}`;
    n /= 1024;
  }
}
function span(s) {
  s = Math.max(0, Math.floor(s));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${s % 60}s`;
}
const ago = (t) => (t ? `${span(Date.now() / 1000 - t)} ago` : "never");
const date = (t) => new Date(t * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const pad = (n) => String(n).padStart(2, "0");
// "03/11 00:52": the date and time, next to the relative one
function stamp(t) {
  const d = new Date(t * 1000);
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const when = (t) => (t ? `${stamp(t)} · ${ago(t)}` : "never");
// for table cells: the time on top, how long ago below
const whenCell = (t) => (t ? `${stamp(t)}<div class="muted small">${ago(t)}</div>` : "never");
// well separated hues, no red (it stays for errors); the server's nodes take them in address order
const HUES = [210, 40, 140, 275, 180, 90, 245, 60];
// A node's hue comes from its address, so it stays the same everywhere and across restarts
function hue(address) {
  const all = (overview?.nodes || []).map((n) => n.address).filter(Boolean).sort();
  const i = all.indexOf(address);
  if (i >= 0) return HUES[i % HUES.length];
  const h = parseInt(String(address || "0").slice(0, 6), 16) % 360 || 0;
  // spread over 30..300 only: red, pink and magenta stay for errors
  return Math.round(30 + (h * 270) / 360);
}
const nodeHue = (id) => {
  const n = overview?.nodes.find((x) => x.id === id);
  return n ? hue(n.address) : null;
};
const hstyle = (h) => (h === null || h === undefined ? "" : ` style="--h:${h}"`);

$("#theme").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("ns-theme", next); } catch (e) { /* private mode */ }
  themeLabel();
  if (tab === "stats" && lastStats) chart(lastStats);
});
function themeLabel() {
  const t = document.documentElement.dataset.theme === "light" ? "Switch to dark mode" : "Switch to light mode";
  $("#theme").title = t;
  $("#theme").setAttribute("aria-label", t);
}
themeLabel();

const addrRow = (a, label = "Server address") => `<div class="addr-row"><span class="addr-label">${label}</span><button class="addr" data-copy="${esc(a)}" title="Copy address">${esc(a)}${COPY_ICON}</button></div>`;
document.addEventListener("click", (e) => {
  const copy = e.target.closest("[data-copy]");
  if (copy) navigator.clipboard?.writeText(copy.dataset.copy).then(() => toast("Address copied"), () => toast("Copy not allowed here: select it by hand"));
});

const cells = (list) => list.map(([v, l, cls]) => `<div class="cell"><div class="v ${cls || ""}">${v}</div><div class="l">${l}</div></div>`).join("");

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 2600);
}

// ---- API ----

async function api(path, data) {
  const opt = { headers: { "X-Requested-With": "nomad-server" } };
  if (data !== undefined) {
    opt.method = "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(data);
  }
  const r = await fetch(path, opt);
  if (r.status === 401) {
    showLogin();
    throw new Error("login");
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function showLogin() {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#login-pw").focus();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/login", { password: $("#login-pw").value });
    $("#login-pw").value = "";
    $("#login-err").textContent = "";
    boot();
  } catch (err) {
    $("#login-err").textContent = err.message === "login" ? "" : err.message === "wrong password" ? "Wrong password." : err.message;
  }
});

// reload from the button: no splash again
$("#reload").addEventListener("click", () => {
  try { sessionStorage.setItem("ns-reload", "1"); } catch (e) { /* private mode */ }
  location.reload();
});

$("#logout").addEventListener("click", async () => {
  await api("/api/logout", {});
  showLogin();
});

// ---- dialog ----

function ask(title, bodyHtml, okText = "OK", danger = false) {
  const dlg = $("#dlg");
  $("#dlg-title").textContent = title;
  $("#dlg-body").innerHTML = bodyHtml;
  const ok = $("#dlg-ok");
  ok.textContent = okText;
  ok.className = danger ? "btn danger" : "btn primary";
  dlg.returnValue = "";
  dlg.showModal();
  const first = $("input", dlg);
  if (first) first.focus();
  // Enter in a text field confirms (the form's first button is Cancel)
  const onKey = (e) => {
    if (e.key === "Enter" && e.target.tagName === "INPUT") {
      e.preventDefault();
      dlg.close("ok");
    }
  };
  dlg.addEventListener("keydown", onKey);
  return new Promise((res) => {
    dlg.addEventListener("close", () => {
      dlg.removeEventListener("keydown", onKey);
      res(dlg.returnValue === "ok");
    }, { once: true });
  });
}

// ---- tabs ----

$$("#tabs .tab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

function setTab(name) {
  if (name === "honeypot" && !advanced()) name = "overview";
  tab = name;
  $$("#tabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".page").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${name}`));
  try { localStorage.setItem("ns-tab", name); } catch (e) { /* private mode */ }
  if (name === "overview" && overview) renderOverview();
  if (name === "stats") loadStats();
  if (name === "settings") loadSettings();
  if (name === "logs") renderLog();
  if (name === "honeypot") loadHoneypot();
}

// ---- overview ----

async function loadOverview() {
  try {
    overview = await api("/api/overview");
  } catch (e) {
    $("#conn").classList.add("off");
    $("#conn-text").textContent = "server not answering";
    return;
  }
  const r = overview.rns;
  $("#conn").classList.toggle("off", !!r.error);
  const p = overview.proc || {};
  $("#conn-text").textContent = `${r.shared ? "rnsd" : "own instance"} · up ${span(overview.now - overview.started)}`
    + (p.rss ? ` · ${size(p.rss)}` : "") + ` · CPU ${(p.cpu || 0).toFixed(1)}%`;
  $("#conn").title = "Reticulum connection, server uptime, memory and CPU of the server process (100% = one core)";
  $("#version").textContent = "v" + overview.version;
  $("#about-ver").textContent = "v" + overview.version;
  fillNodeSelects();
  if (tab === "overview") renderOverview();
}

function renderOverview() {
  const o = overview;
  // honeypot nodes live in their own tab
  const nodes = o.nodes.filter((n) => !isHP(n));
  const running = nodes.filter((n) => n.state === "running").length;
  const online = nodes.reduce((a, n) => a + n.online.length, 0);
  const sum = (k) => nodes.reduce((a, n) => a + (n.stats[k] || 0), 0);
  const lastHour = nodes.reduce((a, n) => a + n.spark.reduce((b, m) => b + m[0], 0), 0);
  $("#strip").innerHTML = cells([
    [`${running}/${nodes.length}`, "nodes running"],
    [num(online), "connected now"],
    [num(lastHour), "requests, last hour", "amber"],
    [num(sum("today_views")), "page views today"],
    [size(sum("today_bytes")), "sent today"],
    [num(sum("views")), "page views, all time"],
  ]);

  const r = o.rns;
  const up = r.interfaces.filter((i) => i.up).length;
  $("#rns-mode").textContent = (r.shared ? "Using the shared instance (rnsd)" : "Own Reticulum instance")
    + (r.interfaces.length ? ` · ${up} of ${r.interfaces.length} interfaces up` : "");
  let rns = r.error ? `<div class="node-err">${esc(r.error)}</div>` : "";
  rns += r.interfaces.length
    ? r.interfaces.map((i) => {
      // "TCPInterface[hub/host:4242]" -> type TCP, name hub/host:4242
      const m = /^(\w+?)(?:Interface)?\[(.*)\]$/.exec(i.name);
      const ty = m ? m[1] : "", nm = m ? m[2] : i.name;
      return `<div class="iface"><span class="dot ${i.up ? "" : "off"}"></span><span class="nm" title="${esc(i.name)}"><span class="ty">${esc(ty)}</span>${esc(nm)}</span><span class="io">↓${size(i.rx)} ↑${size(i.tx)}</span></div>`;
    }).join("")
    : (r.error ? "" : `<div class="empty">No interfaces: this instance reaches nobody else.</div>`);
  $("#rns").innerHTML = rns;

  $("#nodes").innerHTML = nodes.length ? nodes.map(nodeCard).join("") : `<div class="panel empty">No nodes yet. Create one with Create new node: you get a folder with its own identity and a sample index.mu.</div>`;

  const rows = [];
  nodes.forEach((n) => n.online.forEach((v) => rows.push({ n, v })));
  $("#online").innerHTML = rows.length
    ? `<table class="online"><tr><th>Node</th><th>Link</th><th>Visitor</th><th>Arrived</th><th class="n">Connected</th><th class="n">Requests</th><th class="n">In</th><th class="n">Out</th></tr>${rows.map(({ n, v }) => `<tr>
      <td><span class="nm-cell"${hstyle(hue(n.address))}><span class="ndot"></span>${esc(n.name)}</span></td>
      <td class="who">${esc(v.link)}</td>
      <td>${v.who ? `<span class="who">${esc(v.who)}</span>${v.name ? ` <span class="muted">${esc(v.name)}</span>` : ""} ${blockBtn(v.who, v.name, n.id)}`
        : `<span class="muted">Not identified</span> ${isMaze(v.blocked) ? `<span class="flag">in the maze</span> ${releaseBtn(v.link, n.id)}` : v.blocked ? `<span class="flag bad">link blocked · ${esc(v.blocked)}</span> <button class="btn-unblock" data-unblocklink="${esc(v.link)}" data-node="${esc(n.id)}">Unblock</button>` : `<button class="btn-block" data-blocklink="${esc(v.link)}" data-node="${esc(n.id)}" title="Block this connection until it disconnects">Block link</button>`}`}${v.crawler ? ` <span class="flag">crawler?</span>` : ""}</td>
      <td class="small">${v.addr ? `on N${v.addr} · ` : ""}${viaText(v)}</td>
      <td class="n">${stamp(v.since)}<div class="muted small">${span(o.now - v.since)}</div></td><td class="n">${num(v.requests)}</td><td class="n">${size(v.rx)}</td><td class="n">${size(v.tx)}</td></tr>`).join("")}</table>`
    : `<div class="empty">Nobody connected.</div>`;
}

function nodeCard(n) {
  const s = n.stats;
  const running = n.state === "running";
  const peak = Math.max(1, ...n.spark.map((m) => m[0]));
  const hour = n.spark.reduce((a, m) => a + m[0], 0);
  const bars = n.spark.map(([c, b], i) => {
    const mins = 59 - i;
    const tip = `${mins ? mins + " min ago" : "this minute"}: ${c} requests, ${size(b)}`;
    return c ? `<i style="height:${Math.max(6, (c / peak) * 100)}%" title="${tip}"></i>` : `<i class="z" title="${tip}"></i>`;
  }).join("");
  return `<article class="node ${esc(n.state)}" data-id="${esc(n.id)}"${hstyle(hue(n.address))}>
    <div class="node-top">
      <span class="node-name">${esc(n.name)}</span>
      <span class="state">${esc(n.state)}</span>
      ${n.plugin ? `<span class="tag">plugin</span>` : ""}
      ${isHP(n) ? `<span class="tag honey">honeypot · ${num(n.caught)} caught</span>` : ""}
    </div>
    ${n.addresses && n.addresses.length ? [n.address, ...n.addresses].map((a, k) => a ? addrRow(a, `Address N${k + 1}`) : `<div class="addr-row"><span class="addr-label">Address N${k + 1}</span><span class="muted small">made at the first Start</span></div>`).join("") : addrRow(n.address)}
    ${n.error ? `<div class="node-err">${esc(n.error)}</div>` : ""}
    ${n.state === "stopped" ? `<div class="node-hint">Stopped: nobody can reach it. Add or edit its pages with <b>Edit pages</b>, then press <b>Start</b> to put it online.</div>` : ""}
    <div class="signal">
      <div>
        <div class="spark" aria-label="Requests per minute, last hour: ${hour}">${bars}</div>
        <div class="spark-axis"><span>60 min</span><span>${num(hour)} ${hour === 1 ? "request" : "requests"}</span><span>now</span></div>
      </div>
      <div class="readout">
        ${[[n.online.length, "online"], [num(s.today_views), "views today"], [num(s.views), "all time"], [size(s.today_bytes), "sent today"]]
          .map(([v, l]) => `<div><div class="v">${v}</div><div class="l">${l}</div></div>`).join("")}
      </div>
    </div>
    <div class="node-foot">
      <span class="meta">Announced <b>${when(n.last_announce)}</b> · <b>${n.pages}</b> pages · <b>${n.files}</b> files</span>
      ${running ? `<button class="btn small" data-act="announce">Announce</button><button class="btn small" data-act="rescan">Rescan pages</button><button class="btn small" data-act="restart">Restart</button><button class="btn small ghost" data-act="stop">Stop</button>` : `<button class="btn small primary" data-act="start">Start</button>`}
      <button class="btn small ghost" data-act="pages">Edit pages</button>
      <button class="btn small ghost" data-act="stats">Stats</button>
      <button class="btn small ghost" data-act="settings">Settings</button>
      <button class="btn small ghost danger" data-act="delete">Remove</button>
    </div>
  </article>`;
}

async function onNodeAction(e) {
  if (e.target.closest("[data-copy]")) return;
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const id = b.closest(".node").dataset.id;
  const act = b.dataset.act;
  if (act === "pages") return openPages(id);
  const hp = isHP((overview?.nodes || []).find((x) => x.id === id) || {});
  if (act === "stats") {
    if (hp) return $("#hp-strip").scrollIntoView({ behavior: "smooth" });
    $("#stats-node").value = id;
    return setTab("stats");
  }
  if (act === "settings") {
    if (hp) {
      $("#hp-node").value = id;
      renderHpForm();
      return $("#hp-form").scrollIntoView({ behavior: "smooth" });
    }
    $("#set-node").value = id;
    return setTab("settings");
  }
  const confirmText = {
    stop: ["Stop node", `<p>Node <b>${esc(id)}</b> stops serving pages and its visitors are disconnected until you start it again.</p>`, "Stop node"],
    restart: ["Restart node", `<p>Node <b>${esc(id)}</b> restarts: visitors connected now are disconnected.</p>`, "Restart node"],
    delete: [hp ? "Remove honeypot" : "Remove node", `<p><b>${esc(id)}</b> stops and its folder, identity included, moves to Removed nodes in Settings, where you can restore it or delete it for good. Nothing is deleted now.</p>`, "Remove"],
  }[act];
  if (confirmText && !(await ask(confirmText[0], confirmText[1], confirmText[2], true))) return;
  b.disabled = true;
  try {
    await api(`/api/node/${encodeURIComponent(id)}/${act}`, {});
    toast({ announce: "Announced", restart: "Node restarted", rescan: "Pages rescanned", stop: "Node stopped", start: "Node started", delete: "Moved to Removed nodes" }[act]);
    if (act === "delete") { fillNodeSelects.sig = null; renderHpForm.shown = null; }
  } catch (err) {
    toast("Error: " + err.message);
  }
  await loadOverview();
  if (tab === "honeypot") loadHoneypot();
}
$("#nodes").addEventListener("click", onNodeAction);
$("#hp-nodes").addEventListener("click", onNodeAction);

$("#new-node-btn").addEventListener("click", async () => {
  const ok = await ask("New node", `<div class="form"><div class="field"><label for="nn-name">Name</label><input id="nn-name" type="text" maxlength="60" placeholder="My node"></div></div><p class="muted small">Creates a folder with a new identity, pages/ and files/ and a sample home page. The node stays stopped until you add its pages and press Start.</p>`, "Create node");
  const name = $("#nn-name")?.value.trim();
  if (!ok || !name) return;
  try {
    await api("/api/nodes/new", { name });
    toast("Node created, stopped: add its pages, then press Start");
    loadOverview();
  } catch (err) {
    toast("Error: " + err.message);
  }
});

function fillNodeSelects() {
  const nodes = overview?.nodes || [];
  const sig = nodes.map((n) => n.id + n.name + n.type).join("|") + advanced();
  if (fillNodeSelects.sig === sig) return;
  fillNodeSelects.sig = sig;
  for (const sel of ["#stats-node", "#set-node", "#log-node", "#hp-node"]) {
    const el = $(sel);
    const keep = el.value;
    // logs: every node (honeypots only in advanced mode); honeypot settings: honeypots only; the rest: normal nodes only
    const list = sel === "#log-node" ? nodes.filter((n) => advanced() || !isHP(n))
      : sel === "#hp-node" ? nodes.filter((n) => advanced() && isHP(n)) : nodes.filter((n) => !isHP(n));
    const opts = list.map((n) => `<option value="${esc(n.id)}">${esc(n.name)}</option>`).join("");
    el.innerHTML = (sel === "#log-node" ? `<option value="">All nodes</option>` : "")
      + (sel === "#stats-node" && list.length > 1 ? `<option value="all">All nodes (${list.length})</option>` : "") + opts;
    if ([...el.options].some((o) => o.value === keep)) el.value = keep;
    // Stats opens on one node; All nodes only when chosen
    else if (sel === "#stats-node" && list.length) el.value = list[0].id;
  }
  pickDots();
}

function pickDots() {
  for (const [sel, dot] of [["#stats-node", "#stats-dot"], ["#set-node", "#set-dot"]]) {
    const h = nodeHue($(sel).value);
    if (h === null) $(dot).removeAttribute("style");
    else $(dot).setAttribute("style", `--h:${h}`);
  }
}
$("#stats-node").addEventListener("change", pickDots);
$("#set-node").addEventListener("change", pickDots);

// ---- stats ----

$("#stats-node").addEventListener("change", loadStats);
$$("#stats-range button").forEach((b) => b.addEventListener("click", () => {
  statsDays = Number(b.dataset.days);
  $$("#stats-range button").forEach((x) => x.classList.toggle("active", x === b));
  if (lastStats) chart(lastStats);
}));
$$("#chart-mode button").forEach((b) => b.addEventListener("click", () => {
  chartMode = b.dataset.mode;
  $$("#chart-mode button").forEach((x) => x.classList.toggle("active", x === b));
  if (lastStats) chart(lastStats);
}));
window.addEventListener("resize", () => { if (tab === "stats" && lastStats) chart(lastStats); });

async function loadStats() {
  const id = $("#stats-node").value;
  if (!id) {
    $("#stats-req").innerHTML = `<div class="cell empty">No nodes yet.</div>`;
    return;
  }
  let d;
  try {
    d = id === "all" ? await allNodesStats() : await api(`/api/stats?node=${encodeURIComponent(id)}`);
  } catch (e) {
    return;
  }
  const st = d.stats;
  lastStats = st;
  $("#stats-since").textContent = `Counting since ${date(st.since)}` + (st.imported ? ` · ${num(st.imported.views)} views imported from ${st.imported.from}` : "");
  const [req, traffic] = counterCells(d);
  $("#stats-req").innerHTML = req;
  $("#stats-traffic").innerHTML = traffic;
  chart(st);
  const files = merge(st.paths.file, st.paths.media), fileBytes = merge(st.bytes?.file, st.bytes?.media);
  $("#top-pages").innerHTML = topTable(st.paths.page, st.bytes?.page, "Page", "Views");
  $("#top-files").innerHTML = topTable(files, fileBytes, "File", "Requests");
  $("#top-refresh").innerHTML = topTable(st.paths.refresh, st.bytes?.refresh, "Page", "Refreshes");
  const vis = Object.entries(st.visitors || {}).sort((a, b) => b[1].requests - a[1].requests).slice(0, 25);
  $("#top-visitors").innerHTML = vis.length
    ? `<table><tr><th>LXMF address</th><th class="n">Requests</th><th class="n">Sent</th><th class="n">Last seen</th></tr>${vis.map(([h, v]) => `<tr><td><div class="who-cell">${esc(h)}</div>${v.name ? `<div class="muted small">${esc(v.name)}</div>` : ""}<div class="row-acts">${blockBtn(h, v.name, id === "all" ? "" : id, v.identity)}</div></td><td class="n">${num(v.requests)}</td><td class="n">${v.bytes ? size(v.bytes) : "-"}</td><td class="n">${whenCell(v.last)}</td></tr>`).join("")}</table>`
    : `<div class="empty">Nobody has identified yet. Visitors who identify in NomadNet or rBrowser show up here with their LXMF address.</div>`;
}

// every normal node added up; pages and files keep the node name in front
async function allNodesStats() {
  const st = { totals: {}, days: {}, hours: {}, minutes: {}, paths: {}, bytes: {}, visitors: {}, since: 0 }, s = {};
  for (const n of (overview?.nodes || []).filter((x) => !isHP(x))) {
    const d = await api(`/api/stats?node=${encodeURIComponent(n.id)}`);
    const o = d.stats;
    for (const k of ["totals", "days", "hours", "minutes"]) addUp(st[k], o[k]);
    for (const k of ["paths", "bytes"]) {
      for (const [kind, map] of Object.entries(o[k] || {})) {
        const into = st[k][kind] ||= {};
        for (const [p, v] of Object.entries(map || {})) into[`${n.name} · ${p}`] = v;
      }
    }
    for (const [h, v] of Object.entries(o.visitors || {})) {
      const t = st.visitors[h] ||= { requests: 0, bytes: 0, last: 0, name: "", identity: "" };
      t.requests += v.requests || 0;
      t.bytes += v.bytes || 0;
      t.last = Math.max(t.last, v.last || 0);
      t.name ||= v.name || "";
      t.identity ||= v.identity || "";
    }
    if (o.since && (!st.since || o.since < st.since)) st.since = o.since;
    addUp(s, d.node.stats);
  }
  return { stats: st, node: { stats: s } };
}

// Requests and Traffic counters of one node, as in Stats (also under each honeypot)
function counterCells(d) {
  const st = d.stats, s = d.node.stats, t = st.totals;
  const today = st.days[todayKey()] || {};
  return [cells([
    [num(s.views), "page views", "amber"],
    [num(s.today_views), "views today"],
    [num(s.refreshes), "self-refreshes"],
    [num(s.files), "downloads"],
    [num(s.media), "images"],
    [num(t.connects), "connections"],
    [num(t.crawler_links), "crawler-like links"],
    [num(t.limited + t.denied), "refused or denied"],
  ]), cells([
    [size(t.bytes), "content sent", "amber"],
    [size(today.bytes), "content sent today"],
    [size(t.link_tx), "link traffic out"],
    [size(t.link_rx), "link traffic in"],
    [size(today.link_tx), "out today"],
    [size(today.link_rx), "in today"],
    [size(t.connects ? (t.link_tx + t.link_rx) / t.connects : 0), "per connection"],
  ])];
}

const merge = (a, b) => Object.assign({}, a || {}, b || {});

function todayKey(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function topTable(counts, bytes, what, unit, max = 40) {
  const rows = Object.entries(counts || {}).sort((a, b) => b[1] - a[1]);
  if (!rows.length) return `<div class="empty">Nothing yet.</div>`;
  const top = rows[0][1] || 1;
  const total = rows.reduce((a, r) => a + r[1], 0);
  const totalB = Object.values(bytes || {}).reduce((a, v) => a + v, 0);
  const rest = rows.slice(max);
  return `<table><tr><th>${what} (${rows.length})</th><th class="n">${unit}</th><th class="n">Sent</th></tr>${rows.slice(0, max).map(([p, n]) => `<tr><td><div class="path">${esc(p.replace(/^\/(page|file|media)\//, ""))}</div><div class="bar" style="width:${Math.max(2, (n / top) * 100)}%"></div></td><td class="n">${num(n)}</td><td class="n muted">${bytes && bytes[p] ? size(bytes[p]) : "-"}</td></tr>`).join("")}${rest.length ? `<tr><td class="muted">${rest.length} more</td><td class="n muted">${num(rest.reduce((a, r) => a + r[1], 0))}</td><td></td></tr>` : ""}<tr class="total"><td>Total</td><td class="n">${num(total)}</td><td class="n">${totalB ? size(totalB) : "-"}</td></tr></table>`;
}

function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1))));
  for (const m of [1, 2, 5, 10]) if (m * p >= raw) return Math.max(1, m * p);
  return 10 * p;
}

// Stats chart; the Honeypot tab draws the same one with the maze as series of its own
function chart(st, o = {}) {
  const el = o.el || "#chart", days = o.days ?? statsDays, hp = !!o.honeypot;
  const traffic = (o.mode || chartMode) === "traffic";
  const minutely = days === 0, hourly = days === 1;
  const series = hp
    ? (traffic
      ? [["amber", "bait content", (v) => v.bytes || 0], ["bad", "maze sent", (v) => v.maze_bytes || 0],
         ["cyan", "link out", (v) => Math.max(0, (v.link_tx || 0) - (v.bytes || 0) - (v.maze_bytes || 0))], ["slate", "link in", (v) => v.link_rx || 0]]
      : [["amber", "bait pages", (v) => v.page || 0], ["bad", "maze pages", (v) => v.maze_page || 0], ["cyan", "maze files", (v) => v.maze_file || 0],
         ["slate", "bait files", (v) => (v.file || 0) + (v.media || 0)]])
    : traffic
      ? [["amber", "content sent", (v) => v.bytes || 0], ["cyan", "link out", (v) => Math.max(0, (v.link_tx || 0) - (v.bytes || 0))], ["slate", "link in", (v) => v.link_rx || 0]]
      : [["amber", "page views", (v) => v.page || 0], ["slate", "self-refreshes", (v) => v.refresh || 0], ["cyan", "files + images", (v) => (v.file || 0) + (v.media || 0)]];
  $(o.legend || "#legend").innerHTML = series.map(([c, l]) => `<span><i class="sw c-${c}"></i>${l}</span>`).join("")
    + (traffic ? `<span class="muted">link traffic counts when a visitor disconnects</span>` : "");
  const rows = [];
  // 1 hour: one bar per minute (in memory), 24 hours: one per hour (keys "YYYY-MM-DD HH"), otherwise one per day
  const count = minutely ? 60 : hourly ? 24 : days;
  const nowMin = Math.floor(Date.now() / 60000);
  for (let i = count - 1; i >= 0; i--) {
    if (minutely) {
      const d = new Date((nowMin - i) * 60000);
      const v = st.minutes?.[String(nowMin - i)] || {};
      const label = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
      rows.push({ k: `${todayKey(d)} ${label}`, label, vals: series.map(([, , f]) => f(v)), v });
      continue;
    }
    const d = new Date(Date.now() - i * (hourly ? 3600000 : 86400000));
    const k = hourly ? d.toISOString().slice(0, 13).replace("T", " ") : todayKey(d);
    const v = (hourly ? st.hours : st.days)?.[k] || {};
    const label = hourly ? `${String(d.getHours()).padStart(2, "0")}:00` : k.slice(5);
    rows.push({ k: hourly ? `${todayKey(d)} ${label}` : k, label, vals: series.map(([, , f]) => f(v)), v });
  }
  const peak = Math.max(1, ...rows.map((r) => r.vals.reduce((a, b) => a + b, 0)));
  const step = traffic ? niceBytes(peak / 4) : niceStep(peak / 4);
  const max = step * Math.ceil(peak / step);
  const W = Math.max(280, Math.round($(el).clientWidth) || 1000), H = 230, L = traffic ? 62 : 44, B = 22, T = 8;
  const bw = (W - L) / rows.length;
  const y = (v) => H - B - (v / max) * (H - B - T);
  const fmt = traffic ? size : num;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${traffic ? "Traffic" : "Requests"} per ${minutely ? "minute" : hourly ? "hour" : "day"}">`;
  for (let v = 0; v <= max; v += step) {
    svg += `<line class="gl" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 8}" y="${y(v) + 3}" text-anchor="end">${fmt(v)}</text>`;
  }
  const every = Math.ceil(rows.length / Math.max(3, Math.floor(W / 70)));
  rows.forEach((r, i) => {
    const x = L + i * bw + bw * 0.14, w = Math.max(1, bw * 0.72);
    const tip = hp
      ? (traffic ? `${r.k}: ${size(r.v.bytes)} bait, ${size(r.v.maze_bytes)} maze, ${size(r.v.link_tx)} out, ${size(r.v.link_rx)} in`
        : `${r.k}: ${num(r.v.page)} bait pages, ${num(r.v.maze_page)} maze pages, ${num(r.v.maze_file)} maze files, ${num(r.v.connects)} connections`)
      : traffic
        ? `${r.k}: ${size(r.v.bytes)} content, ${size(r.v.link_tx)} out, ${size(r.v.link_rx)} in`
        : `${r.k}: ${num(r.v.page)} views, ${num(r.v.refresh)} refreshes, ${num((r.v.file || 0) + (r.v.media || 0))} files/images, ${num(r.v.connects)} connections`;
    let base = 0;
    svg += `<g><title>${tip}</title>`;
    series.forEach(([c], j) => {
      const v = r.vals[j];
      if (!v) return;
      svg += `<rect class="c-${c}" x="${x}" y="${y(base + v)}" width="${w}" height="${Math.max(1, y(base) - y(base + v))}"/>`;
      base += v;
    });
    if (!base) svg += `<rect fill="transparent" x="${x}" y="${T}" width="${w}" height="${H - B - T}"/>`;
    svg += `</g>`;
    if (i % every === 0) svg += `<text x="${x + w / 2}" y="${H - 6}" text-anchor="middle">${r.label}</text>`;
  });
  $(el).innerHTML = svg + "</svg>";
}

function niceBytes(raw) {
  let unit = 1;
  while (raw >= unit * 1024 && unit < 1024 ** 3) unit *= 1024;
  return niceStep(raw / unit) * unit;
}

// ---- logs ----

$("#log-node").addEventListener("change", renderLog);
$("#log-filter").addEventListener("input", renderLog);
$$("#log-kinds button").forEach((b) => b.addEventListener("click", () => {
  logKind = b.dataset.kind;
  $$("#log-kinds button").forEach((x) => x.classList.toggle("active", x === b));
  renderLog();
}));

async function pollLog() {
  try {
    const d = await api(`/api/log?after=${logAfter}&limit=1000`);
    if (d.lines.length) {
      logAfter = d.lines[d.lines.length - 1].seq;
      logRows = logRows.concat(d.lines).slice(-3000);
      if (tab === "logs") renderLog();
    }
  } catch (e) { /* offline: try again */ }
}

function logMatch(r) {
  const node = $("#log-node").value;
  if (node && r.node !== node) return false;
  if (!advanced() && hpNodes().some((n) => n.id === r.node)) return false;
  const k = r.kind;
  if (logKind === "page" && !(k === "page" || k === "media")) return false;
  if (logKind === "connect" && k !== "connect") return false;
  if (logKind === "file" && k !== "file") return false;
  if (logKind === "problem" && !(k === "warn" || k === "error")) return false;
  const f = $("#log-filter").value.trim().toLowerCase();
  return !f || r.text.toLowerCase().includes(f) || (r.node || "").toLowerCase().includes(f);
}

function renderLog() {
  const box = $("#log");
  const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const rows = logRows.filter(logMatch).slice(-1500);
  box.innerHTML = rows.length ? rows.map((r) => {
    const t = new Date(r.t * 1000);
    const ts = t.toLocaleDateString("en-GB", { day: "2-digit", month: "2-digit" }) + " " + t.toLocaleTimeString("en-GB");
    return `<div class="ln ${esc(r.kind)}"${r.node ? hstyle(nodeHue(r.node)) : ""}><span class="t">${ts}</span><span class="nd">${esc(r.node || "server")}</span><span class="tx">${esc(r.text)}</span></div>`;
  }).join("") : `<div class="empty">No lines match.</div>`;
  if ($("#log-follow").checked || atEnd) box.scrollTop = box.scrollHeight;
}

// ---- settings ----

const advanced = () => { try { return localStorage.getItem("ns-advanced") === "1"; } catch (e) { return false; } };
function applyAdvanced() {
  document.documentElement.classList.toggle("advanced", advanced());
}
applyAdvanced();
async function setAdvanced(on) {
  // advanced mode off = honeypots off: they stop, and stay stopped until started again
  const running = on ? [] : hpNodes().filter((n) => n.state === "running");
  if (running.length) {
    if (!(await ask("Turn off advanced mode", `<p>Honeypots running now: <b>${running.map((n) => esc(n.name)).join(", ")}</b>. They are stopped and stay stopped; turn advanced mode on again and press Start to bring them back. The blocklist stays as it is.</p>`, "Stop honeypots and turn off", true))) return;
    for (const n of running) {
      await api(`/api/node/${encodeURIComponent(n.id)}/stop`, {}).catch((e) => toast("Error: " + e.message));
    }
    await loadOverview();
  }
  try { localStorage.setItem("ns-advanced", on ? "1" : "0"); } catch (err) { /* private mode */ }
  applyAdvanced();
  fillNodeSelects();
  renderLog();
  loadBlocked();
  loadTrash();
  toast(on ? "Advanced mode on: see the Honeypot tab" : "Advanced mode off: honeypots stopped and hidden");
  if (!on && tab === "honeypot") setTab("overview");
}
let logoClicks = [];
document.addEventListener("click", (e) => {
  if (!e.target.closest(".about-splash")) return;
  const now = Date.now();
  logoClicks = logoClicks.filter((t) => now - t < 3000).concat(now);
  if (logoClicks.length < 5) return;
  logoClicks = [];
  setAdvanced(!advanced());
});
$("#adv-off").addEventListener("click", () => setAdvanced(false));

const isHP = (n) => n.type === "honeypot";
const hpNodes = () => (overview?.nodes || []).filter(isHP);

const NODE_FIELDS = [
  ["node", "name", "text", "Name", "Shown in NomadNet and in the announces"],
  ["node", "enabled", "bool", "Enabled", "Start this node with the server"],
  ["node", "announce_at_start", "bool", "Announce at start", "Without an announce the node cannot be reached after a restart"],
  ["node", "announce_interval", "number", "Announce every", "Minutes, 0 = only at start"],
  ["node", "rescan_interval", "number", "Rescan pages every", "Minutes, 0 = never (new pages appear after a rescan)"],
  ["node", "plugin", "text", "Plugin file", "Python file with dynamic pages, in the node folder"],
  ["node", "require_identify", "bool", "Identify to see content", "Visitors who do not identify get a page asking them to (like NomadNet and rngit)"],
  ["limits", "requests_per_minute", "number", "Requests per minute", "Per visitor, 0 = no limit"],
  ["counter", "show_on_pages", "bool", "Counters on pages", "Fill {{visits}}, {{visits_total}} and {{visits_today}}. Requests are counted either way"],
  ["footer", "visits", "bool", "Visits line on every page", "Adds the counters at the bottom of .mu pages that have none of their own"],
  ["footer", "watermark", "bool", "Served by nomad-server", "A small line with the project link at the bottom of .mu pages"],
  ["footer", "center", "bool", "Center the footer", "Off = aligned left"],
];
// honeypot nodes are special: their own fields, only in the Honeypot tab
const HP_FIELDS = [
  ["node", "name", "text", "Name", "Make it look interesting: crawlers love archives"],
  ["node", "enabled", "bool", "Enabled", "Start it with the server"],
  ["node", "announce_at_start", "bool", "Announce at start", ""],
  ["node", "announce_interval", "number", "Announce every", "Minutes, 0 = only at start"],
  ["honeypot", "addresses", "select:1,2,3", "Addresses", "More addresses serving the same pages, announced as \"<name> N1\", \"N2\", \"N3\" two seconds apart: crawlers have a budget per node, so each address catches more. Applies when the node restarts"],
  ["honeypot", "multi_seconds", "number", "Same peer on all addresses: within seconds", "An identity that connects to every address within this time is caught like a trap: people pick one node from a list, crawlers take them all. 0 = off"],
  ["honeypot", "traps", "text", "Trap pages", "Comma separated, * at the end = everything below (e.g. /page/trap/*). Opening one gets the visitor caught"],
  ["honeypot", "mode", "select:page,silent,tarpit,maze=maze pages,maze_files=maze files,maze_both=maze both", "Answer caught visitors with", "page: blocked page with the reason; silent: dropped; tarpit: no answers; maze: endless small fake pages, fake file downloads or both, never refused: the crawler keeps busy here instead of on your real nodes"],
  ["honeypot", "slow_maze", "select:off,fixed=fixed delay,adaptive=adaptive delay", "Slow maze", "Maze answers sent late: a crawler's page budget costs it more time. Adaptive: just before the visitor's request times out (worked out from the link's round trip, about 10 s); fixed: the seconds below. Too long and the crawler times out without the page"],
  ["honeypot", "slow_seconds", "number", "Slow maze: fixed delay, seconds", "Used with fixed delay; NomadNet clients wait about 11 s at least"],
  ["honeypot", "reason", "text", "Reason for caught visitors", "Shown with the page mode"],
  ["honeypot", "to_blocklist", "bool", "Put caught visitors in the blocklist", "Off: an identified crawler stays caught on the honeypots only (the maze or tarpit keeps going, it is listed in Known crawlers). On: it also goes into the Blocklist"],
  ["honeypot", "autoblock", "bool", "Also catch crawler-like visitors", "Same answer as the traps, also for link hoppers (a new link for every page) and links opening many pages, fast or over an hour"],
  ["honeypot", "hop_links", "number", "Link hopping: links", "An identity opening this many links with one or two pages each... (people keep one link open while they browse)"],
  ["honeypot", "hop_seconds", "number", "Link hopping: within seconds", "...within this many seconds"],
  ["honeypot", "crawler_pages", "number", "Fast crawler: pages on one link", "Different pages on a single link..."],
  ["honeypot", "crawler_seconds", "number", "Fast crawler: within seconds", "...within this many seconds"],
  ["honeypot", "long_pages", "number", "Slow crawler: pages on one link", "Different pages on a single link, over a longer time..."],
  ["honeypot", "long_seconds", "number", "Slow crawler: within seconds", "...within this many seconds"],
  ["node", "require_identify", "bool", "Identify to see content", "On: only identified crawlers get in, and they can be blocked by address"],
  ["limits", "requests_per_minute", "number", "Requests per minute", "Per visitor, 0 = no limit"],
];
const SERVER_FIELDS = [
  ["server", "rns_config", "text", "Reticulum config folder", "Empty = the default (~/.reticulum)"],
  ["server", "nodes_dir", "text", "Nodes folder", "One sub-folder per node"],
  ["server", "log_to_file", "bool", "Log to file", "logs/server.log instead of the terminal"],
  ["webui", "host", "text", "Web UI address", "127.0.0.1 = this computer only, 0.0.0.0 = the whole network (set a password)"],
  ["webui", "port", "number", "Web UI port", ""],
  ["webui", "splash", "bool", "Splash screen on start", "Shows the logo for a few seconds when the web UI opens"],
  ["blocklist", "reasons", "text", "Block reasons", "Offered when you block a peer, separated by |"],
];

function formHtml(fields, values, prefix) {
  return fields.map(([sec, key, type, label, hint]) => {
    const id = `${prefix}-${sec}-${key}`;
    const v = values?.[sec]?.[key] ?? "";
    const on = /^(yes|true|on|1)$/i.test(v);
    // data-orig: the value drawn here, so a save sends only what was changed in the form
    const orig = `data-orig="${esc(type === "bool" ? (on ? "yes" : "no") : String(v).trim())}"`;
    const input = type.startsWith("select:")
      ? `<select id="${id}" ${orig}>${type.slice(7).split(",").map((o) => { const [val, label] = o.split("="); return `<option value="${esc(val)}" ${val === v ? "selected" : ""}>${esc(label || val)}</option>`; }).join("")}</select>`
      : type === "bool"
      ? `<input id="${id}" type="checkbox" ${orig} ${on ? "checked" : ""}>`
      : `<input id="${id}" type="${type}" ${orig} value="${esc(v)}" ${type === "number" ? 'min="0"' : ""}>`;
    return `<div class="field"><label for="${id}">${label}</label>${input}${hint ? `<div class="hint">${esc(hint)}</div>` : ""}</div>`;
  }).join("");
}

function formValues(fields, prefix) {
  const out = {};
  for (const [sec, key, type] of fields) {
    const el = $(`#${prefix}-${sec}-${key}`);
    const v = type === "bool" ? (el.checked ? "yes" : "no") : el.value.trim();
    // an untouched field may be stale (e.g. Enabled after Start): never send it back
    if (v !== el.dataset.orig) (out[sec] ||= {})[key] = v;
  }
  return out;
}

$("#set-node").addEventListener("change", renderNodeForm);

function renderNodeForm() {
  const n = overview?.nodes.find((x) => x.id === $("#set-node").value);
  $("#node-addr").innerHTML = n
    ? `<div class="field"><span class="field-label">Server address</span><button class="addr" data-copy="${esc(n.address)}" title="Copy address">${esc(n.address)}${COPY_ICON}</button></div>`
    : "";
  $("#node-form").innerHTML = n ? formHtml(NODE_FIELDS, n.settings, "nf") : `<div class="empty">No nodes yet.</div>`;
  $("#node-msg").textContent = n ? `Folder: nodes/${n.id}` : "";
}

async function loadSettings() {
  await loadOverview();
  renderNodeForm();
  loadBlocked();
  loadTrash();
  loadShortcuts();
  try {
    const s = await api("/api/settings");
    $("#server-form").innerHTML = formHtml(SERVER_FIELDS, s.server, "sf");
    $("#pw-msg").textContent = s.has_password ? "A password is set." : "No password: anyone who can open this page can use it.";
    canOpen = !!s.can_open;
    $("#set-open").classList.toggle("hidden", !canOpen);
    const row = (label, path, target) => `<div class="path-row"><span>${label}</span><span class="mono">${esc(path)}</span>${canOpen ? `<button class="btn small ghost" data-open="${target}">Open folder</button>` : "<span></span>"}</div>`;
    $("#paths").innerHTML = row("Server", s.base, "base") + row("Nodes", s.nodes_dir, "nodes")
      + (canOpen ? "" : `<div class="muted small">Folders open in a window only when this page is on the computer running the server.</div>`);
  } catch (e) { /* login */ }
}

// ---- blocked visitors ----

let blocked = {};
let blackholed = new Set();
// Reticulum blackhole of a blocked peer's identity: by hand only, it acts on the whole instance
// the identity comes from the blocklist or the caller; else the server looks it up
const bhBtn = (who, identity) => {
  const id = identity || blocked[who]?.identity;
  return id && blackholed.has(id)
    ? `<button class="btn-unblock" data-bh="${esc(who)}" data-id="${esc(id)}" data-on="0" title="Lift the blackhole in Reticulum">Unblackhole</button>`
    : `<button class="btn-block" data-bh="${esc(who)}" data-id="${esc(id || "")}" data-on="1" title="Blackhole its identity in Reticulum too">Blackhole</button>`;
};
// blocks on the honeypots follow the mode each honeypot uses now
const blockMode = (b, node) => {
  if (b.scope !== "honeypots") return b.mode || "page";
  const n = (overview?.nodes || []).find((x) => x.id === node) || hpNodes()[0];
  return n?.settings?.honeypot?.mode || b.mode || "page";
};
const nodeName = (id) => ((overview?.nodes || []).find((n) => n.id === id) || {}).name || id;
const scopeText = (sc) => (!sc || sc === "all") ? "every node" : sc === "honeypots" ? "honeypots only" : nodeName(sc);
const widenBtn = (who) => `<button class="btn-block" data-widen="${esc(who)}" title="Block it on every node of this server">Block on all nodes</button>`;
const blockBtn = (who, name, node, identity) => {
  const b = blocked[who];
  if (!b) return `<button class="btn-block" data-block="${esc(who)}" data-name="${esc(name || "")}" data-node="${esc(node || "")}" title="Block this LXMF address">Block</button> ${bhBtn(who, identity)}`;
  const all = !b.scope || b.scope === "all";
  // the flag on its own line, the buttons together in one row under it
  return `<span class="flag bad">blocked${all ? "" : " on " + esc(scopeText(b.scope))}</span> <span class="btn-row">${all ? "" : widenBtn(who) + " "}<button class="btn-unblock" data-unblock="${esc(who)}" title="Remove it from the blocklist">Unblock</button> ${bhBtn(who)}</span>`;
};

let blockReasons = [];

// after a block change: the blocklist, then the tab on screen
async function refreshBlocks() {
  await loadBlocked();
  if (tab === "honeypot") loadHoneypot();
  if (tab === "overview") loadOverview();
  if (tab === "stats") loadStats();
}

async function loadBlocked() {
  try {
    const d = await api("/api/blocklist");
    blocked = d.items || {};
    blockReasons = d.reasons || [];
    blackholed = new Set(d.blackholed || []);
  } catch (e) {
    return;
  }
  // the blocklist is always shown, honeypot blocks included
  const rows = Object.entries(blocked).sort((a, b) => (b[1].at || 0) - (a[1].at || 0));
  $("#blocked").innerHTML = rows.length
    ? `<div class="table-wrap"><table><tr><th>LXMF address</th><th>Name</th><th>Reason</th><th>Answer</th><th>Where</th><th class="n">Blocked</th><th></th></tr>${rows.map(([h, b]) => `<tr><td class="who-cell">${esc(h)}</td><td>${esc(b.name || "")}</td><td>${esc(b.reason || "-")}</td><td>${b.scope === "honeypots" ? "as each honeypot is set" : { silent: "silent, dropped", tarpit: "tarpit, no answers", maze: "maze, fake pages" }[b.mode] || "page with the reason"}</td><td>${esc(scopeText(b.scope))}${!b.scope || b.scope === "all" ? "" : " " + widenBtn(h)}${b.node ? `<div class="muted small">${String(b.via || "").startsWith("auto") ? "caught on" : "seen on"} ${esc(nodeName(b.node))}</div>` : ""}</td><td class="n">${whenCell(b.at)}</td><td class="n"><button class="btn small ghost" data-unblock="${esc(h)}">Unblock</button> ${bhBtn(h)}</td></tr>`).join("")}</table></div>`
    : `<div class="empty">Nobody blocked. Use Block next to an LXMF address in Overview or Stats.</div>`;
}

// The block dialog: reason and mode. For a link the block lasts until it disconnects.
async function askBlock(title, intro, forLink, node) {
  const opts = blockReasons.map((r) => `<option>${esc(r)}</option>`).join("");
  const ok = await ask(title, `${intro}
    <div class="form">
      <div class="field"><label for="bl-reason">Reason</label><select id="bl-reason">${opts}<option value="">No reason</option></select></div>
      <div class="field"><label for="bl-page">Show a page with the reason</label><input id="bl-page" type="radio" name="bl-mode" value="page" checked><div class="hint">It gets "Access blocked" and the reason for every page: its users see why.</div></div>
      <div class="field"><label for="bl-silent">Silent</label><input id="bl-silent" type="radio" name="bl-mode" value="silent"><div class="hint">${forLink ? "Disconnected now" : "Dropped as soon as it identifies"}, no answer at all.</div></div>
      <div class="field"><label for="bl-tarpit">Tarpit</label><input id="bl-tarpit" type="radio" name="bl-mode" value="tarpit"><div class="hint">Kept connected, every request waits until it times out: the crawler loses time, the mesh carries nothing.</div></div>
      ${forLink || !node ? "" : `<div class="field"><label for="bl-all">On every node</label><input id="bl-all" type="radio" name="bl-scope" checked><div class="hint">Stopped on all the nodes of this server.</div></div>
      <div class="field"><label for="bl-one">Only on ${esc(node)}</label><input id="bl-one" type="radio" name="bl-scope"><div class="hint">The other nodes still answer it.</div></div>`}
    </div>`, "Block", true);
  if (!ok) return null;
  return { reason: $("#bl-reason").value, mode: $("#bl-silent").checked ? "silent" : $("#bl-tarpit").checked ? "tarpit" : "page",
           scope: $("#bl-one")?.checked ? node : "all" };
}

document.addEventListener("click", async (e) => {
  const ul = e.target.closest("[data-unblocklink]");
  if (!ul) return;
  try {
    await api(`/api/node/${encodeURIComponent(ul.dataset.node)}/unblock_link`, { link: ul.dataset.unblocklink });
    toast(ul.dataset.maze ? "Released from the maze" : "Link unblocked");
  } catch (err) {
    toast("Error: " + err.message);
  }
  await loadOverview();
  if (tab === "honeypot") loadHoneypot();
});

document.addEventListener("click", async (e) => {
  const h = e.target.closest("[data-bh]");
  if (!h) return;
  const who = h.dataset.bh, on = h.dataset.on === "1", id = h.dataset.id || blocked[who]?.identity || "";
  const text = on
    ? `<p>${id ? `Identity <span class="mono">${esc(id)}</span>` : "The identity"} (LXMF <span class="mono">${esc(who)}</span>) is blackholed in Reticulum: this instance drops its announces and the paths to all its destinations, its node and LXMF address included, for every program using this Reticulum.</p>
       <p>It keeps reaching your nodes: the blocklist handles that. To share it, use Export as text or publish_blackhole in the rnsd config.</p>`
    : `<p>The blackhole of identity <span class="mono">${esc(id)}</span> is lifted: its announces and paths are accepted again. It stays in the blocklist.</p>`;
  if (!(await ask(on ? "Blackhole in Reticulum" : "Lift the blackhole", text, on ? "Blackhole" : "Unblackhole", on))) return;
  try {
    await api("/api/blackhole", { who, identity: id, on });
    toast(on ? "Blackholed in Reticulum" : "Blackhole lifted");
  } catch (err) {
    toast("Error: " + err.message);
  }
  refreshBlocks();
});

document.addEventListener("click", async (e) => {
  const w = e.target.closest("[data-widen]");
  if (!w) return;
  if (!(await ask("Block on all nodes", `<p>LXMF <span class="mono">${esc(w.dataset.widen)}</span> was blocked only on some nodes. From now on every node of this server treats it the same way.</p>`, "Block on all nodes", true))) return;
  try {
    await api("/api/blocklist/scope", { who: w.dataset.widen, scope: "all" });
    toast("Blocked on every node");
  } catch (err) {
    toast("Error: " + err.message);
  }
  refreshBlocks();
});

document.addEventListener("click", async (e) => {
  const l = e.target.closest("[data-blocklink]");
  if (!l) return;
  const choice = await askBlock("Block this link", `<p>Link <span class="mono">${esc(l.dataset.blocklink)}</span> on ${esc(l.dataset.node)} did not identify: it has no address, only this connection. The block lasts <b>until it disconnects</b>; to block a peer for good it has to identify (turn on "Identify to see content").</p>`, true);
  if (!choice) return;
  try {
    await api(`/api/node/${encodeURIComponent(l.dataset.node)}/block_link`, { link: l.dataset.blocklink, ...choice });
    toast("Link blocked until it disconnects");
  } catch (err) {
    toast("Error: " + err.message);
  }
  await loadOverview();
  if (tab === "honeypot") loadHoneypot();
});

document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-block]");
  const u = e.target.closest("[data-unblock]");
  if (!b && !u) return;
  let reason = "", mode = "page", scope = "all";
  if (b) {
    const name = b.dataset.name;
    const choice = await askBlock("Block this peer", `<p>LXMF <span class="mono">${esc(b.dataset.block)}</span>${name ? ` (${esc(name)})` : ""}. Crawlers often identify on their own to read protected pages.</p>`, false, b.dataset.node);
    if (!choice) return;
    ({ reason, mode } = choice);
    scope = choice.scope;
  }
  const id = u && blocked[u.dataset.unblock]?.identity;
  let lift = false;
  if (u && id && blackholed.has(id)) {
    if (!(await ask("Unblock", `<p>LXMF <span class="mono">${esc(u.dataset.unblock)}</span> leaves the blocklist. Its identity is also blackholed in Reticulum.</p>
      <div class="field"><label for="bh-lift">Lift the Reticulum blackhole too</label><input id="bh-lift" type="checkbox" checked></div>`, "Unblock"))) return;
    lift = $("#bh-lift").checked;
  }
  try {
    if (b) await api("/api/blocklist/add", { who: b.dataset.block, name: b.dataset.name, node: b.dataset.node, reason, mode, scope });
    else {
      if (lift) await api("/api/blackhole", { who: u.dataset.unblock, on: false });
      await api("/api/blocklist/remove", { who: u.dataset.unblock });
    }
    toast(b ? "Blocked" : "Unblocked");
  } catch (err) {
    toast("Error: " + err.message);
  }
  refreshBlocks();
});

async function loadTrash() {
  let d;
  try {
    d = await api("/api/trash");
  } catch (e) {
    return;
  }
  d.items = d.items.filter((t) => advanced() || t.type !== "honeypot");
  $("#trash").innerHTML = d.items.length
    ? `<div class="table-wrap"><table><tr><th>Node</th><th>Address</th><th class="n">Removed</th><th class="n">Size</th><th></th></tr>${d.items.map((t) => `<tr>
        <td><span class="nm-cell"${hstyle(hue(t.address))}><span class="ndot"></span>${esc(t.name || t.id)}</span><div class="muted small mono">${esc(t.entry)}</div></td>
        <td class="who-cell">${esc(t.address || "no identity")}</td>
        <td class="n">${t.removed ? whenCell(t.removed) : "-"}</td><td class="n">${size(t.size)}</td>
        <td class="n"><button class="btn small" data-trash="restore" data-entry="${esc(t.entry)}" ${d.edit ? "" : "disabled"}>Restore</button>
          <button class="btn small ghost danger" data-trash="delete" data-entry="${esc(t.entry)}" ${d.edit ? "" : "disabled"}>Delete forever</button></td></tr>`).join("")}</table></div>`
    : `<div class="empty">No removed nodes.</div>`;
}

$("#trash").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-trash]");
  if (!b) return;
  const entry = b.dataset.entry, act = b.dataset.trash;
  const ok = act === "restore"
    ? await ask("Restore node", `<p><b>${esc(entry)}</b> comes back as a node with its identity, address, pages and counters, and starts. It is refused if another node uses the same identity.</p>`, "Restore node")
    : await ask("Delete forever", `<p><b>${esc(entry)}</b> is deleted from the disk, <b>identity included</b>: its address is lost for good. This cannot be undone. Make a backup first if you may need it.</p>`, "Delete forever", true);
  if (!ok) return;
  try {
    const r = await api(`/api/trash/${act}`, { entry });
    toast(act === "restore" ? `Node ${r.id} restored` : "Deleted for good");
    fillNodeSelects.sig = null;
    await loadOverview();
    loadTrash();
  } catch (err) {
    toast("Error: " + err.message);
  }
});

async function loadShortcuts() {
  let st;
  try { st = await api("/api/shortcuts"); } catch (e) { return; }
  $("#sc-box").classList.toggle("hidden", !st.supported);
  if (!st.supported) return;
  $("#sc-desktop").checked = !!st.desktop;
  $("#sc-menu").checked = !!st.menu;
  $("#sc-menu").closest(".field").classList.toggle("hidden", !st.menu_supported);
  $("#sc-hint").textContent = st.hint || "";
}
$("#sc-box").addEventListener("change", async (e) => {
  const el = e.target.closest("[data-sc]");
  if (!el) return;
  try {
    await api("/api/shortcuts", { kind: el.dataset.sc, on: el.checked });
    toast(el.checked ? "Icon created" : "Icon removed");
  } catch (err) {
    toast("Error: " + err.message);
  }
  loadShortcuts();
});

let canOpen = false;
async function openFolder(target, node) {
  try {
    await api("/api/open_folder", { target, node });
  } catch (err) {
    toast("Error: " + err.message);
  }
}
$("#paths").addEventListener("click", (e) => {
  const b = e.target.closest("[data-open]");
  if (b) openFolder(b.dataset.open);
});
$("#set-open").addEventListener("click", () => openFolder("node", $("#set-node").value));
$("#set-edit").addEventListener("click", () => { if ($("#set-node").value) openPages($("#set-node").value); });

async function saveNode(restart) {
  const id = $("#set-node").value;
  if (!id) return;
  if (restart && !(await ask("Save and restart node", `<p>Node <b>${esc(id)}</b> restarts with the new settings: visitors connected now are disconnected.</p>`, "Save and restart", true))) return;
  try {
    await api(`/api/node/${encodeURIComponent(id)}/settings`, formValues(NODE_FIELDS, "nf"));
    if (restart) await api(`/api/node/${encodeURIComponent(id)}/restart`, {});
    toast(restart ? "Saved and restarted" : "Saved. A new plugin file needs a restart");
    await loadOverview();
    renderNodeForm();
  } catch (err) {
    toast("Error: " + err.message);
  }
}
$("#node-save").addEventListener("click", () => saveNode(false));
$("#node-save-restart").addEventListener("click", () => saveNode(true));

$("#node-reset").addEventListener("click", async () => {
  const id = $("#set-node").value;
  if (!id || !(await ask("Reset counters", `<p>All counters of <b>${esc(id)}</b> go back to zero. This cannot be undone.</p>`, "Reset counters", true))) return;
  await api(`/api/node/${encodeURIComponent(id)}/reset_stats`, {}).then(() => toast("Counters reset"), (e) => toast("Error: " + e.message));
});

$("#node-delete").addEventListener("click", async () => {
  const id = $("#set-node").value;
  if (!id || !(await ask("Remove node", `<p>The node <b>${esc(id)}</b> stops and its folder, identity included, moves to Removed nodes (below), where you can restore it. Nothing is deleted.</p>`, "Remove node", true))) return;
  try {
    await api(`/api/node/${encodeURIComponent(id)}/delete`, {});
    toast("Node moved to Removed nodes");
    fillNodeSelects.sig = null;
    loadSettings();
  } catch (err) {
    toast("Error: " + err.message);
  }
});

$("#server-save").addEventListener("click", async () => {
  try {
    await api("/api/settings", formValues(SERVER_FIELDS, "sf"));
    $("#server-msg").textContent = "Saved. Address, port, folders and log file apply after a restart.";
  } catch (err) {
    $("#server-msg").textContent = "Error: " + err.message;
  }
});

$("#pw-save").addEventListener("click", async () => {
  const a = $("#pw1").value, b = $("#pw2").value;
  if (a !== b) return ($("#pw-msg").textContent = "The two passwords differ.");
  if (!a && !(await ask("Remove password", "<p>Without a password anyone who can open this page can manage the server. Over the network, pages and identities become read only.</p>", "Remove password", true))) return;
  try {
    await api("/api/password", { password: a });
    $("#pw1").value = $("#pw2").value = "";
    $("#pw-msg").textContent = a ? "Password set." : "Password removed.";
    $("#logout").classList.toggle("hidden", !a);
  } catch (err) {
    $("#pw-msg").textContent = "Error: " + err.message;
  }
});

$("#server-stop").addEventListener("click", async () => {
  if (!(await ask("Stop server", "<p>All nodes go offline and this page stops answering. To start it again you need the computer: <span class='mono'>python nomad_server.py</span>, the start file or the service.</p>", "Stop server", true))) return;
  try {
    await api("/api/shutdown", {});
    $("#restart-msg").textContent = "Stopped. Start it again on the server.";
  } catch (err) {
    $("#restart-msg").textContent = "Error: " + err.message;
  }
});

$("#server-restart").addEventListener("click", async () => {
  if (!(await ask("Restart server", "<p>All nodes stop and start again. Visitors connected now are disconnected.</p>", "Restart server", true))) return;
  try {
    await api("/api/restart", {});
  } catch (e) { /* it is going down */ }
  $("#restart-msg").textContent = "Restarting...";
  const t0 = Date.now();
  const wait = async () => {
    try {
      const o = await api("/api/overview");
      if (o.started * 1000 > t0 - 1000) {
        $("#restart-msg").textContent = "Restarted.";
        logAfter = 0; logRows = [];
        return loadSettings();
      }
    } catch (e) { /* not up yet */ }
    if (Date.now() - t0 < 90000) setTimeout(wait, 1500);
    else $("#restart-msg").textContent = "No answer after 90 s: check the server.";
  };
  setTimeout(wait, 2500);
});

$("#honeypot-btn").addEventListener("click", async () => {
  const ok = await ask("Create honeypot node", `<div class="form"><div class="field"><label for="hp-name">Name</label><input id="hp-name" type="text" maxlength="60" value="Mesh Archive"></div></div>
    <p class="muted small">A bait node: an archive of many linked pages with hidden trap links that people never see and crawlers always follow. Whoever opens a trap, or browses like a crawler, is caught and kept in an endless maze of fake pages (maze by default, not added to the blocklist). It is created stopped: press Start to put it online.</p>`, "Create honeypot");
  const name = $("#hp-name")?.value.trim();
  if (!ok || !name) return;
  try {
    await api("/api/nodes/honeypot", { name });
    toast("Honeypot created, stopped: press Start to put it online");
    await loadOverview();
    loadHoneypot();
  } catch (err) {
    toast("Error: " + err.message);
  }
});

// ---- honeypot tab ----

let hpFilter = "all";
let hpAll = [], crawlerRows = [];   // what the two lists show, for Export as text
const hpShown = (p) => hpFilter === "all" || (hpFilter === "bad" && (p.state === "caught" || p.state === "blocked"))
  || (hpFilter === "online" && p.online) || (hpFilter === "id" && p.who) || (hpFilter === "anon" && !p.who);
let hpOrder = "newest";
// the server keeps up to 60 distinct pages per connection
const HP_PAGES = 60;
try { hpOrder = localStorage.getItem("ns-hp-order") === "oldest" ? "oldest" : "newest"; } catch (e) { /* private mode */ }
document.addEventListener("click", (e) => {
  const o = e.target.closest("[data-hpo]");
  if (!o || o.dataset.hpo === hpOrder) return;
  hpOrder = o.dataset.hpo;
  try { localStorage.setItem("ns-hp-order", hpOrder); } catch (err) { /* private mode */ }
  hpOpen.clear();
  document.querySelectorAll("#hp-peers details.hp-pages[open]").forEach((d) => { d.open = false; });
  loadHoneypot();
});
// the list redraws every 5 s, but not while a page list is open: the rows would move under the pointer
const hpOpen = new Set();
const hpPaused = () => !!document.querySelector("#hp-peers details.hp-pages[open]");
document.addEventListener("toggle", (e) => {
  const d = e.target;
  if (!d.matches || !d.matches("details.hp-pages")) return;
  if (d.open) hpOpen.add(d.dataset.k); else hpOpen.delete(d.dataset.k);
  $("#hp-paused").hidden = !hpPaused();
  if (!d.open && !hpPaused()) loadHoneypot();
}, true);
document.addEventListener("click", (e) => {
  const f = e.target.closest("[data-hpf]");
  if (!f) return;
  hpFilter = f.dataset.hpf;
  // a new filter is a new list: close the open page lists so it is not paused
  hpOpen.clear();
  document.querySelectorAll("#hp-peers details.hp-pages[open]").forEach((d) => { d.open = false; });
  loadHoneypot();
});

async function fetchCrawlers() {
  try {
    return (await api("/api/honeypot/crawlers")).items || {};
  } catch (e) {
    return null;
  }
}

async function loadCrawlers() {
  const items = await fetchCrawlers();
  const anchor = hpAnchor();
  renderCrawlers(items);
  hpRestore(anchor);
}

function renderCrawlers(items) {
  if (!items) return;
  const hidden = Object.values(items).filter((c) => c.hidden).length;
  const rows = Object.entries(items).filter(([, c]) => !c.hidden).sort((a, b) => (b[1].last || 0) - (a[1].last || 0));
  crawlerRows = rows;
  $("#hp-crawlers").innerHTML = rows.length
    ? `<div class="table-wrap"><table><tr><th>Crawler</th><th>Honeypots</th><th class="n">Visits</th><th class="n">Caught</th><th class="n">First seen</th><th class="n">Last seen</th><th></th></tr>${rows.map(([who, c]) => {
      const hps = Object.values(c.honeypots || {});
      const back = hps.length > 1 || c.visits > 1;
      return `<tr data-k="cr|${esc(who)}"><td><span class="who">${esc(who)}</span>${back ? ` <span class="flag bad">came back</span>` : ""}
          ${c.name ? `<div class="hp-name">${esc(c.name)}</div>` : ""}${c.identity ? `<div class="muted small mono">identity ${esc(c.identity)}</div>` : ""}</td>
        <td>${hps.map((h) => `<div>${esc(h.name)} <span class="muted small">${num(h.visits)} ${h.visits === 1 ? "visit" : "visits"}, last ${when(h.last)}</span>${took(h) ? `<div class="small maze-text">took ${took(h)}</div>` : ""}</div>`).join("")}</td>
        <td class="n">${num(c.visits)}</td><td class="n">${num(c.caught)}</td><td class="n">${whenCell(c.first)}</td><td class="n">${whenCell(c.last)}</td>
        <td class="n">${blocked[who] ? bhBtn(who, c.identity) : blockBtn(who, c.name, "", c.identity)} <button class="btn small ghost" data-forget="${esc(who)}" title="Remove it from this list (not from the blocklist)">Forget</button></td></tr>`;
    }).join("")}</table></div>`
    : hidden ? `<div class="empty">List cleared: ${num(hidden)} known ${hidden === 1 ? "crawler" : "crawlers"} kept in the file, still caught. One that comes back shows here again with all its data.</div>`
    : `<div class="empty">No crawler caught yet. Identified crawlers the honeypots catch are listed here, and kept.</div>`;
}

document.addEventListener("click", async (e) => {
  const f = e.target.closest("[data-forget]");
  if (!f) return;
  if (!(await ask("Forget crawler", `<p>LXMF <span class="mono">${esc(f.dataset.forget)}</span> leaves the known crawlers and the honeypots stop treating it as caught. The blocklist and the blackhole stay as they are; if it is caught again it comes back here.</p>`, "Forget", true))) return;
  await api("/api/honeypot/forget", { who: f.dataset.forget }).then(() => toast("Forgotten"), (err) => toast("Error: " + err.message));
  loadCrawlers();
});

// ---- honeypot stats: one block like Stats, all the honeypots added up or one of them ----

let hpStatsNode = "all", hpDays = 0, hpChartMode = "requests", hpLast = null, hpPeers = [];

// caught peers, each LXMF address once on all the honeypots (a link counts alone when it did not identify)
const caughtPeers = (peers) => new Set(peers.filter((p) => p.caught && p.caught !== "blocked by hand")
  .map((p) => p.who || `${p.node}|${p.link}`)).size;

function addUp(into, from) {
  for (const [k, v] of Object.entries(from || {})) {
    if (typeof v === "number") into[k] = (into[k] || 0) + v;
    else if (v && typeof v === "object") addUp(into[k] || (into[k] = {}), v);
  }
  return into;
}

async function loadHpStats(nodes) {
  const data = await fetchHpStats(nodes);
  const anchor = hpAnchor();
  renderHpStats(nodes, data);
  hpRestore(anchor);
}

async function fetchHpStats(nodes) {
  if (!nodes.some((n) => n.id === hpStatsNode)) hpStatsNode = "all";
  const st = { totals: {}, days: {}, hours: {}, minutes: {} }, s = {};
  for (const n of nodes.filter((x) => hpStatsNode === "all" || x.id === hpStatsNode)) {
    try {
      const d = await api(`/api/stats?node=${encodeURIComponent(n.id)}`);
      addUp(st.totals, d.stats.totals);
      addUp(st.days, d.stats.days);
      addUp(st.hours, d.stats.hours);
      addUp(st.minutes, d.stats.minutes);
      addUp(s, d.node.stats);
    } catch (e) { /* login */ }
  }
  return { st, s };
}

function renderHpStats(nodes, { st, s }) {
  const sel = $("#hp-stats-node");
  sel.innerHTML = `<option value="all">All honeypots (${nodes.length})</option>`
    + nodes.map((n) => `<option value="${esc(n.id)}">${esc(n.name)}</option>`).join("");
  sel.value = hpStatsNode;
  const caught = caughtPeers(hpPeers.filter((p) => hpStatsNode === "all" || p.node === hpStatsNode));
  const t = st.totals, today = st.days[todayKey()] || {};
  $("#hp-req").innerHTML = cells([
    [num((t.maze || 0) - (t.maze_files || 0)), "fake maze pages", "amber"],
    [num(t.maze_files), "fake maze files"],
    [num((today.maze_page || 0) + (today.maze_file || 0)), "maze today"],
    [num(s.views), "bait page views"],
    [num(s.files), "bait downloads"],
    [num(t.connects), "connections"],
    [num(t.crawler_links), "crawler-like links"],
    [num(caught), "caught"],
  ]);
  $("#hp-traffic").innerHTML = cells([
    [size(t.maze_bytes), "maze sent", "amber"],
    [size(today.maze_bytes), "maze sent today"],
    [size(t.bytes), "bait content sent"],
    [size(t.link_tx), "link traffic out"],
    [size(t.link_rx), "link traffic in"],
    [size(t.connects ? (t.link_tx + t.link_rx) / t.connects : 0), "per connection"],
  ]);
  hpLast = st;
  hpChart();
}

const hpChart = () => hpLast && chart(hpLast, { el: "#hp-chart", legend: "#hp-legend", mode: hpChartMode, days: hpDays, honeypot: true });

$("#hp-stats-node").addEventListener("change", (e) => { hpStatsNode = e.target.value; loadHpStats(hpNodes()); });
$$("#hp-range button").forEach((b) => b.addEventListener("click", () => {
  hpDays = Number(b.dataset.days);
  $$("#hp-range button").forEach((x) => x.classList.toggle("active", x === b));
  hpChart();
}));
$$("#hp-chart-mode button").forEach((b) => b.addEventListener("click", () => {
  hpChartMode = b.dataset.mode;
  $$("#hp-chart-mode button").forEach((x) => x.classList.toggle("active", x === b));
  hpChart();
}));
window.addEventListener("resize", () => { if (tab === "honeypot") hpChart(); });

async function loadHoneypot() {
  if (!overview) await loadOverview();
  const nodes = hpNodes();
  const autoBlocked = Object.values(blocked).filter((b) => String(b.via || "").startsWith("auto")).length;
  let peers = [];
  try {
    peers = (await api("/api/honeypot/peers?limit=5000")).items || [];
  } catch (e) { /* login */ }
  hpPeers = peers;
  const statsData = await fetchHpStats(nodes);
  const crawlerItems = await fetchCrawlers();
  const anchor = hpAnchor();
  const caught = caughtPeers(peers);
  const identified = new Set(peers.filter((p) => p.who).map((p) => p.who)).size;
  const mazed = peers.filter((p) => p.maze);
  const mazeTime = mazed.reduce((a, p) => a + (p.maze_last - p.maze_first), 0);
  $("#hp-strip").innerHTML = cells([
    [`${nodes.filter((n) => n.state === "running").length}/${nodes.length}`, "honeypots running"],
    [num(peers.filter((p) => p.until === null).length), "connected now"],
    [num(peers.length), "connections recorded"],
    [num(identified), "identified peers"],
    [num(caught), "caught", "amber"],
    [num(autoBlocked), "blocked by honeypots"],
    [num(mazed.reduce((a, p) => a + p.maze - (p.maze_files || 0), 0)), "fake maze pages served"],
    [num(mazed.reduce((a, p) => a + (p.maze_files || 0), 0)), "fake maze files served"],
    [mazed.length ? span(mazeTime) : "-", "crawler time lost in the maze"],
  ]);
  $("#hp-nodes").innerHTML = nodes.length ? nodes.map(nodeCard).join("")
    : `<div class="panel empty">No honeypot yet. Create one: it starts stopped, press Start to put the bait online.</div>`;
  renderHpStats(nodes, statsData);
  renderCrawlers(crawlerItems);
  const now = Date.now() / 1000;
  // one row per identified peer on all the honeypots (crawlers open a new link for every page), one per link otherwise
  const groups = new Map();
  for (const p of peers) {
    const key = p.who ? `who|${p.who}` : `${p.node}|link ${p.link}|${p.since}`;
    const g = groups.get(key) || { ...p, key, links: 0, requests: 0, tries: 0, first: p.since, last: 0, online: false, pages: [], tried: [], trap: [], files: [], caught: "", mode: "",
                                   maze: 0, maze_files: 0, maze_bytes: 0, maze_time: 0, maze_last: 0, maze_wait: 0, maze_got: [], hps: [] };
    if (!g.hps.includes(p.node_name)) g.hps.push(p.node_name);
    if (p.via && !g.via) Object.assign(g, { via: p.via, via_path: p.via_path });
    if (p.hops) g.hops = p.hops;
    g.addrs ||= [];
    if (p.addr && !g.addrs.includes(p.addr)) g.addrs.push(p.addr);
    for (const x of p.maze_got || []) if (g.maze_got.length < HP_PAGES) g.maze_got.push(x);
    g.links += 1;
    g.requests += p.requests || 0;
    g.tries += p.tries || 0;
    if (p.maze) {
      g.maze += p.maze;
      g.maze_files += p.maze_files || 0;
      g.maze_bytes += p.maze_bytes || 0;
      g.maze_wait += p.maze_wait || 0;
      g.maze_time += p.maze_last - p.maze_first;
      g.maze_last = Math.max(g.maze_last, p.maze_last);
    }
    g.first = Math.min(g.first, p.since);
    g.last = Math.max(g.last, p.until === null ? now : p.until);
    g.online = g.online || p.until === null;
    for (const x of p.pages || []) if (!g.pages.includes(x) && g.pages.length < HP_PAGES) g.pages.push(x);
    for (const x of p.tried || []) if (!g.tried.includes(x) && g.tried.length < 14) g.tried.push(x);
    for (const x of p.trap || []) if (!g.trap.includes(x) && g.trap.length < 14) g.trap.push(x);
    for (const x of p.files || []) if (!g.files.includes(x) && g.files.length < 14) g.files.push(x);
    // the first catch tells why: "caught before" only when nothing else is known
    if (p.caught && (!g.caught || g.caught === "caught before")) { g.caught = p.caught; g.mode = p.mode; }
    g.identity = g.identity || p.identity;
    groups.set(key, g);
  }
  // by arrival only: a row never changes place when its peer does something
  const dir = hpOrder === "oldest" ? 1 : -1;
  const all = [...groups.values()].sort((a, b) => dir * (a.first - b.first) || (a.key < b.key ? -1 : 1));
  for (const p of all) {
    const b = p.who && blocked[p.who];
    p.state = p.caught && p.caught !== "blocked by hand" ? "caught" : b || p.mode ? "blocked" : p.online ? "online" : "browsed";
  }
  hpAll = all;
  $("#hp-paused").hidden = !hpPaused();
  if (hpPaused()) {
    renderHpForm();
    return hpRestore(anchor);
  }
  const counts = { all: all.length, bad: all.filter((p) => p.state === "caught" || p.state === "blocked").length,
                   online: all.filter((p) => p.online).length, id: all.filter((p) => p.who).length, anon: all.filter((p) => !p.who).length };
  $("#hp-filter").innerHTML = [["all", "All"], ["bad", "Caught or blocked"], ["online", "Online"], ["id", "Identified"], ["anon", "Not identified"]]
    .map(([k, l]) => `<button data-hpf="${k}" class="${hpFilter === k ? "active" : ""}">${l} <span class="muted">${counts[k]}</span></button>`).join("");
  $("#hp-order").innerHTML = [["newest", "Newest first"], ["oldest", "Oldest first"]]
    .map(([k, l]) => `<button data-hpo="${k}" class="${hpOrder === k ? "active" : ""}">${l}</button>`).join("");
  const rows = all.filter(hpShown);
  const tip = (cls, x) => cls === "maze" ? ` title="${/^file /.test(x) ? "Fake file from the maze" : "Fake page from the maze"}"` : "";
  const chips = (list, cls) => list.map((x) => `<span class="chip ${cls}"${tip(cls, x)}>${esc(x.replace(/^\/(page|file)\//, ""))}</span>`).join("");
  const many = nodes.length > 1;
  $("#hp-peers").innerHTML = rows.length ? `<div class="hp-row hp-head"><div>Status</div><div>Peer and what it did</div><div class="hp-nums">Requests</div><div></div></div>` + rows.map((p) => {
    const b = p.who && blocked[p.who];
    const why = p.caught ? `${p.caught}${p.mode ? " · " + p.mode : ""}` : b ? `${b.reason || "blocked"} · ${blockMode(b, p.node)}` : "";
    const actions = p.who ? (b ? `${!b.scope || b.scope === "all" ? "" : widenBtn(p.who)} <button class="btn-unblock" data-unblock="${esc(p.who)}">Unblock</button> ${bhBtn(p.who, p.identity)}`
      : `<button class="btn-block" data-block="${esc(p.who)}" data-name="${esc(p.name || "")}" data-node="${esc(p.node)}">Block</button> ${bhBtn(p.who, p.identity)}`) : linkBtns(p);
    const meta = [p.addrs && p.addrs.length ? `on ${p.addrs.map((a) => "N" + a).join(", ")}` : "", viaText(p), p.identity ? `identity ${esc(p.identity)}` : "", many ? `on ${esc(p.hps.join(", "))}` : "",
                  b && b.scope && b.scope !== "all" ? `blocked on ${esc(scopeText(b.scope))}` : ""].filter(Boolean).join(" · ");
    return `<div class="hp-row" data-k="${esc(p.key)}">
      <div class="hp-state"><span class="badge ${p.state}">${p.state}</span>
        <div class="muted small">arrived ${stamp(p.first)}</div>
        <div class="muted small">${p.online ? "online now" : `left ${stamp(p.last)}`}</div>
        <div class="muted small">${ago(p.online ? p.first : p.last)}</div></div>
      <div class="hp-who">
        <div>${p.who ? `<span class="who">${esc(p.who)}</span>${p.name ? ` <span class="hp-name">${esc(p.name)}</span>` : ""}` : `<span class="muted">Not identified</span> <span class="who muted">link ${esc(p.link)}</span>`}</div>
        <div class="muted small">${meta}</div>
        ${why ? `<div class="small bad-text">${esc(why)}</div>` : ""}
        ${p.maze ? `<div class="small maze-text">In the maze: ${mazeWhat(p)} in ${span(p.maze_time)}, ${size(p.maze_bytes)}${p.maze_wait ? ` · held ${span(p.maze_wait)} by the slow maze` : ""} · last ${p.online && now - p.maze_last < 90 ? "now" : when(p.maze_last)}</div>` : ""}
        ${p.pages.length || p.tried.length || p.trap.length || p.files.length || p.maze_got.length ? `<details class="hp-pages" data-k="${esc(p.who || p.link)}"${hpOpen.has(p.who || p.link) ? " open" : ""}><summary>${p.pages.length}${p.pages.length >= HP_PAGES ? "+" : ""} ${p.pages.length === 1 ? "page" : "pages"}${p.trap.length ? `, ${p.trap.length} ${p.trap.length === 1 ? "trap" : "traps"}` : ""}${p.files.length ? `, ${p.files.length} ${p.files.length === 1 ? "file" : "files"} downloaded` : ""}${p.tried.length ? `, ${p.tried.length} blocked` : ""}${p.maze_got.length ? `, ${p.maze_got.length}${p.maze_got.length >= HP_PAGES ? "+" : ""} in the maze` : ""}${!p.pages.length && p.tried.length && p.state === "blocked" ? ` <span class="bad-text">(Already in blocklist: request blocked!)</span>` : ""}</summary>${chips(p.pages, "")}${chips(p.trap, "trap")}${chips(p.files, "file")}${chips(p.tried, "bad")}${chips(p.maze_got, "maze")}</details>` : ""}
      </div>
      <div class="hp-nums"><b>${num(p.requests)}</b> ${p.requests === 1 ? "request" : "requests"}${p.tries ? `<div class="bad-text small">+${num(p.tries)} blocked</div>` : ""}<div class="muted small">${num(p.links)} ${p.links === 1 ? "link" : "links"}</div></div>
      <div class="hp-act">${actions}</div>
    </div>`;
  }).join("") : `<div class="empty">${all.length ? "Nothing with this filter." : "Nobody has reached a honeypot yet."}</div>`;
  renderHpForm();
  hpRestore(anchor);
}

// the row at the top of the screen stays where it is when rows are added or grow above it
// the first thing on screen in the Honeypot tab (a peer or crawler row, else a fixed heading),
// put back at the same height after a redraw: new rows above never move what is being read
const HP_ANCHORS = "#tab-honeypot [data-k], #tab-honeypot h2, #tab-honeypot .kgroup-title, #tab-honeypot .panel-head";
function hpAnchor() {
  if (tab !== "honeypot" || window.scrollY < 1) return null;
  for (const el of document.querySelectorAll(HP_ANCHORS)) {
    const top = el.getBoundingClientRect().top;
    if (top >= 0) return { k: el.dataset.k, el, top };
  }
  return null;
}
function hpRestore(a) {
  if (!a) return;
  const el = a.k ? [...document.querySelectorAll("#tab-honeypot [data-k]")].find((x) => x.dataset.k === a.k) : (a.el.isConnected ? a.el : null);
  if (el) window.scrollBy(0, el.getBoundingClientRect().top - a.top);
}

function mazeWhat(p) {
  const files = p.maze_files || 0, pages = p.maze - files;
  return [pages ? `${num(pages)} fake ${pages === 1 ? "page" : "pages"}` : "", files ? `${num(files)} fake ${files === 1 ? "file" : "files"}` : ""].filter(Boolean).join(", ");
}

// a link can be blocked only while it is connected: once closed, it comes back with a new id
function isMaze(mode) { return /^maze/.test(mode || ""); }
function releaseBtn(link, node) { return `<button class="btn-unblock" data-unblocklink="${esc(link)}" data-node="${esc(node)}" data-maze="1" title="It keeps getting answers either way: this takes it out of the maze, back to the real pages">Release from maze</button>`; }

function linkBtns(p) {
  if (!p.online) return `<span class="muted small" title="This connection is closed: it cannot be blocked any more. If the peer comes back it gets a new link id.">disconnected</span>`;
  if (isMaze(p.mode)) return releaseBtn(p.link, p.node);
  if (p.mode) return `<button class="btn-unblock" data-unblocklink="${esc(p.link)}" data-node="${esc(p.node)}">Unblock link</button>`;
  return `<button class="btn-block" data-blocklink="${esc(p.link)}" data-node="${esc(p.node)}" title="Block this connection until it disconnects">Block link</button>`;
}

function renderHpForm() {
  const n = hpNodes().find((x) => x.id === $("#hp-node").value);
  const dot = $("#hp-dot");
  if (n) dot.setAttribute("style", `--h:${hue(n.address)}`); else dot.removeAttribute("style");
  // keep what is being typed: redraw only when another honeypot is chosen
  if (renderHpForm.shown === (n && n.id) && $("#hp-form").children.length) return;
  renderHpForm.shown = n && n.id;
  $("#hp-form").innerHTML = n ? formHtml(HP_FIELDS, n.settings, "hp") : `<div class="empty">No honeypot nodes.</div>`;
}
$("#hp-node").addEventListener("change", renderHpForm);

$("#hp-save").addEventListener("click", async () => {
  const id = $("#hp-node").value;
  if (!id) return;
  try {
    const vals = formValues(HP_FIELDS, "hp");
    await api(`/api/node/${encodeURIComponent(id)}/settings`, vals);
    $("#hp-msg").textContent = "Saved: traps and blocking apply at once.";
    const node = (overview?.nodes || []).find((n) => n.id === id);
    if (vals.honeypot?.addresses && node?.state === "running"
        && await ask("Restart honeypot", `<p>The number of addresses changes when <b>${esc(node.name)}</b> restarts: visitors connected now are disconnected.</p>`, "Restart now")) {
      await api(`/api/node/${encodeURIComponent(id)}/restart`, {});
      $("#hp-msg").textContent = "Saved and restarted.";
    }
    renderHpForm.shown = null;
    await loadOverview();
    loadHoneypot();
  } catch (err) {
    $("#hp-msg").textContent = "Error: " + err.message;
  }
});

$("#hp-clear").addEventListener("click", async () => {
  if (!(await ask("Clear history", "<p>The peer connections history of every honeypot is emptied. Blocks stay in the blocklist (Settings), counters and live connections stay.</p>", "Clear history", true))) return;
  await api("/api/honeypot/clear", {}).then(() => toast("History cleared"), (e) => toast("Error: " + e.message));
  loadHoneypot();
});

$("#hp-reset").addEventListener("click", async () => {
  const id = $("#hp-node").value;
  if (!id || !(await ask("Reset counters", `<p>All counters of honeypot <b>${esc(id)}</b> go back to zero. Its peer history and the blocklist stay.</p>`, "Reset counters", true))) return;
  await api(`/api/node/${encodeURIComponent(id)}/reset_stats`, {}).then(() => toast("Counters reset"), (e) => toast("Error: " + e.message));
  await loadOverview();
  loadHoneypot();
});

$("#hp-delete").addEventListener("click", async () => {
  const id = $("#hp-node").value;
  if (!id || !(await ask("Remove honeypot", `<p>Honeypot <b>${esc(id)}</b> stops and its folder, identity included, moves to Removed nodes in Settings, where you can restore or delete it. The peers it blocked stay in the blocklist.</p>`, "Remove honeypot", true))) return;
  try {
    await api(`/api/node/${encodeURIComponent(id)}/delete`, {});
    toast("Honeypot moved to Removed nodes");
    fillNodeSelects.sig = null;
    renderHpForm.shown = null;
    await loadOverview();
    loadHoneypot();
  } catch (err) {
    toast("Error: " + err.message);
  }
});

$("#bl-export").addEventListener("click", async () => {
  try {
    toast(`Saved ${await download("/api/blocklist.txt")}`);
  } catch (err) {
    toast("Error: " + err.message);
  }
});

// ---- honeypot lists as text ----

const fullTime = (t) => { const d = new Date(t * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
const fileStamp = () => fullTime(Date.now() / 1000).slice(0, 16).replace(/[-: ]/g, "");

function saveText(name, lines) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], { type: "text/plain;charset=utf-8" }));
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  toast(`Saved ${name}`);
}

const shortPath = (x) => x.replace(/^\/(page|file)\//, "");

function peersText() {
  const rows = hpAll.filter(hpShown);
  const filter = { all: "all", bad: "caught or blocked", online: "online", id: "identified", anon: "not identified" }[hpFilter];
  const out = [`# nomad-server honeypot peer connections, ${fullTime(Date.now() / 1000)}`,
               `# ${rows.length} ${rows.length === 1 ? "peer" : "peers"}, filter: ${filter}, ${hpOrder === "oldest" ? "oldest" : "newest"} first`];
  for (const p of rows) {
    const b = p.who && blocked[p.who];
    const who = p.who ? `LXMF ${p.who}${p.name ? ` (${p.name})` : ""}` : `not identified, link ${p.link}`;
    out.push("", `${p.state.toUpperCase()}  ${who}`);
    if (p.identity) out.push(`  identity  ${p.identity}`);
    out.push(`  on        ${p.hps.join(", ")}`,
             `  arrived   ${fullTime(p.first)}, ${p.online ? "online now" : `left ${fullTime(p.last)}`}`,
             `  activity  ${num(p.requests)} ${p.requests === 1 ? "request" : "requests"}${p.tries ? `, +${num(p.tries)} blocked` : ""}, ${num(p.links)} ${p.links === 1 ? "link" : "links"}`);
    const why = p.caught ? `${p.caught}${p.mode ? " · " + p.mode : ""}` : b ? `${b.reason || "blocked"} · ${blockMode(b, p.node)}` : "";
    if (why) out.push(`  why       ${why}`);
    if (p.maze) out.push(`  maze      ${mazeWhat(p)} in ${span(p.maze_time)}, ${size(p.maze_bytes)}${p.maze_wait ? `, held ${span(p.maze_wait)} by the slow maze` : ""}, last ${fullTime(p.maze_last)}`);
    for (const [label, list] of [["pages", p.pages], ["traps", p.trap], ["files", p.files], ["refused", p.tried], ["maze got", p.maze_got]]) {
      if (list.length) out.push(`  ${label.padEnd(9)} ${list.map(shortPath).join(", ")}`);
    }
  }
  return out;
}

// "42 requests, 31.2 KB sent (18 maze)": what a crawler took from one honeypot
function took(h) {
  if (!h.sent && !h.requests) return "";   // not counted yet, or nothing kept of it
  return `${num(h.requests || 0)} ${h.requests === 1 ? "request" : "requests"}, ${size(h.sent)} sent${h.maze ? ` (${num(h.maze)} maze)` : ""}`;
}

function crawlersText() {
  const out = [`# nomad-server known crawlers, ${fullTime(Date.now() / 1000)}`,
               `# ${crawlerRows.length} ${crawlerRows.length === 1 ? "crawler" : "crawlers"}, last seen first`];
  for (const [who, c] of crawlerRows) {
    const hps = Object.values(c.honeypots || {});
    const flags = [hps.length > 1 || c.visits > 1 ? "came back" : "", blocked[who] ? "in the blocklist" : ""].filter(Boolean);
    out.push("", `LXMF ${who}${c.name ? ` (${c.name})` : ""}${flags.length ? `  [${flags.join(", ")}]` : ""}`);
    if (c.identity) out.push(`  identity    ${c.identity}`);
    out.push(`  visits      ${num(c.visits)}, caught ${num(c.caught)}`,
             `  first seen  ${fullTime(c.first)}`, `  last seen   ${fullTime(c.last)}`);
    for (const h of hps) out.push(`  honeypot    ${h.name}: ${num(h.visits)} ${h.visits === 1 ? "visit" : "visits"}${took(h) ? ", " + took(h) : ""}, last ${fullTime(h.last)}`);
  }
  return out;
}

$("#hp-export").addEventListener("click", () => saveText(`nomad-server-honeypot-peers-${fileStamp()}.txt`, peersText()));
$("#cr-hide").addEventListener("click", async () => {
  if (!(await ask("Clear list", "<p>The known crawlers leave this list. <b>Nothing is deleted</b>: they stay in the file and stay caught (the maze goes on), and a crawler that comes back shows here again with all its data, old and new.</p>", "Clear list"))) return;
  try {
    const r = await api("/api/honeypot/crawlers/hide", {});
    toast(`${num(r.hidden)} hidden, kept in the file`);
  } catch (err) {
    toast("Error: " + err.message);
  }
  loadCrawlers();
});
$("#cr-export").addEventListener("click", () => saveText(`nomad-server-known-crawlers-${fileStamp()}.txt`, crawlersText()));

// ---- backup and identity ----

async function download(url) {
  const r = await fetch(url, { headers: { "X-Requested-With": "nomad-server" } });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  const name = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "")?.[1] || "download";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await r.blob());
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  return name;
}

async function postFile(url, file) {
  const r = await fetch(url, { method: "POST", headers: { "X-Requested-With": "nomad-server", "Content-Type": "application/octet-stream" }, body: file });
  const j = await r.json().catch(() => ({}));
  if (r.status === 401) showLogin();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

$("#node-backup").addEventListener("click", async () => {
  const id = $("#set-node").value;
  if (!id) return;
  try {
    toast(`Saved ${await download(`/api/node/${encodeURIComponent(id)}/backup`)}`);
  } catch (err) {
    toast("Error: " + err.message);
  }
});

$("#id-export").addEventListener("click", async () => {
  const id = $("#set-node").value;
  if (!id || !(await ask("Export identity", "<p>The file is the node's private key. Whoever has it can run a node with this address: keep it private.</p>", "Export"))) return;
  try {
    toast(`Saved ${await download(`/api/node/${encodeURIComponent(id)}/identity`)}`);
  } catch (err) {
    toast("Error: " + err.message);
  }
});

$("#id-import").addEventListener("click", () => $("#id-file").click());
$("#id-file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  const id = $("#set-node").value;
  if (!f || !id) return;
  if (!(await ask("Import identity", `<p>Node <b>${esc(id)}</b> takes the identity in <b>${esc(f.name)}</b> and its address changes to that identity's. The current identity moves to the node's <span class="mono">.trash</span> folder. The node restarts.</p>`, "Replace identity", true))) return;
  try {
    const r = await postFile(`/api/node/${encodeURIComponent(id)}/identity`, f);
    toast(`New address ${r.address}`);
    await loadOverview();
    renderNodeForm();
  } catch (err) {
    toast("Error: " + err.message);
  }
});

$("#id-new").addEventListener("click", async () => {
  const id = $("#set-node").value;
  if (!id) return;
  if (!(await ask("New identity", `<p>Node <b>${esc(id)}</b> gets a brand new identity, so a <b>new address</b>: visitors and links that use the old one will not find it any more. The current identity moves to the node's <span class="mono">.trash</span> folder, so you can put it back with Import identity. The node restarts.</p>`, "Create new identity", true))) return;
  try {
    const r = await api(`/api/node/${encodeURIComponent(id)}/new_identity`, {});
    toast(`New address ${r.address}`);
    await loadOverview();
    renderNodeForm();
  } catch (err) {
    toast("Error: " + err.message);
  }
});

$("#restore-btn").addEventListener("click", () => $("#restore-file").click());
$("#restore-file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  if (!(await ask("Restore from nS backup", `<p>A new node is created from <b>${esc(f.name)}</b>, a backup made by nomad-server (Backup node), with the identity, address, pages and counters in it. It is refused if another node already uses that identity.</p>`, "Restore node"))) return;
  try {
    const r = await postFile("/api/nodes/restore", f);
    toast(`Node ${r.id} restored, address ${r.address}`);
    loadOverview();
  } catch (err) {
    toast("Not restored: " + err.message);
  }
});

// where a link came from: hops always, the interface when Reticulum tells it
function viaText(v) {
  if (!v.hops && !v.via) return "";
  const hops = v.hops ? `${v.hops} ${v.hops === 1 ? "hop" : "hops"}` : "";
  const via = v.via ? `<span class="mono" title="${v.via_path ? "Interface rnsd uses to reach this peer: where it came from" : "Interface the link came in on"}">${esc(v.via)}</span>` : "";
  return [hops, via].filter(Boolean).join(" via ");
}

// ---- import a node from another server ----

const imp = { step: "choose", items: [], pick: null, dir: null, busy: false };
const FOLDER_ICON = '<svg class="fi" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 5.5a1 1 0 0 1 1-1h4l1.8 2h7.2a1 1 0 0 1 1 1v7.5a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1z"/></svg>';
const KIND = { nomadnet: "NomadNet", "nomad-server": "nomad-server", server: "page server" };

function impFound(i) {
  const what = `${i.mu} page${i.mu === 1 ? "" : "s"} (.mu)${i.pages > i.mu ? `, ${i.pages - i.mu} other in pages/` : ""}, ${i.files} file${i.files === 1 ? "" : "s"}, ${size(i.size)}`;
  const addr = i.used_by ? `<span class="flag bad">already here as node ${esc(i.used_by)}</span>`
    : i.address ? `address <span class="mono">${esc(i.address)}</span>`
    : `<span class="flag">no identity file: it gets a new address</span>`;
  return `<b>${esc(i.name)}</b> <span class="muted small">${KIND[i.kind] || i.kind}${i.index ? "" : " · no index.mu"}</span>
    <div class="mono small imp-path">${esc(i.root)}</div><div class="small">${what}</div><div class="small">${addr}</div>`;
}

function impPickHtml() {
  const i = imp.pick;
  if (!i) return "";
  return `<div class="form imp-pick"><div class="field"><label for="imp-name">Name of the new node</label>
    <input id="imp-name" type="text" maxlength="60" value="${esc(i.name)}"></div></div>
    <p class="muted small">Identity, pages, files${i.visits ? " and old counters" : ""} are copied into a new node, which stays <b>stopped</b>. The old folder is not touched. Stop the old server before pressing Start: one address must never be served from two places.</p>`;
}

function impRender() {
  const b = $("#imp-body");
  $("#imp-back").classList.toggle("hidden", imp.step === "choose");
  const can = imp.pick && imp.pick.mu && !imp.pick.used_by && !imp.busy;
  $("#imp-go").classList.toggle("hidden", imp.step === "choose");
  $("#imp-go").disabled = !can;
  if (imp.step === "choose") {
    b.innerHTML = `<p class="muted small">Bring here a node served by NomadNet or another server: its identity (so its address), pages and files.</p>
      <div class="imp-opts">
        <button class="imp-opt" data-imp="scan"><b>Autoscan import</b><span class="muted small">Searches this computer for old nodes: folders with an identity and pages/ with .mu pages (NomadNet, rns_server_page and similar).</span></button>
        <button class="imp-opt" data-imp="manual"><b>Manual import</b><span class="muted small">Choose the folder of the old server yourself.</span></button>
      </div>`;
  } else if (imp.step === "scan") {
    if (imp.busy) {
      b.innerHTML = `<p class="muted">Scanning this computer… (up to 30 seconds)</p>`;
      return;
    }
    const res = imp.scan || { items: [] };
    const list = res.items.map((i, k) => `<label class="imp-item${i.used_by ? " off" : ""}"><input type="radio" name="imp-r" value="${k}"${imp.pick === i ? " checked" : ""}${i.used_by ? " disabled" : ""}><span>${impFound(i)}</span></label>`).join("");
    b.innerHTML = `<p class="muted small">${res.items.length ? `${res.items.length} node${res.items.length === 1 ? "" : "s"} found` : "No old nodes found"} in ${res.seconds} s, ${res.dirs} folders${res.complete ? "" : ". The scan stopped at the time limit: if yours is missing, use Manual import"}.</p>
      <div class="imp-list">${list}</div>${impPickHtml()}`;
  } else {
    const d = imp.dir;
    if (!d) {
      b.innerHTML = `<p class="muted">Loading…</p>`;
      return;
    }
    const f = d.found;
    const state = !d.path ? "" : f && f.mu ? `<div class="imp-found ok">${impFound(f)}</div>`
      : f ? `<div class="imp-found">Nothing to import here: pages/ has no .mu pages.</div>`
      : `<div class="imp-found">No node here: open the folder that holds <span class="mono">pages/</span> (and usually <span class="mono">identity</span>). For NomadNet it is <span class="mono">.nomadnetwork</span> in your home folder.</div>`;
    const busy = imp.picking || imp.sending;
    const host = `<b>${esc(d.host || "the server")}</b>`;
    const where = d.path ? `, starting from <span class="mono">${esc(d.path)}</span>` : "";
    const note = imp.sending ? esc(imp.sending)
      : imp.picking ? "Choose the folder in the window that opened…"
      : d.can_pick ? `<b>Browse folders on this computer</b> opens the folder window of this computer (${host}, where nomad-server runs)${where}.`
      : `<b>Browse folders on your device</b> opens the folder window of the device you are using now, not of ${host}. The folder you choose is sent to the server: only identity, settings, pages and files.`;
    const crumbs = (d.crumbs || []).map((c, k, a) => k === a.length - 1 ? `<span class="imp-crumb cur">${esc(c.name)}</span>`
      : `<button class="imp-crumb" data-dir="${esc(c.path)}" title="${esc(c.path)}">${esc(c.name)}</button>`).join(`<span class="imp-sep">›</span>`);
    const places = (d.places || []).map((p) => `<button class="imp-place${p.path === d.path ? " cur" : ""}" data-dir="${esc(p.path)}" title="${esc(p.path)}">${esc(p.name)}</button>`).join("");
    const rows = d.dirs.map((x) => `<button class="imp-dir${x.node ? " node" : ""}" data-dir="${esc(x.path)}" title="${x.node ? "Holds a node: open it to see what is inside" : esc(x.path)}">${FOLDER_ICON}<span class="nm">${esc(x.name)}</span>${x.node ? `<span class="flag">node</span>` : ""}</button>`).join("");
    b.innerHTML = `<p class="muted small imp-lead">Open the folder of the old node: the folders below are on the server ${host}.</p>
      <div class="imp-ex">
        <div class="imp-tool">
          <button class="icon-btn" data-imp="up" title="Up to the parent folder" aria-label="Up"${d.parent == null ? " disabled" : ""}><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 15V5M5 10l5-5 5 5"/></svg></button>
          <div class="imp-crumbs">${crumbs || `<span class="imp-crumb cur">Drives</span>`}</div>
          <button class="btn small imp-pickbtn" data-imp="${d.can_pick ? "pick" : "upload"}" title="${d.can_pick ? "Opens the folder window of this computer" : "Opens the folder window of the device you are using, not of the server"}"${busy ? " disabled" : ""}>${d.can_pick ? "Browse folders on this computer" : "Browse folders on your device"}</button>
        </div>
        <div class="imp-cols">
          <nav class="imp-places" aria-label="Places">${places}</nav>
          <div class="imp-dirs">${rows || `<span class="muted small imp-none">No subfolders</span>`}</div>
        </div>
        <div class="imp-tool"><input id="imp-path" type="text" class="mono" value="${esc(d.path)}" aria-label="Go to folder" placeholder="Type a folder path"><button class="btn small" data-imp="go" title="Go to the folder typed here">Go</button></div>
      </div>
      <p class="muted small imp-note">${note}</p>
      ${state}${f && f.mu ? impPickHtml() : ""}`;
    const cr = $(".imp-crumbs", b);
    if (cr) cr.scrollLeft = cr.scrollWidth;
  }
}

async function impBrowse(path) {
  try {
    imp.dir = await api("/api/import/browse?path=" + encodeURIComponent(path));
    imp.pick = imp.dir.found && imp.dir.found.mu ? imp.dir.found : null;
  } catch (err) {
    toast("Error: " + err.message);
  }
  impRender();
}

$("#import-btn").addEventListener("click", () => {
  Object.assign(imp, { step: "choose", pick: null, dir: null, scan: null, busy: false });
  impRender();
  $("#imp").showModal();
});
$("#imp-cancel").addEventListener("click", () => $("#imp").close());
$("#imp-back").addEventListener("click", () => {
  Object.assign(imp, { step: "choose", pick: null });
  impRender();
});
$("#imp-body").addEventListener("change", (e) => {
  if (e.target.name !== "imp-r") return;
  imp.pick = imp.scan.items[+e.target.value];
  impRender();
  $("#imp-name")?.focus();
});
$("#imp-body").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.id === "imp-path") {
    e.preventDefault();
    impBrowse(e.target.value.trim());
  }
});
$("#imp-body").addEventListener("click", async (e) => {
  const dir = e.target.closest("[data-dir]");
  if (dir) return impBrowse(dir.dataset.dir);
  const a = e.target.closest("[data-imp]")?.dataset.imp;
  if (a === "scan") {
    Object.assign(imp, { step: "scan", busy: true, pick: null });
    impRender();
    try {
      imp.scan = await api("/api/import/scan");
    } catch (err) {
      imp.scan = { items: [], seconds: 0, dirs: 0, complete: true };
      toast("Error: " + err.message);
    }
    imp.busy = false;
    if (imp.step === "scan") impRender();
  } else if (a === "manual") {
    Object.assign(imp, { step: "manual", pick: null, dir: null });
    impRender();
    impBrowse("");
  } else if (a === "upload") {
    $("#imp-folder").click();
  } else if (a === "pick") {
    imp.picking = true;
    impRender();
    try {
      const r = await api("/api/import/pick", { start: imp.dir?.path || "" });
      imp.picking = false;
      if (r.path) return impBrowse(r.path);
    } catch (err) {
      toast("Error: " + err.message);
    }
    imp.picking = false;
    if ($("#imp").open) impRender();
  } else if (a === "up") {
    impBrowse(imp.dir.parent);
  } else if (a === "go") {
    impBrowse($("#imp-path").value.trim());
  }
});
// only what an import uses: never NomadNet's messages, caches and the rest
const IMP_CFG = ["config", "config.cfg", "config.cfg.owr", "node.cfg", "config.ini", "server.cfg", "settings.cfg"];
function impWanted(rel) {
  const p = rel.split("/").slice(1);
  if (p.includes("__pycache__")) return false;
  if (p.length === 1 && IMP_CFG.includes(p[0])) return true;
  const s = p[0] === "storage" ? p.slice(1) : p;
  return (s.length === 1 && (s[0] === "identity" || s[0] === "visits.json"))
    || (s.length > 1 && (s[0] === "pages" || s[0] === "files"))
    || (s.length === 2 && s[0] === "data" && s[1] === "visits.json");
}

$("#imp-folder").addEventListener("change", async (e) => {
  const all = [...e.target.files];
  e.target.value = "";
  const list = all.filter((f) => impWanted(f.webkitRelativePath || f.name));
  if (!list.some((f) => /(^|\/)pages\/.+\.mu$/.test(f.webkitRelativePath.split("/").slice(1).join("/")))) {
    if (all.length) toast("Nothing to import: no pages/ with .mu pages in that folder");
    return;
  }
  const session = [...crypto.getRandomValues(new Uint8Array(8))].map((x) => x.toString(16).padStart(2, "0")).join("");
  let folder = "";
  try {
    for (let k = 0; k < list.length; k++) {
      imp.sending = `Sending ${k + 1} of ${list.length} files…`;
      if (k % 5 === 0) impRender();
      const r = await postFile(`/api/import/upload?session=${session}&path=${encodeURIComponent(list[k].webkitRelativePath)}`, list[k]);
      folder = r.folder;
    }
  } catch (err) {
    toast("Not sent: " + err.message);
  }
  imp.sending = "";
  if (folder) impBrowse(folder);
  else impRender();
});

$("#imp-go").addEventListener("click", async () => {
  const i = imp.pick;
  if (!i) return;
  imp.busy = true;
  $("#imp-go").disabled = true;
  try {
    const r = await api("/api/nodes/import", { path: i.root, name: $("#imp-name")?.value.trim() || i.name });
    $("#imp").close();
    toast(`Node ${r.id} imported, stopped: ${r.pages} pages, ${r.files} files${r.new_identity ? ", new address" : ""}`);
    loadOverview();
  } catch (err) {
    toast("Not imported: " + err.message);
  } finally {
    imp.busy = false;
    if ($("#imp").open) impRender();
  }
});

// ---- page manager ----

const pm = { node: null, items: [], edit: false, cur: null, folder: { root: "pages", dir: "" }, dirty: false };
const FILE_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M4 1.5h5.5L13 5v9.5H4z"/><path d="M9.5 1.5V5H13"/></svg>';
const DIR_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M1.5 4h5l1.5 1.5h6.5v8h-13z"/></svg>';
const IMG_EXT = /\.(webp|png|jpe?g|gif|bmp|tiff)$/i;

async function openPages(id) {
  const n = overview?.nodes.find((x) => x.id === id);
  if (!n) return;
  Object.assign(pm, { node: id, cur: null, folder: { root: "pages", dir: "" }, dirty: false });
  $("#pm-title").textContent = `Pages of ${n.name}`;
  $("#pm-dot").setAttribute("style", `--h:${hue(n.address)}`);
  showFile(null);
  $("#pm").showModal();
  await loadTree();
}

async function loadTree() {
  let d;
  try {
    d = await api(`/api/files?node=${encodeURIComponent(pm.node)}`);
  } catch (err) {
    return toast("Error: " + err.message);
  }
  pm.items = d.items;
  pm.edit = d.edit;
  $("#pm-open").classList.toggle("hidden", !d.can_open);
  $("#pm-note").textContent = d.edit ? "Saved pages are served at once, new ones after the automatic rescan." : "Read only: set a web UI password to edit pages over the network.";
  for (const b of ["#pm-new", "#pm-mkdir", "#pm-upload", "#pm-rename", "#pm-delete", "#pm-save"]) $(b).disabled = !d.edit;
  renderTree();
}

function renderTree() {
  const cur = pm.cur ? `${pm.cur.root}/${pm.cur.path}` : "";
  const fold = `${pm.folder.root}/${pm.folder.dir}`.replace(/\/$/, "");
  let html = "";
  for (const root of ["pages", "files"]) {
    html += `<div class="pm-group">${root}/</div><button class="pm-item dir ${fold === root ? "sel" : ""}" data-root="${root}" data-path="" data-dir="1">${DIR_ICON}${root === "pages" ? "pages (served as /page/...)" : "files (downloads, /file/...)"}</button>`;
    for (const it of pm.items.filter((x) => x.root === root).sort((a, b) => a.path.localeCompare(b.path))) {
      const depth = it.path.split("/").length;
      const key = `${root}/${it.path}`;
      const sel = it.dir ? key === fold : key === cur;
      html += `<button class="pm-item ${it.dir ? "dir" : ""} ${sel ? "sel" : ""}" style="padding-left:${8 + depth * 14}px" data-root="${root}" data-path="${esc(it.path)}" ${it.dir ? 'data-dir="1"' : ""} title="${esc(it.path)}">${it.dir ? DIR_ICON : FILE_ICON}${esc(it.path.split("/").pop())}${it.dir ? "" : `<span class="sz">${size(it.size)}</span>`}</button>`;
    }
  }
  $("#pm-tree").innerHTML = html;
  $("#pm-where").textContent = `New pages, folders and uploads go in ${fold}/`;
}

$("#pm-tree").addEventListener("click", async (e) => {
  const b = e.target.closest(".pm-item");
  if (!b) return;
  const root = b.dataset.root, path = b.dataset.path;
  if (b.dataset.dir) {
    pm.folder = { root, dir: path };
    return renderTree();
  }
  if (pm.dirty && !(await ask("Unsaved changes", `<p>Leave <b>${esc(pm.cur.path)}</b> without saving?</p>`, "Leave", true))) return;
  try {
    const f = await api(`/api/file?node=${encodeURIComponent(pm.node)}&root=${root}&path=${encodeURIComponent(path)}`);
    pm.folder = { root, dir: path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "" };
    showFile(f);
  } catch (err) {
    toast("Error: " + err.message);
  }
});

function linkFor(root, path) {
  if (root === "files") return `\`[${path.split("/").pop()}\`:/file/${path}]`;
  if (IMG_EXT.test(path)) return `\`(${path.split("/").pop()}\`:/media/${path})`;
  return `\`[${path.split("/").pop()}\`:/page/${path}]`;
}

function showFile(f) {
  pm.cur = f;
  pm.dirty = false;
  const ta = $("#pm-text"), info = $("#pm-info");
  $("#pm-path").textContent = f ? `${f.root}/${f.path}` : "";
  $("#pm-state").textContent = f ? `${size(f.size)}` : "";
  for (const b of ["#pm-rename", "#pm-delete"]) $(b).classList.toggle("hidden", !f);
  $("#pm-save").classList.toggle("hidden", !f || f.text === undefined);
  if (f && f.text !== undefined) {
    ta.value = f.text;
    ta.readOnly = !pm.edit;
    ta.classList.remove("hidden");
    info.innerHTML = "";
    info.classList.add("hidden");
    ta.focus();
  } else {
    ta.classList.add("hidden");
    info.classList.remove("hidden");
    info.innerHTML = f
      ? `<p><b>${esc(f.path)}</b> is not a text file (${size(f.size)}): rename, move or replace it with an upload.</p><p>Link to it from a page:</p><p class="mono">${esc(linkFor(f.root, f.path))}</p>`
      : `<p>Choose a file on the left, or create a page with <b>New page</b>.</p><p>Pages are <span class="mono">.mu</span> files in micron markup; <span class="mono">index.mu</span> is the home page. Put downloads in <span class="mono">files/</span> and images for pages in <span class="mono">pages/</span>.</p>`;
  }
  if (pm.items.length) renderTree();
}

$("#pm-text").addEventListener("input", () => {
  if (!pm.dirty) {
    pm.dirty = true;
    $("#pm-state").textContent = "unsaved changes";
  }
});
$("#pm-text").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    savePage();
  } else if (e.key === "Tab" && !e.shiftKey && !e.ctrlKey) {
    e.preventDefault();
    document.execCommand("insertText", false, "    ");
  }
});
$("#pm-save").addEventListener("click", savePage);

async function savePage() {
  if (!pm.cur || pm.cur.text === undefined || !pm.edit) return;
  try {
    const r = await api("/api/files/save", { node: pm.node, root: pm.cur.root, path: pm.cur.path, text: $("#pm-text").value, mtime: pm.cur.mtime });
    pm.cur.mtime = r.mtime;
    pm.dirty = false;
    $("#pm-state").textContent = `saved ${new Date().toLocaleTimeString("en-GB")}`;
    loadTree();
  } catch (err) {
    toast("Not saved: " + err.message);
  }
}

const joinPath = (dir, name) => (dir ? `${dir}/${name}` : name);

$("#pm-new").addEventListener("click", async () => {
  const ok = await ask("New page", `<div class="form"><div class="field"><label for="np-name">File name</label><input id="np-name" type="text" value="new.mu"></div></div><p class="muted small">In ${esc(pm.folder.root)}/${esc(pm.folder.dir)}</p>`, "Create page");
  let name = $("#np-name")?.value.trim();
  if (!ok || !name) return;
  if (!/\.[a-z0-9]+$/i.test(name)) name += ".mu";
  const path = joinPath(pm.folder.dir, name);
  if (pm.items.some((x) => x.root === pm.folder.root && x.path === path)) return toast("A file with that name exists already");
  try {
    await api("/api/files/save", { node: pm.node, root: pm.folder.root, path, text: name.endsWith(".mu") ? `>${name.replace(/\.mu$/, "")}\n\n` : "" });
    await loadTree();
    showFile(await api(`/api/file?node=${encodeURIComponent(pm.node)}&root=${pm.folder.root}&path=${encodeURIComponent(path)}`));
  } catch (err) {
    toast("Error: " + err.message);
  }
});

$("#pm-mkdir").addEventListener("click", async () => {
  const ok = await ask("New folder", `<div class="form"><div class="field"><label for="nf-name">Folder name</label><input id="nf-name" type="text"></div></div><p class="muted small">In ${esc(pm.folder.root)}/${esc(pm.folder.dir)}</p>`, "Create folder");
  const name = $("#nf-name")?.value.trim();
  if (!ok || !name) return;
  try {
    await api("/api/files/mkdir", { node: pm.node, root: pm.folder.root, path: joinPath(pm.folder.dir, name) });
    pm.folder.dir = joinPath(pm.folder.dir, name);
    loadTree();
  } catch (err) {
    toast("Error: " + err.message);
  }
});

$("#pm-upload").addEventListener("click", () => $("#pm-file").click());
$("#pm-file").addEventListener("change", async (e) => {
  const list = [...e.target.files];
  e.target.value = "";
  const clash = list.filter((f) => pm.items.some((x) => x.root === pm.folder.root && x.path === joinPath(pm.folder.dir, f.name)));
  if (clash.length && !(await ask("Replace files", `<p>These already exist in ${esc(pm.folder.root)}/${esc(pm.folder.dir)} and will be replaced:</p><p class="mono">${clash.map((f) => esc(f.name)).join("<br>")}</p>`, "Replace", true))) return;
  let done = 0;
  for (const f of list) {
    const path = joinPath(pm.folder.dir, f.name);
    const r = await fetch(`/api/files/upload?node=${encodeURIComponent(pm.node)}&root=${pm.folder.root}&path=${encodeURIComponent(path)}`,
      { method: "POST", headers: { "X-Requested-With": "nomad-server", "Content-Type": "application/octet-stream" }, body: f });
    if (r.ok) done++;
    else toast(`${f.name}: ${(await r.json().catch(() => ({}))).error || r.statusText}`);
  }
  if (done) toast(`Uploaded ${done} ${done === 1 ? "file" : "files"}`);
  loadTree();
});

$("#pm-rename").addEventListener("click", async () => {
  if (!pm.cur) return;
  const from = `${pm.cur.root}/${pm.cur.path}`;
  const ok = await ask("Rename or move", `<div class="form"><div class="field"><label for="rn-path">New path</label><input id="rn-path" type="text" value="${esc(from)}"></div></div><p class="muted small">Starts with pages/ or files/. Links in other pages are not changed.</p>`, "Rename");
  const to = $("#rn-path")?.value.trim().replace(/^\/+/, "");
  if (!ok || !to || to === from) return;
  const [newRoot, ...rest] = to.split("/");
  try {
    await api("/api/files/rename", { node: pm.node, root: pm.cur.root, path: pm.cur.path, new_root: newRoot, new_path: rest.join("/") });
    toast("Renamed");
    showFile(null);
    loadTree();
  } catch (err) {
    toast("Error: " + err.message);
  }
});

$("#pm-delete").addEventListener("click", async () => {
  if (!pm.cur) return;
  if (!(await ask("Move to trash", `<p><b>${esc(pm.cur.root)}/${esc(pm.cur.path)}</b> moves to the node's <span class="mono">.trash</span> folder and stops being served. Nothing is deleted.</p>`, "Move to trash", true))) return;
  try {
    await api("/api/files/delete", { node: pm.node, root: pm.cur.root, path: pm.cur.path });
    toast("Moved to trash");
    showFile(null);
    loadTree();
  } catch (err) {
    toast("Error: " + err.message);
  }
});

$("#pm-open").addEventListener("click", () => openFolder("node", pm.node));
$("#pm-close").addEventListener("click", closePages);
$("#pm").addEventListener("cancel", (e) => { e.preventDefault(); closePages(); });
async function closePages() {
  if (pm.dirty && !(await ask("Unsaved changes", `<p>Close without saving <b>${esc(pm.cur.path)}</b>?</p>`, "Close", true))) return;
  pm.dirty = false;
  $("#pm").close();
  loadOverview();
}

// ---- start ----

// Shown by the HTML at once; it leaves after 2 s, or at once when the setting is off
function hideSplash(after) {
  if (hideSplash.done) return;
  hideSplash.done = true;
  const el = $("#splash");
  setTimeout(() => {
    el.classList.add("hide");
    setTimeout(() => {
      el.remove();
      document.documentElement.classList.remove("splashing");
    }, 600);
  }, after);
}

async function boot() {
  let s;
  try {
    s = await fetch("/api/session").then((r) => r.json());
  } catch (e) {
    return setTimeout(boot, 3000);
  }
  let again = false;
  try { again = !!sessionStorage.getItem("ns-reload"); sessionStorage.removeItem("ns-reload"); } catch (e) { /* private mode */ }
  hideSplash(s.splash && !again ? 2000 : 0);
  if (!s.ok) return showLogin();
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#logout").classList.toggle("hidden", !s.password);
  await loadBlocked();
  await loadOverview();
  let saved = "overview";
  try { saved = localStorage.getItem("ns-tab") || "overview"; } catch (e) { /* private mode */ }
  // the Honeypot tab only exists in advanced mode
  if (saved === "honeypot" && !advanced()) saved = "overview";
  setTab(saved);
  if (!boot.started) {
    boot.started = true;
    setInterval(loadOverview, 3000);
    setInterval(pollLog, 1500);
    setInterval(() => { if (tab === "stats") loadStats(); }, 10000);
    setInterval(() => { if (tab === "honeypot") loadHoneypot(); }, 5000);
    pollLog();
  }
}

boot();
