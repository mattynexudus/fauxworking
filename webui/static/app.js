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

// Two-click confirm on a button, auto-disarming after `ms`. Replaces native confirm().
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

/* ============================================================ state */
const HEADLINE_VOLUMES = ["coworkers", "bookings_total", "check_ins", "visitors"];

const state = {
  connOk: true,
  auth: false,
  groups: [],
  commands: [],
  byId: {},
  wizard: null,
  headlineKeys: HEADLINE_VOLUMES,
  bizMode: "none",
  businesses: [],
  businessId: null,
  bizError: null,
  activeRunId: null,
  stream: null,
  streamingRunId: null,
  streamErrCount: 0,
  lastRun: null,          // {command, dryRun, status}
  lastReport: null,       // last /api/report payload (for the "show empty" toggle)
  plan: null,             // {seed, counts, seeded}
  volCells: {},           // key -> {live, plan, row, input}
  argvCache: { key: null, display: "" },
  pollTick: 0,
  ticker: null,           // 1s elapsed ticker while a run is active
  guided: { values: {} },
};

/* ============================================================ boot */
async function loadAll() {
  await loadBusinesses(false);
  if (!(await loadCommands())) return;
  await loadPlan();
  renderVolumesPanel();
  renderGuided();
  renderGroups();
  await loadReport();
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
  for (const id of ["groups", "volumes-body", "step-body", "console", "report", "report-table",
                    "report-summary", "last-run", "runs-body", "outputs", "outputs-summary"]) {
    const n = document.getElementById(id);
    if (n) n.textContent = "";
  }
  for (const id of ["status-badge", "active-run", "run-hint", "loc"]) {
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
  loadReport(); loadRuns();
  loadPlan().then(() => { syncVolumesPanel(); updateGuidedCard(); });
}

/* ============================================================ header */
function wireStatic() {
  $("#login-form").addEventListener("submit", onLogin);
  armable($("#signout-btn"), { armLabel: "Sign out — CLI too?", run: doSignOut });
  armable($("#cancel-btn"), { armLabel: "Really stop it?", run: doCancel });
  $("#results-refresh").addEventListener("click", loadReport);
  $("#runs-refresh").addEventListener("click", loadRuns);
  $("#outputs-empty").addEventListener("change", renderOutputs);

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
        updateGuidedCard();
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
    state.groups = data.groups || [];
    state.commands = data.commands || [];
    state.byId = Object.fromEntries(state.commands.map(c => [c.id, c]));
    state.wizard = state.byId.wizard || null;
    if (data.headline_volume_keys) state.headlineKeys = data.headline_volume_keys;
    return true;
  } catch (e) {
    for (const id of ["volumes-body", "step-body", "groups"]) $(`#${id}`).innerHTML = "";
    $("#step-body").append(
      el("p", { class: "err small", text: (e.offline ? "Server unreachable." : e.message) + " " }),
      el("button", { class: "btn small", onclick: () => loadAll() }, "Retry"));
    return false;
  }
}

function volumeParams() {
  return (state.wizard ? state.wizard.params : [])
    .filter(p => p.type === "int" && !["seed", "layer"].includes(p.name));
}

/* ---------- shared field rendering ---------- */
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

/* ============================================================ data volumes (standalone) */
function gVal(name, dflt) { const v = state.guided.values[name]; return v != null ? v : dflt; }
function gSet(name, v) { state.guided.values[name] = v; store.set("guided", state.guided.values); }
function planCount(k) { return state.plan && state.plan.counts ? state.plan.counts[k] : undefined; }
function planSeeded(k) { return (state.plan && state.plan.seeded ? state.plan.seeded[k] : 0) || 0; }

