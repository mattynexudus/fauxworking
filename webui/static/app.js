"use strict";

/* ============================================================ utilities */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (v === true) n.setAttribute(k, "");
    else if (v !== false && v != null) n.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) n.append(kid);
  return n;
}

// <svg class="i"><use href="#i-name"></use></svg> — the sprite in index.html.
function icon(name, cls) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", cls || "i");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

const store = {
  get(k) { try { return JSON.parse(localStorage.getItem("fauxworking." + k)); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem("fauxworking." + k, JSON.stringify(v)); } catch (_) {} },
};

async function api(path, opts) {
  let res;
  try { res = await fetch(path, opts); }
  catch (_) { setConn(false); const e = new Error("network"); e.offline = true; throw e; }
  setConn(true);
  let body = null;
  try { body = await res.json(); } catch (_) {}
  if (!res.ok) {
    const e = new Error((body && body.error) || res.statusText);
    e.status = res.status; e.body = body; throw e;
  }
  return body;
}
const jpost = (path, obj) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj || {}) });

function setConn(ok) {
  if (ok === state.connOk) return;
  state.connOk = ok;
  $("#conn-banner").hidden = ok;
}

function setTitle(marker) {
  document.title = marker ? `${marker} · Fauxworking` : "Fauxworking control panel";
}

let toastTimer = null;
function toast(msg) {
  const t = $("#toast");
  t.innerHTML = "";
  t.append(icon("check"), el("span", { text: msg }));
  t.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
}

// Two-click confirm on a button, auto-disarming after `ms`. Still used by
// sign-out and cancel; the seed/teardown paths use the modal instead.
function armable(btn, { armLabel, run, ms = 4000 }) {
  const orig = btn.textContent;
  let armed = false, timer = null;
  const disarm = () => {
    armed = false; btn.classList.remove("armed"); btn.textContent = orig;
    if (timer) { clearTimeout(timer); timer = null; }
  };
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    if (!armed) {
      armed = true; btn.classList.add("armed"); btn.textContent = armLabel;
      timer = setTimeout(disarm, ms);
      return;
    }
    disarm(); run();
  });
  btn._disarm = disarm;
  return btn;
}

/* ---------- confirm modal ---------- */
/* Resolves true only on an explicit confirm. `phrase`, when given, keeps the
   confirm button disabled until it's typed back verbatim — the teardown gate,
   moved from an inline field into the dialog. */
function confirmDialog({ title, body, confirmLabel = "Confirm", phrase = null,
                        tone = "danger", setup = null }) {
  const dlg = $("#confirm-modal");
  $("#confirm-title").textContent = title;
  const bodyEl = $("#confirm-body");
  bodyEl.innerHTML = "";
  for (const part of Array.isArray(body) ? body : [body]) bodyEl.append(part);

  const wrap = $("#confirm-phrase-wrap");
  const input = $("#confirm-phrase");
  const ok = $("#confirm-ok");
  input.value = "";

  // Anything the dialog's own controls change (mode radios, a dry-run box)
  // goes through this, so the phrase gate / label / tone stay in step with
  // what the button would actually do.
  const ctl = {
    setPhrase(p) {
      phrase = p;
      wrap.hidden = !p;
      if (p) { $("#confirm-phrase-label").textContent = `Type “${p}” to continue:`; input.placeholder = p; }
      ctl.refresh();
    },
    setLabel(l) { ok.textContent = l; },
    setTone(t) { ok.className = "btn small " + t; },
    refresh() { ok.disabled = !!phrase && input.value !== phrase; },
  };
  ctl.setLabel(confirmLabel);
  ctl.setTone(tone);
  ctl.setPhrase(phrase);
  if (setup) setup(ctl, bodyEl);
  const sync = () => ctl.refresh();
  sync();

  return new Promise(resolve => {
    let settled = false;
    const done = (val) => {
      if (settled) return;
      settled = true;
      input.removeEventListener("input", sync);
      ok.removeEventListener("click", onOk);
      $("#confirm-cancel").removeEventListener("click", onCancel);
      dlg.removeEventListener("close", onCancel);
      if (dlg.open) dlg.close();
      resolve(val);
    };
    const onOk = () => { if (!ok.disabled) done(true); };
    const onCancel = () => done(false);
    input.addEventListener("input", sync);
    ok.addEventListener("click", onOk);
    $("#confirm-cancel").addEventListener("click", onCancel);
    dlg.addEventListener("close", onCancel);   // covers Esc and the backdrop
    dlg.showModal();
    (phrase ? input : ok).focus();
  });
}

/* ============================================================ state */
const state = {
  connOk: true,
  auth: false,
  commands: [],
  byId: {},
  wizard: null,
  headlineKeys: [],
  layers: [],             // [{index, label, class}]
  layerByEntity: {},      // apiPath -> layer index
  entityLabels: {},       // apiPath -> readable name
  entityByVolKey: {},     // volume key -> apiPath
  hardCount: 4,
  bizMode: "none",
  businesses: [],
  businessId: null,
  bizError: null,
  activeRunId: null,
  stream: null,
  streamingRunId: null,
  streamErrCount: 0,
  lastRun: null,          // {command, dryRun, status}
  lastReport: null,
  plan: null,             // {counts, seeded}
  volCells: {},           // volume key -> {row, input, p}
  argvCache: { key: null, display: "" },
  pollTick: 0,
  ticker: null,
  runStartedAt: null,
  layerState: {},         // layer index -> {status, counts, startedAt}
  guided: { values: {} },
  skipLayers: [],         // layer indices deselected in the table
};

/* ============================================================ boot */
async function loadAll() {
  await loadBusinesses(false);
  if (!(await loadCommands())) return;
  await loadPlan();
  await loadReport();
  renderEntityTable();
  renderActionBar();
  renderDailyCard();
  await loadRuns();
  if (state.activeRunId && state.streamingRunId !== state.activeRunId) {
    attachStream(state.activeRunId, true);
  }
}

async function loadPlan() {
  try { state.plan = await api("/api/plan"); } catch (_) {}
}

function showLoggedOut() {
  if (state.stream) { state.stream.close(); state.stream = null; }
  state.streamingRunId = null;
  state.activeRunId = null;
  state.plan = null;
  state.commands = [];
  state.byId = {};
  state.volCells = {};
  state.lastReport = null;
  setTitle(null);
  resetConsole();
  for (const id of ["entity-groups", "daily-fields", "report", "report-summary",
                    "runs-body", "outputs", "outputs-summary", "layer-list"]) {
    const n = document.getElementById(id);
    if (n) n.textContent = "";
  }
  for (const id of ["status-badge", "run-hint", "loc", "cancel-btn", "run-status", "global-refresh"]) {
    const n = document.getElementById(id);
    if (n) n.hidden = true;
  }
}