function renderVolumesPanel() {
  const body = $("#volumes-body");
  if (!body) return;
  body.innerHTML = "";
  state.volCells = {};
  if (!state.auth || !state.wizard) return;

  const table = (rows) => {
    const t = el("table", { class: "vol-table" });
    t.append(el("tr", {},
      el("th", { text: "" }), el("th", { text: "seeded" }), el("th", { text: "target" })));
    for (const p of rows) t.append(volumeRow(p));
    return t;
  };
  const params = Object.fromEntries(volumeParams().map(p => [p.name, p]));
  body.append(table(state.headlineKeys.map(k => params[k]).filter(Boolean)));

  const rest = volumeParams().filter(p => !state.headlineKeys.includes(p.name));
  const seedP = state.wizard.params.find(p => p.name === "seed");
  const more = el("details", { class: "more" });
  more.append(el("summary", { text: `${rest.length} more entities` }));
  more.append(table(rest));
  body.append(more);

  if (seedP) {
    const f = renderField(seedP, gVal("seed"), v => { gSet("seed", v); syncVolumesPanel(); updateGuidedCard(); });
    const warn = el("div", { class: "warn-hint", id: "seed-warn", hidden: true });
    f.wrap.append(warn);
    body.append(f.wrap);
  }
  body.append(el("p", { class: "muted small",
    text: "Raising a target adds that many on the next run — it never rewrites or removes "
        + "existing records. Lower one via teardown." }));
  syncVolumesPanel();
}

function volumeRow(p) {
  const stored = state.guided.values[p.name];
  const start = (stored != null && stored !== "") ? stored
    : (planCount(p.name) != null ? planCount(p.name) : p.default);
  const input = el("input", { type: "number", min: p.min != null ? p.min : 0, value: start });
  if (p.max != null) input.max = p.max;
  input.addEventListener("input", () => {
    gSet(p.name, input.value === "" ? undefined : input.value);
    syncVolumesPanel();
    updateGuidedCard();
  });
  const live = el("td", { class: "vol-num" });
  const row = el("tr", { class: "vol-row" },
    el("td", { class: "vol-label", text: p.label }), live, el("td", {}, input));
  state.volCells[p.name] = { live, row, input, p };
  return row;
}

// in-place update: seeded counts + soft-max / seed / cap warnings, no rebuild
function syncVolumesPanel() {
  for (const [key, c] of Object.entries(state.volCells)) {
    const seeded = planSeeded(key), gen = planCount(key);
    c.live.textContent = seeded ? String(seeded) : (gen != null ? String(gen) : "—");
    const val = c.input.value === "" ? null : Number(c.input.value);
    const over = val != null && ((c.p.max != null && val > c.p.max) ||
      (c.p.soft_max != null && val > c.p.soft_max));
    c.row.classList.toggle("warn", !!over);
    c.input.classList.toggle("bad", c.p.max != null && val != null && val > c.p.max);
  }
  const sw = $("#seed-warn");
  if (sw) {
    const s = gVal("seed"), manifest = state.plan && state.plan.seed;
    const changed = s != null && s !== "" && manifest != null && Number(s) !== Number(manifest);
    sw.hidden = !changed;
    sw.textContent = changed
      ? "Seed differs from the last run — this forces a full rebuild and diverges from what's seeded live."
      : "";
  }
}

/* ============================================================ guided flow (single card) */
function renderGuided() {
  const body = $("#step-body");
  if (!body) return;
  body.innerHTML = "";
  if (!state.auth || !state.wizard) return;

  body.append(el("p", { class: "muted small",
    text: "Regenerates data/*.json from the Data volumes panel (incrementally), then seeds it." }));

  const layerP = state.wizard.params.find(p => p.name === "layer");
  body.append(renderField(layerP, gVal("layer", layerP.default),
    v => { gSet("layer", v); updateGuidedCard(); }).wrap);

  const exportP = state.wizard.params.find(p => p.name === "export_csv");
  body.append(renderField(exportP, gVal("export_csv", true),
    v => { gSet("export_csv", v); updateGuidedCard(); }).wrap);

  const freshP = state.wizard.params.find(p => p.name === "fresh");
  body.append(renderField(freshP, gVal("fresh", false),
    v => { gSet("fresh", v); updateGuidedCard(); }).wrap);
  body.append(el("div", { class: "warn-hint", id: "guided-fresh-warn", hidden: true,
    text: "Rewrites every data file from scratch — diverges from what's already seeded live." }));

  body.append(el("p", { class: "small muted", id: "guided-biz" }));
  body.append(el("p", { class: "review-summary", id: "guided-echo" }));
  body.append(el("p", { class: "small muted", id: "guided-delta" }));
  body.append(el("pre", { class: "review-cmd", id: "guided-cmd", text: "…" }));
  body.append(el("div", { class: "card-err", id: "guided-err" }));

  const actions = el("div", { class: "step-actions" });
  const preview = el("button", { class: "btn primary", id: "guided-preview",
    onclick: () => runGuided(true) }, "Preview (dry run)");
  const live = armable(el("button", { class: "btn danger", id: "guided-live" }, "Run for real"),
    { armLabel: "Creates real records across every layer — click to confirm",
      run: () => runGuided(false), ms: 5000 });
  actions.append(el("span", { class: "spacer" }), preview, live);
  body.append(actions);
  body.append(el("div", { class: "go-live-prompt", id: "guided-golive", hidden: true }));

  updateGuidedCard();
}