async function refreshStatus() {
  let s;
  try { s = await api("/api/status"); } catch (_) { return; }

  const wasAuth = state.auth;
  state.auth = s.authenticated;
  $("#auth-text").textContent = s.authenticated ? "signed in" : "signed out";
  $("#auth-pill").className = "pill " + (s.authenticated ? "ok" : "bad");
  $("#login").hidden = s.authenticated;
  $("#main").hidden = !s.authenticated;
  $("#signout-btn").hidden = !s.authenticated;
  $("#global-refresh").hidden = !s.authenticated;

  if (!s.authenticated) { showLoggedOut(); return; }

  state.activeRunId = s.active_run ? s.active_run.run_id : null;
  renderActiveRun(s.active_run);
  if (s.active_run && state.streamingRunId !== s.active_run.run_id) {
    attachStream(s.active_run.run_id, true);
  }
  updateAllRunStates();

  if (!wasAuth) { loadAll(); return; }

  // slow background refresh (~21s) so a CLI run elsewhere doesn't leave us stale
  state.pollTick = (state.pollTick + 1) % 7;
  if (state.pollTick === 0 && !state.activeRunId) idleRefresh();
}

function idleRefresh() {
  loadRuns();
  loadReport().then(() => { syncEntityTable(); syncDailyVisibility(); });
  loadPlan().then(() => { syncEntityTable(); updateActionBar(); });
}

/* ============================================================ static wiring */
function wireStatic() {
  $("#login-form").addEventListener("submit", onLogin);
  armable($("#signout-btn"), { armLabel: "Sign out — CLI too?", run: doSignOut });
  armable($("#cancel-btn"), { armLabel: "Really stop it?", run: doCancel });
  $("#global-refresh").addEventListener("click", idleRefresh);
  $("#outputs-empty").addEventListener("change", renderOutputs);
  $("#btn-resync").addEventListener("click", onResync);

  const con = $("#console");
  con.addEventListener("scroll", () => {
    const atBottom = con.scrollHeight - con.scrollTop - con.clientHeight < 24;
    if ($("#follow").checked !== atBottom) $("#follow").checked = atBottom;
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    setTitle(null);
    if (state.auth && !state.activeRunId) idleRefresh();
  });
}

async function onLogin(ev) {
  ev.preventDefault();
  const f = ev.target;
  $("#login-error").textContent = "";
  try {
    await jpost("/api/auth/login", { email: f.email.value, password: f.password.value });
    f.password.value = "";
    await refreshStatus();
  } catch (e) {
    $("#login-error").textContent = e.offline ? "Can’t reach the server." : e.message;
  }
}

async function doSignOut() {
  try { await jpost("/api/auth/logout", {}); location.reload(); }
  catch (e) { alert(e.status === 409 ? e.message : "Sign out failed: " + e.message); }
}

async function loadBusinesses(refresh) {
  let b;
  try { b = await api("/api/businesses" + (refresh ? "?refresh=1" : "")); }
  catch (_) { return; }
  state.bizMode = b.mode;
  state.businesses = b.businesses || [];
  state.bizError = b.error || null;

  const stored = store.get("businessId");
  if (b.mode === "single") state.businessId = state.businesses[0].id;
  else if (b.mode === "multi")
    state.businessId = state.businesses.some(x => x.id === stored) ? stored : null;
  else state.businessId = null;

  renderLocation();
  updateAllRunStates();
}

function renderLocation() {
  const loc = $("#loc");
  loc.innerHTML = "";
  if (!state.auth) { loc.hidden = true; return; }
  loc.hidden = false;

  if (state.bizMode === "multi") {
    loc.append(el("span", { text: "Location" }));
    const sel = el("select", {
      "aria-label": "Nexudus location",
      onchange: e => {
        state.businessId = e.target.value ? Number(e.target.value) : null;
        store.set("businessId", state.businessId);
        updateAllRunStates();
      },
    });
    sel.append(el("option", { value: "" }, "— choose —"));
    for (const it of state.businesses) {
      const o = el("option", { value: String(it.id) }, it.name);
      if (it.id === state.businessId) o.selected = true;
      sel.append(o);
    }
    loc.append(sel);
  } else if (state.bizMode === "single") {
    loc.append(el("span", { text: "Location" }), el("strong", { text: state.businesses[0].name }));
  } else {
    loc.append(state.bizError
      ? el("span", { class: "err", text: state.bizError })
      : el("span", { class: "muted", text: "No locations on this login" }));
  }
}

/* ============================================================ commands */
async function loadCommands() {
  try {
    const data = await api("/api/commands");
    state.commands = data.commands || [];
    state.byId = Object.fromEntries(state.commands.map(c => [c.id, c]));
    state.wizard = state.byId.wizard || null;
    state.headlineKeys = data.headline_volume_keys || [];
    state.layers = data.layers || [];
    state.layerByEntity = data.layer_by_entity || {};
    state.entityLabels = data.entity_labels || {};
    state.entityByVolKey = data.entity_by_volume_key || {};
    state.hardCount = data.hard_dependency_layer_count != null
      ? data.hard_dependency_layer_count : 4;
    // A restored selection can name a layer the server would now reject (an
    // older build, a hand-edited localStorage). Drop those rather than letting
    // the first run fail on a 400 the user can't see the cause of.
    state.skipLayers = state.skipLayers.filter(
      n => n >= state.hardCount && n < state.layers.length);
    return true;
  } catch (e) {
    $("#entity-groups").innerHTML = "";
    $("#entity-groups").append(
      el("p", { class: "err small", style: "padding:12px 15px",
                text: (e.offline ? "Server unreachable." : e.message) + " " }),
      el("button", { class: "btn small", style: "margin:0 15px 12px",
                     onclick: () => loadAll() }, "Retry"));
    return false;
  }
}

function volumeParams() {
  return (state.wizard ? state.wizard.params : [])
    .filter(p => p.type === "int" && !["seed", "layer"].includes(p.name));
}

/* ---------- shared field rendering (used by the command cards) ---------- */
function renderField(p, value, onInput) {
  const wrap = el("div", { class: "field" + (p.type === "bool" ? " check" : "") });
  const id = "f-" + Math.random().toString(36).slice(2, 8);
  let input;

  if (p.type === "bool") {
    input = el("input", { type: "checkbox", id });
    input.checked = value != null ? !!value : !!p.default;
    input.addEventListener("change", () => onInput(input.checked));
    wrap.append(input, el("label", { for: id, text: p.label }));
    if (p.help) wrap.append(el("span", { class: "hint", text: p.help }));
    return { wrap, input, get: () => input.checked };
  }

  wrap.append(el("label", { for: id, text: p.label }));
  if (p.type === "choice") {
    input = el("select", { id });
    for (const [v, lbl] of p.choices || []) {
      const o = el("option", { value: String(v) }, lbl);
      if (String(v) === String(value != null ? value : p.default)) o.selected = true;
      input.append(o);
    }
  } else {
    input = el("input", {
      id, type: p.type === "date" ? "date" : "number",
      value: value != null ? value : (p.default != null ? p.default : ""),
    });
    if (p.min != null) input.min = p.min;
    if (p.max != null) input.max = p.max;
  }
  input.addEventListener("input", () => { clearFieldErr(wrap); onInput(readInput(input, p)); });
  input.addEventListener("change", () => onInput(readInput(input, p)));
  wrap.append(input);
  if (p.help) wrap.append(el("span", { class: "hint", text: p.help }));

  if (p.type === "int" && p.soft_max != null) {
    const warn = el("span", { class: "warn-hint", hidden: true });
    const check = () => {
      const over = input.value !== "" && Number(input.value) > p.soft_max;
      warn.hidden = !over;
      warn.textContent = over ? "large value — expect a slow run" : "";
      wrap.classList.toggle("warn", over);
    };
    input.addEventListener("input", check);
    wrap.append(warn);
    check();
  }
  return { wrap, input, get: () => readInput(input, p) };
}

function readInput(input, p) {
  if (p.type === "bool") return input.checked;
  return input.value === "" ? undefined : input.value;
}
function clearFieldErr(wrap) {
  wrap.classList.remove("bad");
  $$(".field-err", wrap).forEach(n => n.remove());
}
function setFieldErr(wrap, msg) {
  clearFieldErr(wrap);
  wrap.classList.add("bad");
  wrap.append(el("span", { class: "field-err", text: msg }));
}

/* ============================================================ entity table */
function gVal(name, dflt) { const v = state.guided.values[name]; return v != null ? v : dflt; }
function gSet(name, v) { state.guided.values[name] = v; store.set("guided", state.guided.values); }
function planCount(k) { return state.plan && state.plan.counts ? state.plan.counts[k] : undefined; }
function planSeeded(k) { return (state.plan && state.plan.seeded ? state.plan.seeded[k] : 0) || 0; }

function reportByEntity() {
  const rows = (state.lastReport && state.lastReport.report) || [];
  return Object.fromEntries(rows.map(r => [r.entity, r]));
}

function renderEntityTable() {
  const wrap = $("#entity-groups");
  if (!wrap) return;
  wrap.innerHTML = "";
  state.volCells = {};
  if (!state.auth || !state.wizard) return;

  const volParams = Object.fromEntries(volumeParams().map(p => [p.name, p]));
  const entityVol = Object.fromEntries(
    Object.entries(state.entityByVolKey).map(([k, e]) => [e, k]));
  const isEditable = e => !!(entityVol[e] && volParams[entityVol[e]]);

  // group every known entity by its layer
  const byLayer = {};
  for (const [entity, layer] of Object.entries(state.layerByEntity)) {
    (byLayer[layer] = byLayer[layer] || []).push(entity);
  }

  // One table for the whole panel, with a tbody per layer. A single sticky
  // header means the column labels are stated once and every layer's numbers
  // line up in the same columns, which per-layer tables couldn't guarantee.
  const table = el("table", { class: "etab" });
  table.append(el("thead", {}, el("tr", {},
    el("th", { text: "Entity" }),
    el("th", { text: "live" }),
    el("th", { text: "last run" }),
    el("th", { text: "target" }))));

  for (const L of state.layers) {
    const entities = byLayer[L.index] || [];
    if (!entities.length) continue;
    const locked = L.index < state.hardCount;
    const off = state.skipLayers.includes(L.index);

    const body = el("tbody", { class: "lgroup" + (off ? " off" : ""), "data-layer": L.index });

    const cb = el("input", {
      type: "checkbox", "aria-label": `Include layer ${L.index}`,
      title: locked ? "Layers 0\u20133 are a hard dependency chain \u2014 every later layer reads their IDs"
                    : `Include layer ${L.index} in the run`,
    });
    cb.checked = !off;
    cb.disabled = locked;
    cb.addEventListener("change", () => {
      const set = new Set(state.skipLayers);
      if (cb.checked) set.delete(L.index); else set.add(L.index);
      state.skipLayers = [...set].sort((a, b) => a - b);
      store.set("skipLayers", state.skipLayers);
      body.classList.toggle("off", !cb.checked);
      updateActionBar();
    });

    const head = el("div", { class: "lgroup-head" },
      cb,
      el("span", { class: "lg-num", text: String(L.index) }),
      el("span", { class: "lg-name", text: layerName(L) }),
      el("span", { class: "lg-desc", text: layerBlurb(L) }));
    if (locked) head.append(el("span", { class: "lock", text: "always runs" }));
    head.append(el("span", { class: "lg-state", id: `lgs-${L.index}`, hidden: true }));
    body.append(el("tr", { class: "lgroup-row" }, el("td", { colspan: "4" }, head)));

    // editable rows first, then the read-only ones alphabetically
    const ordered = [...entities].sort((a, b) =>
      (isEditable(b) - isEditable(a)) || entityLabel(a).localeCompare(entityLabel(b)));
    for (const entity of ordered) {
      body.append(entityRow(entity, entityVol, volParams, isEditable));
    }
    table.append(body);
  }
  wrap.append(table);
  syncEntityTable();
}

function entityLabel(entity) {
  return (state.entityLabels && state.entityLabels[entity]) || entity;
}

function layerName(L) {
  // LAYER_DESCRIPTIONS reads "People \u2014 coworkers and visitors"; the part
  // before the dash is the name, the rest is the blurb.
  return String(L.label).split("\u2014")[0].trim() || L.class;
}
function layerBlurb(L) {
  const parts = String(L.label).split("\u2014");
  return parts.length > 1 ? parts.slice(1).join("\u2014").trim() : "";
}

function entityRow(entity, entityVol, volParams, isEditable) {
  const key = entityVol[entity];
  const p = key ? volParams[key] : null;

  const live = el("td", { class: "num live" });
  const lastRun = el("td", { class: "lastrun" });
  const targetCell = el("td", { class: "target" });

  let input = null, target = null;
  if (isEditable(entity) && p) {
    const stored = state.guided.values[key];
    const start = (stored != null && stored !== "") ? stored
      : (planCount(key) != null ? planCount(key) : p.default);
    input = el("input", { type: "number", min: p.min != null ? p.min : 0, value: start,
                          "aria-label": `${p.label} target` });
    if (p.max != null) input.max = p.max;
    input.addEventListener("input", () => {
      gSet(key, input.value === "" ? undefined : input.value);
      syncEntityTable();
      updateActionBar();
    });
    targetCell.append(input);
  } else {
    // Not user-editable, but often still a real, known number (report.py's
    // target_for() \u2014 see report_lib.real_targets()) \u2014 shown plain rather
    // than as an input, filled in by syncEntityTable() same as the live count.
    target = el("span", { class: "muted" });
    targetCell.append(target);
  }

  const row = el("tr", { class: "erow" },
    el("th", { scope: "row", text: entityLabel(entity), title: entity }),
    live, lastRun, targetCell);
  if (key) state.volCells[key] = { row, input, p, live, lastRun, entity };
  else row._static = { live, lastRun, target, entity };
  return row;
}