function guidedParams() {
  const p = {};
  for (const vp of volumeParams()) {
    const v = state.guided.values[vp.name];
    if (v != null && v !== "") p[vp.name] = v;
  }
  for (const k of ["seed", "layer"]) {
    const v = state.guided.values[k];
    if (v != null && v !== "") p[k] = v;
  }
  p.export_csv = gVal("export_csv", true) !== false;
  if (gVal("fresh", false) === true) p.fresh = true;
  return p;
}

function updateGuidedCard() {
  if (!$("#guided-echo")) return;
  const fresh = gVal("fresh", false) === true;
  const seedChanged = (() => {
    const s = gVal("seed"), m = state.plan && state.plan.seed;
    return s != null && s !== "" && m != null && Number(s) !== Number(m);
  })();

  $("#guided-fresh-warn").hidden = !(fresh || seedChanged);
  if (seedChanged && !fresh)
    $("#guided-fresh-warn").textContent =
      "Seed changed — this run rebuilds every data file from scratch.";

  // location line
  const biz = $("#guided-biz");
  if (state.bizMode === "multi" && !state.businessId) {
    biz.className = "err small"; biz.textContent = "Pick a location in the header to enable the run.";
  } else {
    biz.className = "small muted";
    const name = state.bizMode === "none" ? "the account's business"
      : bizName(state.businessId) || (state.businesses[0] && state.businesses[0].name) || "—";
    biz.textContent = `Seeds into ${name}.`;
  }

  // targets echo + delta
  const labelOf = k => (state.byId.wizard.params.find(p => p.name === k) || {}).label || k;
  const echo = state.headlineKeys.map(k => `${gVal(k, planCount(k) ?? "?")} ${labelOf(k).toLowerCase()}`);
  const extra = volumeParams().length - state.headlineKeys.length;
  $("#guided-echo").textContent = `Targets: ${echo.join(" · ")}${extra > 0 ? ` · +${extra} more` : ""}`;

  let delta;
  if (fresh || seedChanged) delta = "Rebuilds every data file from scratch.";
  else {
    const parts = state.headlineKeys.map(k => {
      const d = Number(gVal(k, planCount(k) ?? 0)) - Number(planCount(k) ?? 0);
      return d > 0 ? `+${d} ${labelOf(k).toLowerCase()}` : null;
    }).filter(Boolean);
    delta = parts.length ? `Adds ${parts.join(", ")}.`
      : "No target raised — regeneration is skipped, it just seeds the current plan.";
  }
  const layerTxt = gVal("layer") ? `layers 0–${gVal("layer")}` : "every layer";
  $("#guided-delta").textContent = `${delta} Then seeds ${layerTxt}.`;

  // equivalent command (debounced)
  const params = guidedParams();
  const key = JSON.stringify([params, state.businessId]);
  const cmd = $("#guided-cmd");
  if (key === state.argvCache.key) { cmd.textContent = state.argvCache.display; }
  else {
    cmd.textContent = state.argvCache.display || "…";
    jpost("/api/argv", { command: "wizard", params, business_id: state.businessId, dry_run: true })
      .then(r => {
        state.argvCache = { key, display: r.display };
        if ($("#guided-cmd")) { $("#guided-cmd").textContent = r.display; $("#guided-err").textContent = ""; }
      })
      .catch(e => { if ($("#guided-err")) $("#guided-err").textContent = e.offline ? "" : e.message; });
  }

  // run buttons
  const blocked = runBlockedReason({ accepts_business_id: true });
  for (const b of [$("#guided-preview"), $("#guided-live")]) {
    if (!b) continue;
    b.disabled = !!blocked;
    b.title = blocked || "";
    if (blocked && b._disarm) b._disarm();
  }

  // "run for real?" nudge after a successful preview
  const gl = $("#guided-golive");
  const show = state.lastRun && state.lastRun.command === "wizard" &&
    state.lastRun.dryRun && state.lastRun.status === "succeeded";
  gl.hidden = !show;
  if (show && !gl.dataset.built) {
    gl.dataset.built = "1";
    gl.append(el("span", { text: "That was a preview. Run it for real now?" }),
      armable(el("button", { class: "btn danger" }, "Run for real"),
        { armLabel: "Creates real records — click to confirm", run: () => runGuided(false), ms: 5000 }));
  }
}

function updateAllRunStates() {
  $$("#groups .card").forEach(updateCardRunState);
  updateGuidedCard();
}

async function runGuided(dryRun) {
  await startRun({ command: "wizard", params: guidedParams(),
    business_id: state.businessId, dry_run: dryRun }, $("#guided-err"));
}

/* ============================================================ command groups */
function renderGroups() {
  if (!state.auth) return;
  const wrap = $("#groups");
  wrap.innerHTML = "";
  const listed = state.commands.filter(c => !c.guided_only && !c.hidden);
  $("#commands-wrap > summary").textContent = `Individual commands (${listed.length})`;

  let n = 0;
  for (const g of state.groups) {
    const cmds = listed.filter(c => c.group === g.id);
    if (!cmds.length) continue;
    const sec = el("section", { class: "group tone-" + g.tone });
    sec.append(el("div", { class: "group-head" },
      el("span", { class: "gnum", text: ["①", "②", "③", "④", "⑤"][n] || String(n + 1) }),
      el("h3", { text: g.label }),
      el("span", { class: "gblurb", text: g.blurb })));
    for (const c of cmds) sec.append(renderCard(c));
    wrap.append(sec);
    n++;
  }
}

function renderCard(c) {
  const card = el("section", { class: "card tone-" + c.tone, "data-id": c.id });
  const getters = {};

  card.append(el("div", { class: "card-head" },
    el("h4", { text: c.label }),
    el("span", { class: "badge " + c.tone, text: c.tone })));
  card.append(el("p", { class: "desc", text: c.description }));
  if (c.notes) card.append(el("div", { class: "note", text: c.notes }));

  if (c.params.length) {
    const fields = el("div", { class: "fields" });
    for (const p of c.params) {
      const f = renderField(p, undefined, () => {});
      getters[p.name] = f.get;
      if (p.name === "mode") f.input.addEventListener("change", () => { disarmCard(card); updateCardRunState(card); });
      fields.append(f.wrap);
    }
    card.append(fields);
  }

  const row = el("div", { class: "run-row" });
  let dryToggle = null, modeBadge = null;
  if (c.offers_dry_run) {
    dryToggle = el("input", { type: "checkbox" });
    dryToggle.checked = true;
    modeBadge = el("span", { class: "mode-badge preview", text: "preview" });
    dryToggle.addEventListener("change", () => { disarmCard(card); updateCardRunState(card); });
    row.append(el("label", { class: "dry-toggle" }, dryToggle, " dry run"), modeBadge);
  }

  let confirmInput = null, confirmWrap = null;
  if (c.confirm_phrase) {
    confirmInput = el("input", { placeholder: c.confirm_phrase, "aria-label": "confirmation phrase" });
    confirmInput.addEventListener("input", () => updateCardRunState(card));
    confirmWrap = el("span", { class: "confirm-inline" }, "confirm:", confirmInput);
    row.append(confirmWrap);
  }

  const runBtn = el("button", { class: "btn primary" }, "Run");
  runBtn.addEventListener("click", () => onCardRun(c, card));
  row.append(runBtn);
  const reason = el("span", { class: "run-reason" });
  row.append(reason);
  card.append(row);
  const cleanNote = el("div", { class: "run-hint", hidden: true,
    text: "Clean mode can only be previewed — dry run is forced on." });
  card.append(cleanNote);
  const cardErr = el("div", { class: "card-err" });
  card.append(cardErr);

  card._ctl = { getters, dryToggle, modeBadge, confirmInput, confirmWrap, cleanNote, runBtn, reason, cardErr };
  card._armed = false;
  card._armTimer = null;
  updateCardRunState(card);
  return card;
}