// "+7" (safe), "3 failed" (danger), "+7 · 2 failed" (danger — a partial run
// still counts as a problem worth the warning colour), or an em dash when
// the last run never touched this entity at all (skipped layer, or another
// layer owns it) — distinct from "no change", which means it ran and
// everything it needed was already there.
function renderLastRun(cell, lastRun) {
  cell.innerHTML = "";
  cell.classList.remove("good", "bad");
  if (!lastRun) { cell.textContent = "—"; return; }
  const parts = [];
  if (lastRun.created) parts.push(`+${lastRun.created.toLocaleString()}`);
  if (lastRun.failed) parts.push(`${lastRun.failed.toLocaleString()} failed`);
  cell.textContent = parts.length ? parts.join(" · ") : "no change";
  cell.classList.add(lastRun.failed ? "bad" : (lastRun.created ? "good" : "zero"));
}

// Colour the live count against its target: neutral until something's seeded,
// amber while short of target, green once it's reached. One cell carries the
// whole status — no separate chip, no "generated"/"to add" columns.
function paintLive(cell, seeded, target) {
  cell.classList.remove("short", "met");
  if (target == null || seeded === 0) return;
  cell.classList.add(seeded >= target ? "met" : "short");
}

// In-place refresh of counts/status — no rebuild, so an input keeps focus.
function syncEntityTable() {
  const rep = reportByEntity();

  for (const [key, c] of Object.entries(state.volCells)) {
    const seeded = planSeeded(key);
    c.live.textContent = seeded ? seeded.toLocaleString() : "0";

    // Target is whatever the user has typed — colour updates live as they edit.
    const val = c.input && c.input.value !== "" ? Number(c.input.value) : null;
    paintLive(c.live, seeded, val);
    renderLastRun(c.lastRun, rep[c.entity] && rep[c.entity].last_run);

    if (c.input && c.p) {
      const over = val != null && ((c.p.max != null && val > c.p.max) ||
                                   (c.p.soft_max != null && val > c.p.soft_max));
      c.row.classList.toggle("warn", !!over);
      c.input.classList.toggle("bad", c.p.max != null && val != null && val > c.p.max);
      c.input.title = c.p.max != null && val != null && val > c.p.max
        ? `${c.p.label} is capped at ${c.p.max} — ${c.p.help}` : "";
    }
  }

  // read-only rows: no editable plan, but target_for() is still a real, known
  // number — shown plain, and the live count is coloured against it the same way.
  for (const row of $$("#entity-groups tr.erow")) {
    if (!row._static) continue;
    const { live, lastRun, target, entity } = row._static;
    const r = rep[entity];
    const seeded = r ? r.created : 0;
    const tgt = r && r.target != null ? r.target : null;
    live.textContent = seeded.toLocaleString();
    paintLive(live, seeded, tgt);
    renderLastRun(lastRun, r && r.last_run);
    if (target) target.textContent = tgt != null ? tgt.toLocaleString() : "—";
  }

  const s = (state.lastReport && state.lastReport.summary) || {};
  const sub = $("#entities-sub");
  if (sub) {
    sub.textContent = `${(s.total || 0).toLocaleString()} records tracked live`
      + (s.entities ? ` · ${s.below_target || 0} of ${s.entities} entities below target` : "")
      // Stray data/created-ids/*.json records missing their "entity" tag —
      // rare, but silent otherwise (report_lib._grouped_records excludes
      // them from every count above rather than guessing).
      + (s.malformed ? ` · ${s.malformed} untagged record${s.malformed === 1 ? "" : "s"} excluded` : "");
  }
}


/* ============================================================ action bar */
let actionBarWired = false;
function renderActionBar() {
  if (!state.auth || !state.wizard) return;

  for (const [id, name, dflt] of [["#opt-export", "export_csv", true],
                                  ["#opt-fresh", "fresh", false]]) {
    $(id).checked = gVal(name, dflt) === true;
  }

  // loadAll() re-runs on re-auth, so the listeners are attached exactly once.
  if (!actionBarWired) {
    actionBarWired = true;
    for (const [id, name] of [["#opt-export", "export_csv"], ["#opt-fresh", "fresh"]]) {
      $(id).addEventListener("change", () => { gSet(name, $(id).checked); updateActionBar(); });
    }
    $("#opt-dryrun").addEventListener("change", updateActionBar);
    $("#btn-verify").addEventListener("click", () =>
      startRun({ command: "verify", params: {}, dry_run: false }, $("#run-err")));
    $("#btn-teardown").addEventListener("click", onTeardown);
    $("#btn-run").addEventListener("click", onRun);
  }
  updateActionBar();
}

function guidedParams() {
  const p = {};
  for (const vp of volumeParams()) {
    const v = state.guided.values[vp.name];
    if (v != null && v !== "") p[vp.name] = v;
  }
  p.export_csv = $("#opt-export").checked;
  if ($("#opt-fresh").checked) p.fresh = true;
  if (state.skipLayers.length) p.skip_layers = state.skipLayers;
  return p;
}

function updateActionBar() {
  if (!$("#btn-run")) return;
  const dry = $("#opt-dryrun").checked;
  const fresh = $("#opt-fresh").checked;

  // drift warning — "start fresh" is the one control that can diverge from live
  const warn = $("#fresh-warn");
  warn.hidden = !fresh;
  warn.textContent =
    "Rewrites every data file from scratch — diverges from what's already seeded live.";

  // primary button tone follows the checkbox — a live write never looks like a preview
  const btn = $("#btn-run");
  $("#btn-run-label").textContent = dry ? "Preview run" : "Seed to live account";
  btn.classList.toggle("live", !dry);

  // what this run will do
  const labelOf = k => (volumeParams().find(p => p.name === k) || {}).label || k;
  const adds = volumeParams().map(p => {
    const target = Number(gVal(p.name, planCount(p.name) ?? 0));
    const have = Number(planCount(p.name) ?? 0);
    return target > have ? `+${target - have} ${labelOf(p.name).toLowerCase()}` : null;
  }).filter(Boolean);

  let echo;
  if (fresh) echo = "Rebuilds every data file from scratch.";
  else if (adds.length) echo = `Adds ${adds.slice(0, 4).join(", ")}${adds.length > 4 ? `, +${adds.length - 4} more` : ""}.`;
  else echo = "No target raised — regeneration is skipped, it just seeds the current plan.";

  const running = state.layers.filter(L => !state.skipLayers.includes(L.index));
  const skipTxt = state.skipLayers.length ? ` (skipping ${state.skipLayers.join(", ")})` : "";
  const where = state.bizMode === "none" ? "the account's business"
    : bizName(state.businessId) || (state.businesses[0] && state.businesses[0].name) || "—";
  $("#run-echo").textContent = `${echo} Runs ${running.length} of ${state.layers.length} `
    + `layers${skipTxt} into ${where}.`;

  // equivalent CLI command (debounced)
  const params = guidedParams();
  const key = JSON.stringify([params, state.businessId, dry]);
  const cmd = $("#cli-preview");
  if (key === state.argvCache.key) { cmd.textContent = state.argvCache.display; }
  else {
    jpost("/api/argv", { command: "wizard", params, business_id: state.businessId, dry_run: dry })
      .then(r => {
        state.argvCache = { key, display: r.display };
        cmd.textContent = r.display;
        $("#run-err").textContent = "";
      })
      .catch(e => { $("#run-err").textContent = e.offline ? "" : e.message; });
  }

  // gating. Verify is offline and business-agnostic (it just counts local
  // tracking files), so a missing location doesn't block it — only a run in
  // progress does.
  const blocked = runBlockedReason({ accepts_business_id: true });
  for (const b of [$("#btn-run"), $("#btn-teardown")]) {
    b.disabled = !!blocked;
    b.title = blocked || "";
  }
  const verifyBlocked = runBlockedReason({ accepts_business_id: false });
  $("#btn-verify").disabled = !!verifyBlocked;
  $("#btn-verify").title = verifyBlocked || "";
  // Re-sync reads the live account, so it needs a location like a write does.
  $("#btn-resync").disabled = !!blocked;
  $("#btn-resync").title = blocked || "";
  $("#run-reason").textContent = blocked || "";

  // post-preview nudge
  const gl = $("#golive");
  const show = state.lastRun && state.lastRun.command === "wizard" &&
    state.lastRun.dryRun && state.lastRun.status === "succeeded" && !state.activeRunId;
  gl.hidden = !show;
  if (show && !gl.dataset.built) {
    gl.dataset.built = "1";
    gl.append(el("span", { text: "That was a preview. Run it for real now?" }),
      el("button", { class: "btn small danger", onclick: () => { $("#opt-dryrun").checked = false; updateActionBar(); onRun(); } },
        "Run for real"));
  }
}

async function onRun() {
  const dry = $("#opt-dryrun").checked;
  if (!dry) {
    const running = state.layers.filter(L => !state.skipLayers.includes(L.index));
    const ok = await confirmDialog({
      title: "Seed to the live account?",
      body: [
        el("p", { html: `This creates <strong>real records</strong> in `
          + `<strong>${escapeHtml(bizName(state.businessId) || (state.businesses[0] && state.businesses[0].name) || "the account's business")}</strong>.` }),
        el("p", { text: `${running.length} of ${state.layers.length} layers will run. `
          + `Records already tracked are skipped, so this only adds what's missing.` }),
      ],
      confirmLabel: "Seed to live account",
    });
    if (!ok) return;
  }
  await startRun({ command: "wizard", params: guidedParams(),
                   business_id: state.businessId, dry_run: dry }, $("#run-err"));
}

/* Teardown asks what to consider before it asks whether you're sure.
   Both modes can run for real behind a typed phrase — "tracked" uses the
   command's confirm_phrase, "clean" (a full live wipe, tracked or not)
   demands the stronger TEARDOWN_CLEAN_PHRASE, matching
   registry.confirm_phrase_for on the server. A real run also offers three
   post-teardown cleanups (plan files, CSV exports, billing counters); they
   do nothing in a preview, so they only show when "dry run" is unticked. */
const TEARDOWN_CLEAN_PHRASE = "delete everything";
async function onTeardown() {
  const cmd = state.byId.teardown || {};
  const trackedPhrase = cmd.confirm_phrase || "delete tracked records";
  const phraseFor = (mode) => (mode === "clean" ? TEARDOWN_CLEAN_PHRASE : trackedPhrase);
  const chosen = { mode: "tracked", dry: true, clear_data: false, clear_csv: false,
                   reset_counters: false };

  const ok = await confirmDialog({
    title: "Teardown",
    body: [],
    confirmLabel: "Preview",
    tone: "danger",
    setup: (ctl, bodyEl) => {
      const modes = [
        ["tracked", "Just tracked records",
         "Only the IDs this tool logged in data/created-ids/. Nothing else in the account is touched."],
        ["clean", "Everything found live",
         "Every record this tool can find in the account, tracked or not — a full wipe of this location. Needs a stronger typed phrase."],
      ];
      const extras = [
        ["clear_data", "Also delete data/*.json plan files",
         "prebuild's plan files. Safe to keep — regenerated on the next setup."],
        ["clear_csv", "Also delete output/*.csv exports",
         "The exported per-entity CSVs. Rebuilt on the next seed + export."],
        ["reset_counters", "Reset this location's billing counters to 0",
         "Booking / Invoice / Draft / Credit-note numbers Nexudus keeps incrementing. This location may also hold real records that reuse those numbers."],
      ];

      const dryRow = el("label", { class: "opt" });
      const dryBox = el("input", { type: "checkbox" });
      dryBox.checked = true;
      dryRow.append(dryBox, el("span", { text: " Preview only (dry run)" }));

      const extraBoxes = {};
      const extrasWrap = el("div", { class: "td-extras" });
      for (const [key, label, help] of extras) {
        const id = "tdx-" + key;
        const box = el("input", { type: "checkbox", id });
        box.addEventListener("change", () => { chosen[key] = box.checked; });
        extraBoxes[key] = box;
        extrasWrap.append(el("label", { class: "modal-choice", for: id },
          box,
          el("span", {},
            el("strong", { text: label }),
            el("span", { class: "muted small", text: help }))));
      }

      const apply = () => {
        chosen.dry = dryBox.checked;
        ctl.setLabel(chosen.dry ? "Preview"
          : (chosen.mode === "clean" ? "Wipe the account" : "Delete them"));
        ctl.setTone(chosen.dry ? "primary" : "danger");
        ctl.setPhrase(chosen.dry ? null : phraseFor(chosen.mode));
        // the post-teardown cleanups are no-ops in a preview
        extrasWrap.hidden = chosen.dry;
        if (chosen.dry) {
          for (const key of Object.keys(extraBoxes)) {
            extraBoxes[key].checked = false;
            chosen[key] = false;
          }
        }
      };

      for (const [val, label, help] of modes) {
        const id = "tdm-" + val;
        const radio = el("input", { type: "radio", name: "tdmode", id, value: val });
        radio.checked = val === chosen.mode;
        radio.addEventListener("change", () => { if (radio.checked) { chosen.mode = val; apply(); } });
        bodyEl.append(el("label", { class: "modal-choice", for: id },
          radio,
          el("span", {},
            el("strong", { text: label }),
            el("span", { class: "muted small", text: help }))));
      }
      bodyEl.append(dryRow, extrasWrap);
      dryBox.addEventListener("change", apply);
      apply();
    },
  });
  if (!ok) return;
  const params = { mode: chosen.mode };
  if (!chosen.dry) {
    for (const key of ["clear_data", "clear_csv", "reset_counters"]) {
      if (chosen[key]) params[key] = true;
    }
  }
  await startRun({
    command: "teardown",
    params,
    business_id: state.businessId,
    dry_run: chosen.dry,
    confirm: chosen.dry ? undefined : phraseFor(chosen.mode),
  }, $("#run-err"));
}

/* ---------- daily update ---------- */
/* The daily card only earns its spot once there's data live to keep fresh:
   before the first real seed run the account is empty and "create today's
   check-ins" has nothing to attach to, so it stays hidden. Gated on live
   tracked records (a dry-run preview creates none). It's built regardless so
   that when the gate opens mid-session the fields are already there. */