function gatherCardParams(card) {
  const p = {};
  for (const [name, get] of Object.entries(card._ctl.getters)) {
    const v = get();
    if (v !== undefined && v !== "") p[name] = v;
  }
  return p;
}

function runBlockedReason(c) {
  if (state.activeRunId) return "A run is in progress — wait for it to finish.";
  if (c.accepts_business_id && state.bizMode === "multi" && !state.businessId)
    return "Pick a location in the header first.";
  return null;
}

function disarmCard(card) {
  card._armed = false;
  if (card._armTimer) { clearTimeout(card._armTimer); card._armTimer = null; }
}

function updateCardRunState(card) {
  const c = state.byId[card.dataset.id];
  const { getters, dryToggle, modeBadge, confirmInput, confirmWrap, cleanNote, runBtn, reason } = card._ctl;

  const mode = getters.mode ? getters.mode() : null;
  const cleanLocked = c.id === "teardown" && mode === "clean";
  if (dryToggle) {
    if (cleanLocked) { dryToggle.checked = true; dryToggle.disabled = true; }
    else dryToggle.disabled = false;
  }
  if (cleanNote) cleanNote.hidden = !cleanLocked;

  const dry = dryToggle ? dryToggle.checked : false;
  if (modeBadge) {
    modeBadge.className = "mode-badge " + (dry ? "preview" : "livewrite");
    modeBadge.textContent = dry ? "preview · nothing created" : "writes to live account";
  }
  if (confirmWrap) confirmWrap.hidden = !(c.confirm_phrase && !dry);

  let blocked = runBlockedReason(c);
  if (!blocked && c.confirm_phrase && !dry &&
      (!confirmInput || confirmInput.value !== c.confirm_phrase))
    blocked = `Type "${c.confirm_phrase}" to enable the live delete.`;

  runBtn.disabled = !!blocked;
  runBtn.title = blocked || "";
  reason.textContent = blocked || "";
  if (blocked) disarmCard(card);

  if (!blocked && card._armed) {
    runBtn.textContent = "Click again to create real records";
    runBtn.classList.add("armed");
  } else {
    runBtn.textContent = "Run";
    runBtn.classList.remove("armed");
  }
}

async function onCardRun(c, card) {
  const { dryToggle, confirmInput, cardErr } = card._ctl;
  cardErr.textContent = "";
  const dry = dryToggle ? dryToggle.checked : false;

  // two-stage confirm for a live write that isn't already phrase-gated
  if (c.offers_dry_run && !dry && c.writes_live && !c.confirm_phrase && !card._armed) {
    card._armed = true;
    card._armTimer = setTimeout(() => { disarmCard(card); updateCardRunState(card); }, 4000);
    updateCardRunState(card);
    return;
  }
  disarmCard(card);
  updateCardRunState(card);

  await startRun({
    command: c.id,
    params: gatherCardParams(card),
    business_id: c.accepts_business_id ? state.businessId : null,
    dry_run: dry,
    confirm: confirmInput ? confirmInput.value : undefined,
  }, cardErr);
  if (confirmInput) confirmInput.value = "";
}

/* ============================================================ run + stream */
async function startRun(payload, errSlot) {
  const showErr = (msg) => { if (errSlot) errSlot.textContent = msg; };
  try {
    const r = await jpost("/api/run", payload);
    state.lastRun = { command: payload.command, dryRun: !!payload.dry_run, status: "running" };
    $("#run-hint").hidden = true;
    attachStream(r.run_id, true);
    $("#console").scrollIntoView({ block: "nearest", behavior: "smooth" });
    refreshStatus();
  } catch (e) {
    if (e.offline) return showErr("Server unreachable.");
    if (e.status === 409) return showErr((e.body && e.body.error) || "A run is already in progress.");
    showErr(e.message);
  }
}

function attachStream(runId, clear) {
  if (state.stream) { state.stream.close(); state.stream = null; }
  if (clear) { $("#console").textContent = ""; $("#status-badge").hidden = true; $("#run-hint").hidden = true; }
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
    try { $("#console").textContent = await (await fetch(`/api/runs/${runId}/log`)).text(); }
    catch (_) {}
  }
}

function onRunEnd(info) {
  stopTicker();
  setBadge(info.status || "done", info.exit_code);
  setTitle(info.status === "succeeded" ? "✓ done" : "✗ " + (info.status || "failed"));
  if (state.lastRun) state.lastRun.status = info.status;

  const hint = $("#run-hint");
  if (info.status === "failed" || info.status === "error") {
    hint.hidden = false;
    hint.className = "run-hint failed";
    let msg = `Exit code ${info.exit_code}. Re-running is safe — tracked records mean it resumes where it stopped.`;
    if (state.lastRun && ["wizard", "pipeline"].includes(state.lastRun.command))
      msg += " A layer may have failed, or it fell short of target — see the Results panel below.";
    hint.textContent = msg;
  } else {
    hint.hidden = true;
  }

  loadReport();
  loadRuns();
  loadPlan().then(() => { if (!$("#main").hidden) { syncVolumesPanel(); updateGuidedCard(); } });
  refreshStatus();
}

function appendConsole(text) {
  const c = $("#console");
  c.textContent += text + "\n";
  if ($("#follow").checked) c.scrollTop = c.scrollHeight;
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
  state.ticker = setInterval(() => {
    const t = $("#active-run-text");
    if (!t || $("#active-run").hidden) return;
    const m = /·\s(\d+)s$/.exec(t.textContent);
    if (m) t.textContent = t.textContent.replace(/·\s\d+s$/, `· ${Number(m[1]) + 1}s`);
  }, 1000);
}
function stopTicker() { if (state.ticker) { clearInterval(state.ticker); state.ticker = null; } }

function renderActiveRun(run) {
  const bar = $("#active-run");
  if (!run) { bar.hidden = true; stopTicker(); return; }
  bar.hidden = false;
  const secs = Math.max(0, Math.round(Date.now() / 1000 - run.started_at));
  $("#active-run-text").textContent = `Running ${run.command} · ${secs}s`;
  const btn = $("#cancel-btn");
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
  setTimeout(() => { delete btn.dataset.cancelling; btn.textContent = "Cancel"; }, 3000);
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

  // cumulative "what's in the account"
  const s = r.summary || {};
  const bt = s.below_target || 0;
  const sum = $("#report-summary");
  sum.textContent = `${(s.total || 0).toLocaleString()} records tracked · `
    + (s.entities ? (bt ? `${bt} of ${s.entities} entities below target` : `all ${s.entities} entities on target`) : "no targets");
  sum.className = "report-summary" + (bt ? " short" : " ok");

  const rt = $("#report-table");
  rt.innerHTML = "";
  const rows = r.report || [];
  if (!rows.length) {
    rt.append(el("tr", {}, el("td", { class: "empty", colspan: "3", text: "Nothing seeded live yet." })));
  } else {
    rt.append(el("tr", {},
      el("th", { text: "Entity" }), el("th", { class: "num", text: "tracked" }), el("th", { class: "num", text: "target" })));
    for (const row of rows) {
      rt.append(el("tr", { class: row.short ? "short" : "" },
        el("td", { text: row.entity }),
        el("td", { class: "num", text: row.created.toLocaleString() }),
        el("td", { class: "num muted", text: row.target == null ? "—" : row.target.toLocaleString() })));
    }
  }
  $("#report").textContent = r.report_text || "";

  // last seeding run
  const lr = r.last_run || {};
  $("#last-run").textContent = lr.text || "(no live seeding run yet)";
  $("#lastrun-summary").textContent = lr.generated_at
    ? `Last seeding run — ${new Date(lr.generated_at).toLocaleString()}`
    : "Last seeding run";

  renderOutputs();
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
      el("td", { class: "st " + r.status, text: r.status }),
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
    $("#console").textContent = await (await fetch(`/api/runs/${runId}/log`)).text();
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
function restoreGuided() {
  const saved = store.get("guided");
  if (saved && typeof saved === "object") state.guided.values = saved;
}

wireStatic();
restoreGuided();
refreshStatus();
setInterval(refreshStatus, 3000);