function dailyUnlocked() {
  const s = state.lastReport && state.lastReport.summary;
  return !!(state.byId.daily_update && s && s.total > 0);
}
function syncDailyVisibility() {
  const card = $("#daily");
  if (card) card.hidden = !dailyUnlocked();
}

function renderDailyCard() {
  const cmd = state.byId.daily_update;
  const card = $("#daily");
  if (!cmd) { card.hidden = true; return; }
  syncDailyVisibility();

  const fields = $("#daily-fields");
  fields.innerHTML = "";
  state.dailyGetters = {};
  for (const p of cmd.params) {
    const f = renderField(p, undefined, () => {});
    state.dailyGetters[p.name] = f.get;
    fields.append(f.wrap);
  }

  if (!state.dailyWired) {
    state.dailyWired = true;
    $("#daily-dry").addEventListener("change", updateDailyCard);
    $("#btn-daily").addEventListener("click", onDailyRun);
  }
  updateDailyCard();
}

function updateDailyCard() {
  syncDailyVisibility();
  const btn = $("#btn-daily");
  if (!btn || !state.byId.daily_update) return;
  const dry = $("#daily-dry").checked;
  $("#btn-daily-label").textContent = dry ? "Preview" : "Create today’s records";
  btn.classList.toggle("live", !dry);
  const blocked = runBlockedReason(state.byId.daily_update);
  btn.disabled = !!blocked;
  btn.title = blocked || "";
  $("#daily-reason").textContent = blocked || "";
}

async function onDailyRun() {
  const dry = $("#daily-dry").checked;
  const params = {};
  for (const [name, get] of Object.entries(state.dailyGetters || {})) {
    const v = get();
    if (v !== undefined && v !== "") params[name] = v;
  }
  if (!dry) {
    const ok = await confirmDialog({
      title: "Create today’s records?",
      body: [el("p", { html: `This writes <strong>real records</strong> for `
        + `${params.days && Number(params.days) > 1 ? `the last ${escapeHtml(String(params.days))} days` : "today"}`
        + ` into <strong>${escapeHtml(bizName(state.businessId)
            || (state.businesses[0] && state.businesses[0].name) || "the account's business")}</strong>.` })],
      confirmLabel: "Create them",
    });
    if (!ok) return;
  }
  await startRun({ command: "daily_update", params, business_id: state.businessId,
                   dry_run: dry }, $("#daily-err"));
}

/* ---------- re-sync exports ---------- */
async function onResync() {
  await startRun({ command: "refresh_output", params: {},
                   business_id: state.businessId, dry_run: false }, $("#run-err"));
}

/* Why a run can't start right now, or null. Two hard rules the server enforces
   too (jobs.JobManager.start): one run at a time, and a login with access to
   more than one business must say which one before anything touches the live
   account (CLAUDE.md rule 8). Checked here as well so the button is disabled
   with a reason rather than failing on a 400 after the click. */
function runBlockedReason(c) {
  if (state.activeRunId) return "A run is in progress — wait for it to finish.";
  if (c.accepts_business_id && state.bizMode === "multi" && !state.businessId)
    return "Pick a location in the header first.";
  return null;
}

/* Every command in the registry now has a first-class home in the UI --
   wizard drives the Data targets action bar, verify and teardown are buttons
   in it, daily_update has its own card, and refresh_output sits with the
   exports it refreshes. So there is no generic command-card renderer any
   more; the four commands are wired directly to the controls that run them. */
function updateAllRunStates() {
  updateActionBar();
  updateDailyCard();
}

/* ============================================================ run + stream */
async function startRun(payload, errSlot) {
  const showErr = (msg) => { if (errSlot) errSlot.textContent = msg; };
  showErr("");
  try {
    const r = await jpost("/api/run", payload);
    state.lastRun = { command: payload.command, dryRun: !!payload.dry_run, status: "running" };
    $("#run-hint").hidden = true;
    resetLayerProgress(payload.command);
    attachStream(r.run_id, true);
    toast(payload.dry_run ? "Preview started" : `Running ${payload.command}…`);
    refreshStatus();
  } catch (e) {
    if (e.offline) return showErr("Server unreachable.");
    if (e.status === 409) return showErr((e.body && e.body.error) || "A run is already in progress.");
    showErr(e.message);
  }
}

function attachStream(runId, clear) {
  if (state.stream) { state.stream.close(); state.stream = null; }
  if (clear) { resetConsole(); $("#status-badge").hidden = true; $("#run-hint").hidden = true; }
  state.streamingRunId = runId;
  state.streamErrCount = 0;
  setBadge("running");
  setTitle("● running");
  startTicker();

  const es = new EventSource(`/api/stream/${runId}`);
  state.stream = es;
  es.addEventListener("line", ev => appendConsole(ev.data));
  es.addEventListener("end", ev => {
    let info = {};
    try { info = JSON.parse(ev.data); } catch (_) {}
    es.close();
    state.stream = null;
    state.streamingRunId = null;
    onRunEnd(info);
  });
  es.onerror = () => {
    state.streamErrCount++;
    if (es.readyState === EventSource.CLOSED || state.streamErrCount > 5) {
      es.close();
      state.stream = null;
      state.streamingRunId = null;
      reconcileRun(runId);
    }
  };
}

async function reconcileRun(runId) {
  try {
    const d = await api(`/api/runs/${runId}`);
    if (d.status === "running") { attachStream(runId, false); return; }
    onRunEnd({ status: d.status, exit_code: d.exit_code });
  } catch (_) {
    setBadge("ended");
    stopTicker();
    appendConsole("(stream ended — loading the saved log)");
    try {
      const text = await (await fetch(`/api/runs/${runId}/log`)).text();
      resetConsole();
      $("#console").textContent = text;
    } catch (_) {}
  }
}

function onRunEnd(info) {
  stopTicker();
  setBadge(info.status || "done", info.exit_code);
  setTitle(info.status === "succeeded" ? "✓ done" : "✗ " + (info.status || "failed"));
  if (state.lastRun) state.lastRun.status = info.status;
  finishLayerProgress(info.status);
  toast(info.status === "succeeded" ? "Run finished" : `Run ${info.status || "failed"}`);

  const hint = $("#run-hint");
  if (info.status === "failed" || info.status === "error") {
    hint.hidden = false;
    hint.className = "run-hint failed";
    let msg = `Exit code ${info.exit_code}. Re-running is safe — tracked records mean it resumes where it stopped.`;
    if (state.lastRun && ["wizard", "pipeline"].includes(state.lastRun.command))
      msg += " A layer may have failed, or it fell short of target — see Results below.";
    hint.textContent = msg;
  } else {
    hint.hidden = true;
  }

  loadReport().then(() => { syncEntityTable(); syncDailyVisibility(); });
  loadRuns();
  loadPlan().then(() => { if (!$("#main").hidden) { syncEntityTable(); updateActionBar(); } });
  refreshStatus();
}

/* ---------- console + layer progress ----------
   pipeline.py already prints everything needed to track a run layer by layer,
   so the progress list is parsed straight off the stream rather than needing
   a structured progress channel: `--- Layer N: ClassName ---` opens a layer,
   `[entity] Created: c  Skipped: s  Failed: f` closes it with its counts, and
   `!!! Layer N (...) failed entirely` marks it failed. There is no total to
   divide by anywhere, so there's no percentage and no ETA — the checklist and
   the elapsed clock are what's actually knowable. */
const RE_LAYER = /^--- Layer (\d+): (\w+)(?: — (skipped by request))? ---$/;
const RE_SUMMARY = /^\[([\w-]+)\] Created: (\d+)\s+Skipped: (\d+)\s+Failed: (\d+)$/;
const RE_LAYER_FAIL = /^!!! Layer (\d+) \(/;

function resetLayerProgress(command) {
  state.runStartedAt = Date.now();
  state.layerState = {};
  const tracked = ["wizard", "pipeline"].includes(command);
  for (const L of state.layers) {
    state.layerState[L.index] = {
      status: !tracked ? "na"
        : state.skipLayers.includes(L.index) ? "skipped" : "pending",
      counts: null,
    };
  }
  $("#run-foot").hidden = false;
  $("#run-sub").textContent = tracked ? "Starting…" : `Running ${command}…`;
  renderLayerProgress(tracked);
}

function renderLayerProgress(tracked) {
  const list = $("#layer-list");
  list.innerHTML = "";
  if (!tracked) return;
  for (const L of state.layers) {
    const s = state.layerState[L.index] || { status: "pending" };
    const li = el("li", { class: s.status });
    const ic = { done: "check", running: "refresh", failed: "x", skipped: "minus" }[s.status] || "dot";
    li.append(icon(ic, "i lp-icon"),
      el("span", { class: "lp-name", text: `${L.index} · ${layerName(L)}` }));
    if (s.counts) {
      const { c, s: sk, f } = s.counts;
      li.append(el("span", { class: "lp-counts",
        text: `+${c}${sk ? ` · ${sk} skipped` : ""}${f ? ` · ${f} failed` : ""}` }));
    } else if (s.status === "skipped") {
      li.append(el("span", { class: "lp-counts", text: "deselected" }));
    }
    list.append(li);
  }
}

function parseRunLine(line) {
  let m = RE_LAYER.exec(line);
  if (m) {
    const i = Number(m[1]);
    state.layerState[i] = { status: m[3] ? "skipped" : "running", counts: null };
    $("#run-sub").textContent = m[3]
      ? `Skipped layer ${i}` : `Layer ${i} of ${state.layers.length - 1} — ${layerNameByIndex(i)}`;
    renderLayerProgress(true);
    return "c-layer";
  }
  m = RE_LAYER_FAIL.exec(line);
  if (m) {
    const i = Number(m[1]);
    if (state.layerState[i]) state.layerState[i].status = "failed";
    renderLayerProgress(true);
    return "c-err";
  }
  m = RE_SUMMARY.exec(line);
  if (m) {
    // closes whichever layer is currently open
    const cur = Object.keys(state.layerState).find(k => state.layerState[k].status === "running");
    if (cur != null) {
      state.layerState[cur] = {
        status: Number(m[4]) > 0 ? "failed" : "done",
        counts: { c: Number(m[2]), s: Number(m[3]), f: Number(m[4]) },
      };
      renderLayerProgress(true);
    }
    return "c-dim";
  }
  if (/^Traceback|^\s*!{1,3}\s|Error|error:/.test(line)) return "c-err";
  if (/^Note:|^Warning|⚠/.test(line)) return "c-warn";
  if (/^(Done|Total —|✓)/.test(line)) return "c-ok";
  if (/^===/.test(line)) return "c-layer";
  return null;
}

function layerNameByIndex(i) {
  const L = state.layers.find(x => x.index === i);
  return L ? layerName(L) : `#${i}`;
}

function finishLayerProgress(status) {
  // anything still "running" when the process exits never printed its summary
  for (const k of Object.keys(state.layerState)) {
    if (state.layerState[k].status === "running")
      state.layerState[k].status = status === "succeeded" ? "done" : "failed";
  }
  renderLayerProgress(Object.keys(state.layerState).some(k => state.layerState[k].status !== "na"));
  const done = Object.values(state.layerState).filter(s => s.status === "done").length;
  $("#run-sub").textContent = status === "succeeded"
    ? `Finished — ${done} layer${done === 1 ? "" : "s"} completed.`
    : `Ended (${status || "failed"}).`;
}

/* A live seed streams on the order of 10k lines. Appending a node and reading
   scrollHeight per line is one forced layout per line, which locks the page up
   for the whole run — so lines are queued and flushed once per frame, and the
   pane keeps a bounded tail (the full log is always available in the run
   history, served from disk). Progress parsing still sees every line. */
const CONSOLE_MAX_NODES = 4000;
let consoleQueue = [];
let consoleFlush = null;

function appendConsole(text) {
  consoleQueue.push([text, parseRunLine(text)]);
  if (consoleFlush == null) consoleFlush = requestAnimationFrame(flushConsole);
}

function flushConsole() {
  consoleFlush = null;
  const c = $("#console");
  if (!c || !consoleQueue.length) return;
  const frag = document.createDocumentFragment();
  for (const [text, cls] of consoleQueue) {
    frag.append(cls ? el("span", { class: cls, text: text + "\n" })
                    : document.createTextNode(text + "\n"));
  }
  consoleQueue = [];
  c.append(frag);
  while (c.childNodes.length > CONSOLE_MAX_NODES) c.removeChild(c.firstChild);
  if ($("#follow").checked) c.scrollTop = c.scrollHeight;
}

function resetConsole() {
  if (consoleFlush != null) { cancelAnimationFrame(consoleFlush); consoleFlush = null; }
  consoleQueue = [];
  $("#console").textContent = "";
}

function setBadge(status, code) {
  const b = $("#status-badge");
  b.hidden = false;
  b.className = "status-badge " + status;
  b.textContent = code != null && status !== "running" && status !== "succeeded"
    ? `${status} (${code})` : status;
}

function startTicker() {
  stopTicker();
  if (!state.runStartedAt) state.runStartedAt = Date.now();
  const tick = () => {
    const s = Math.max(0, Math.round((Date.now() - state.runStartedAt) / 1000));
    $("#run-elapsed").textContent = s < 60 ? `${s}s`
      : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  };
  tick();
  state.ticker = setInterval(tick, 1000);
}
function stopTicker() { if (state.ticker) { clearInterval(state.ticker); state.ticker = null; } }

function renderActiveRun(run) {
  const btn = $("#cancel-btn");
  // The footer holds only the clock and the cancel button, so before the
  // first run it would render as an empty bordered strip.
  $("#run-foot").hidden = !run && !state.runStartedAt;
  if (!run) { btn.hidden = true; stopTicker(); return; }
  btn.hidden = false;
  if (!state.runStartedAt) state.runStartedAt = run.started_at * 1000;
  if (!btn.dataset.cancelling) { btn.disabled = false; if (btn._disarm) btn._disarm(); }
}

async function doCancel() {
  if (!state.activeRunId) return;
  const btn = $("#cancel-btn");
  btn.dataset.cancelling = "1";
  btn.disabled = true;
  btn.textContent = "cancelling…";
  try { await jpost(`/api/runs/${state.activeRunId}/cancel`, {}); }
  catch (e) { appendConsole("! " + e.message); }
  setTimeout(() => { delete btn.dataset.cancelling; btn.textContent = "Cancel run"; }, 3000);
}

/* ============================================================ results + runs */
function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  const kb = n / 1024;
  return kb < 10 ? `${kb.toFixed(1)} kB` : kb < 1024 ? `${Math.round(kb)} kB` : `${(kb / 1024).toFixed(1)} MB`;
}
function relTime(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

async function loadReport() {
  let r;
  try { r = await api("/api/report"); } catch (_) { return; }
  state.lastReport = r;

  const s = r.summary || {};
  const bt = s.below_target || 0;
  const sum = $("#report-summary");
  sum.textContent = `${(s.total || 0).toLocaleString()} records tracked · `
    + (s.entities ? (bt ? `${bt} of ${s.entities} entities below target` : `all ${s.entities} entities on target`) : "no targets");
  sum.className = "report-summary" + (bt ? " short" : " ok");

  renderRunStatus(r.run_summary);

  const lr = r.last_run || {};
  $("#report").textContent = lr.text || "(no live seeding run yet)";
  $("#raw-report-summary").textContent = lr.generated_at
    ? `Raw report — ${new Date(lr.generated_at).toLocaleString()}`
    : "Raw report";

  renderOutputs();
}

// The one thing per-entity rows can't say: what a run couldn't even attempt
// (a whole layer dying before creating anything — see write_run_json), plus
// the aggregate failure picture. Per-entity created/failed deltas live on
// the Data targets table's "last run" column instead of duplicating them here.
function renderRunStatus(rs) {
  const box = $("#run-status");
  box.innerHTML = "";
  if (!rs) { box.hidden = true; return; }
  box.hidden = false;

  const clean = !rs.layer_failures.length && !rs.total_failed;
  box.className = "run-status " + (clean ? "ok" : "bad");

  const when = rs.generated_at ? relTime(Date.parse(rs.generated_at) / 1000) : "";
  const parts = [`Last run — ${when}`];
  for (const lf of rs.layer_failures) parts.push(`layer failed entirely: ${lf}`);
  if (rs.total_failed) {
    parts.push(`${rs.total_failed.toLocaleString()} record${rs.total_failed === 1 ? "" : "s"} failed`
      + ` across ${rs.entities_failed} entit${rs.entities_failed === 1 ? "y" : "ies"}`);
  }
  if (!clean && rs.top_failure_reasons.length) {
    parts.push("most common: " + rs.top_failure_reasons.map(([reason, n]) => `${reason} ×${n}`).join(" · "));
  }
  if (clean) parts.push(`${rs.total_created.toLocaleString()} created, no failures`);

  box.append(icon(clean ? "check" : "alert"), el("span", { text: parts.join(" · ") }));
}

function renderOutputs() {
  const r = state.lastReport || {};
  const list = r.outputs || [];
  const os = r.outputs_summary || { files: list.length, with_rows: list.filter(o => o.rows > 0).length };
  $("#outputs-summary").textContent =
    list.length ? `Exports — ${os.with_rows} of ${os.files} CSVs have rows` : "Exports";
  $("#outputs-zip").hidden = !list.length;

  const showEmpty = $("#outputs-empty").checked;
  const shown = list.filter(o => showEmpty || o.rows > 0);
  const t = $("#outputs");
  t.innerHTML = "";
  if (!shown.length) {
    t.append(el("tr", {}, el("td", { class: "empty", colspan: "4",
      text: list.length ? "All exports are empty — nothing seeded yet." : "No CSVs exported yet." })));
    return;
  }
  for (const o of shown) {
    t.append(el("tr", { class: o.rows > 0 ? "" : "dim" },
      el("td", {}, el("a", { href: `/api/output/${o.name}` }, o.name.replace(/\.csv$/, ""))),
      el("td", { class: "num", text: o.rows > 0 ? `${o.rows.toLocaleString()}` : "—" }),
      el("td", { class: "num muted", text: fmtBytes(o.size) }),
      el("td", { class: "muted", text: relTime(o.mtime) })));
  }
}

function fmtDur(r) {
  if (r.status === "running") return "…";
  if (!r.started_at || !r.ended_at) return "";
  const s = Math.max(0, Math.round(r.ended_at - r.started_at));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
}
function runTag(r) {
  const a = r.argv_display || "";
  if (/--dry-run/.test(a)) return "dry";
  if (/\s--live\b/.test(a)) return "live";
  return "";
}
const RUN_ICON = { succeeded: "check", failed: "x", error: "x", running: "refresh" };

async function loadRuns() {
  let rows;
  try { rows = await api("/api/runs?limit=40"); } catch (_) { return; }
  const tb = $("#runs-body");
  tb.innerHTML = "";
  if (!rows.length) {
    tb.append(el("tr", {}, el("td", { colspan: "5", class: "empty", text: "No runs yet." })));
    return;
  }
  for (const r of rows) {
    const tag = runTag(r);
    const tr = el("tr", { role: "button", tabindex: "0", title: r.argv_display || r.argv || "" },
      el("td", {}, el("span", { class: "st " + r.status },
        icon(RUN_ICON[r.status] || "dot"), el("span", { text: r.status }))),
      el("td", {}, el("span", { text: r.command }),
        tag ? el("span", { class: "run-tag " + tag, text: tag }) : null),
      el("td", { class: "muted", text: r.started_at ? new Date(r.started_at * 1000).toLocaleTimeString() : "" }),
      el("td", { class: "muted", text: fmtDur(r) }),
      el("td", { class: "muted", text: r.exit_code == null ? "" : "exit " + r.exit_code }));
    const open = () => {
      if (r.status === "running") { attachStream(r.run_id, true); return; }
      if (state.stream && !confirm("Replace the live console with this run's saved log?")) return;
      openLog(r.run_id);
    };
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    tb.append(tr);
  }
}

async function openLog(runId) {
  try {
    const text = await (await fetch(`/api/runs/${runId}/log`)).text();
    resetConsole();
    $("#console").textContent = text;
    setBadge("saved log");
    $("#console").scrollIntoView({ block: "nearest" });
  } catch (_) {}
}

/* ============================================================ helpers */
function bizName(id) { const b = state.businesses.find(x => x.id === id); return b ? b.name : null; }
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
}

/* ============================================================ go */
function restoreState() {
  const saved = store.get("guided");
  if (saved && typeof saved === "object") state.guided.values = saved;
  const skip = store.get("skipLayers");
  if (Array.isArray(skip)) state.skipLayers = skip.filter(n => Number.isInteger(n));
}

wireStatic();
restoreState();
refreshStatus();
setInterval(refreshStatus, 3000);
