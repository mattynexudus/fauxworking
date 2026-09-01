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
  try {
    res = await fetch(path, opts);
  } catch (_) {
    setConn(false);
    const e = new Error("network");
    e.offline = true;
    throw e;
  }
  setConn(true);
  let body = null;
  try { body = await res.json(); } catch (_) {}
  if (!res.ok) {
    const e = new Error((body && body.error) || res.statusText);
    e.status = res.status;
    e.body = body;
    throw e;
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
  activeStartedAt: null,
  stream: null,
  streamingRunId: null,
  streamErrCount: 0,
  lastRun: null,          // {command, dryRun}
  plan: null,             // {seed, counts, seeded} from /api/plan
  guided: { step: 0, values: {} },
};

/* ============================================================ boot */
async function loadAll() {
  await loadBusinesses(false);
  await loadCommands();
  await loadPlan();
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

// Logged out: nothing but the login card. Tear down any live view and clear
// state so a session that expires mid-run leaves nothing stale behind #main.
function showLoggedOut() {
  if (state.stream) { state.stream.close(); state.stream = null; }
  state.streamingRunId = null;
  state.activeRunId = null;
  state.plan = null;
  state.commands = [];
  state.byId = {};
  for (const id of ["groups", "console", "report", "last-run", "runs-body"]) {
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
  try { s = await api("/api/status"); }
  catch (_) { return; }

  const wasAuth = state.auth;
  state.auth = s.authenticated;
  $("#auth-text").textContent = s.authenticated ? "signed in" : "signed out";
  $("#auth-pill").className = "pill " + (s.authenticated ? "ok" : "bad");
  $("#login").hidden = s.authenticated;
  $("#main").hidden = !s.authenticated;
  $("#signout-btn").hidden = !s.authenticated;

  if (!s.authenticated) { showLoggedOut(); return; }

  state.activeRunId = s.active_run ? s.active_run.run_id : null;
  state.activeStartedAt = s.active_run ? s.active_run.started_at : null;
  renderActiveRun(s.active_run);
  if (s.active_run && state.streamingRunId !== s.active_run.run_id) {
    attachStream(s.active_run.run_id, true);
  }
  updateAllRunStates();

  if (!wasAuth) loadAll();   // first sight of an authenticated session
}

/* ============================================================ header: location + sign out */
function wireStatic() {
  $("#login-form").addEventListener("submit", onLogin);
  $("#signout-btn").addEventListener("click", onSignOut);
  $("#results-refresh").addEventListener("click", loadReport);
  $("#runs-refresh").addEventListener("click", loadRuns);
  $("#cancel-btn").addEventListener("click", onCancel);
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

async function onSignOut() {
  if (!confirm("Sign out? This clears the token for the CLI too (same .env).")) return;
  try {
    await jpost("/api/auth/logout", {});
    location.reload();
  } catch (e) {
    alert(e.status === 409 ? e.message : "Sign out failed: " + e.message);
  }
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
        if (state.guided.step >= 1) renderGuided();
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
  const data = await api("/api/commands");
  state.groups = data.groups || [];
  state.commands = data.commands || [];
  state.byId = Object.fromEntries(state.commands.map(c => [c.id, c]));
  state.wizard = state.byId.wizard || null;
  if (data.headline_volume_keys) state.headlineKeys = data.headline_volume_keys;
}

function volumeParams() {
  // wizard's int params minus seed/layer/export_csv == the 11 volume knobs
  return (state.wizard ? state.wizard.params : [])
    .filter(p => p.type === "int" && !["seed", "layer"].includes(p.name));
}

/* ---------- field rendering (shared) ---------- */
function renderField(p, value, onInput) {
  const wrap = el("div", { class: "field" + (p.type === "bool" ? " check" : "") });
  const inputId = "f-" + Math.random().toString(36).slice(2, 8);
  let input;

  if (p.type === "bool") {
    input = el("input", { type: "checkbox", id: inputId });
    input.checked = value != null ? !!value : !!p.default;
    input.addEventListener("change", () => onInput(input.checked));
    wrap.append(input, el("label", { for: inputId, text: p.label }));
    if (p.help) wrap.append(el("span", { class: "hint", text: p.help }));
    return { wrap, input, get: () => input.checked };
  }

  wrap.append(el("label", { for: inputId, text: p.label }));
  if (p.type === "choice") {
    input = el("select", { id: inputId });
    for (const [v, lbl] of p.choices || []) {
      const o = el("option", { value: String(v) }, lbl);
      if (String(v) === String(value != null ? value : p.default)) o.selected = true;
      input.append(o);
    }
  } else {
    input = el("input", {
      id: inputId,
      type: p.type === "date" ? "date" : "number",
      value: value != null ? value : (p.default != null ? p.default : ""),
    });
    if (p.min != null) input.min = p.min;
    if (p.max != null) input.max = p.max;
  }
  input.addEventListener("input", () => { clearFieldErr(wrap); onInput(readInput(input, p)); });
  input.addEventListener("change", () => onInput(readInput(input, p)));
  wrap.append(input);
  if (p.help) wrap.append(el("span", { class: "hint", text: p.help }));
  return { wrap, input, get: () => readInput(input, p) };
}

function readInput(input, p) {
  if (p.type === "bool") return input.checked;
  const v = input.value;
  return v === "" ? undefined : v;
}
function setFieldErr(wrap, msg) {
  clearFieldErr(wrap);
  wrap.classList.add("bad");
  wrap.append(el("span", { class: "field-err", text: msg }));
}
function clearFieldErr(wrap) {
  wrap.classList.remove("bad");
  $$(".field-err", wrap).forEach(n => n.remove());
}

/* ============================================================ guided flow */
const STEPS = ["Volumes", "Options", "Review & run"];

function renderGuided() {
  if (!state.auth || !state.wizard) return;
  renderStepper();
  const body = $("#step-body");
  body.innerHTML = "";
  ({ 0: guidedStepVolumes, 1: guidedStepOptions, 2: guidedStepReview }[state.guided.step])(body);
}

function renderStepper() {
  const sp = $("#stepper");
  sp.innerHTML = "";
  STEPS.forEach((label, i) => {
    if (i) sp.append(el("span", { class: "bar" }));
    const cls = i === state.guided.step ? "step active" : i < state.guided.step ? "step done" : "step";
    sp.append(el("span", { class: cls },
      el("span", { class: "num", text: i < state.guided.step ? "✓" : String(i + 1) }),
      el("span", { text: label })));
  });
}

function gVal(name, dflt) {
  const v = state.guided.values[name];
  return v != null ? v : dflt;
}
function gSet(name, v) {
  state.guided.values[name] = v;
  store.set("guided", state.guided.values);
}

function planCount(key) {
  return state.plan && state.plan.counts ? state.plan.counts[key] : undefined;
}
function planSeeded(key) {
  return (state.plan && state.plan.seeded ? state.plan.seeded[key] : 0) || 0;
}
function volumeField(p) {
  // default = the user's stored value, else the current generated count, else the registry default
  const stored = state.guided.values[p.name];
  const start = (stored != null && stored !== "") ? stored
    : (planCount(p.name) != null ? planCount(p.name) : p.default);
  const f = renderField(p, start, v => gSet(p.name, v));
  const seeded = planSeeded(p.name), gen = planCount(p.name);
  if (seeded || gen != null) {
    f.wrap.append(el("span", { class: "hint",
      text: `${seeded} seeded live${gen != null ? ` · ${gen} in the plan` : ""} — a run only adds new ones` }));
  }
  return f.wrap;
}

function guidedStepVolumes(body) {
  body.append(el("h3", { text: "1 · How much data?" }));
  body.append(el("p", { class: "muted small",
    text: "Regenerates data/*.json locally (incrementally — existing records are kept), then seeds it." }));

  const params = Object.fromEntries(volumeParams().map(p => [p.name, p]));
  for (const key of state.headlineKeys) {
    if (!params[key]) continue;
    body.append(volumeField(params[key]));
  }

  const more = el("details", { class: "more" });
  more.append(el("summary", { text: `${volumeParams().length - state.headlineKeys.length + 1} more settings` }));
  for (const p of volumeParams()) {
    if (state.headlineKeys.includes(p.name)) continue;
    more.append(volumeField(p));
  }
  const seedP = state.wizard.params.find(p => p.name === "seed");
  if (seedP) more.append(renderField(seedP, gVal("seed"), v => gSet("seed", v)).wrap);
  body.append(more);

  const actions = el("div", { class: "step-actions" });
  actions.append(el("span", { class: "spacer" }),
    el("button", { class: "btn primary", onclick: () => { state.guided.step = 1; renderGuided(); } }, "Next →"));
  body.append(actions);
}

function guidedStepOptions(body) {
  body.append(el("h3", { text: "2 · Options" }));

  const everything = gVal("everything", true);
  const everWrap = el("div", { class: "field check" });
  const everCb = el("input", { type: "checkbox", id: "g-everything" });
  everCb.checked = everything !== false;
  everCb.addEventListener("change", () => { gSet("everything", everCb.checked); renderGuided(); });
  everWrap.append(everCb, el("label", { for: "g-everything", text: "Generate everything (recommended)" }));
  body.append(everWrap);

  if (everCb.checked === false) {
    const layerP = state.wizard.params.find(p => p.name === "layer");
    body.append(renderField(layerP, gVal("layer", layerP.default), v => gSet("layer", v)).wrap);
  }

  const exportP = state.wizard.params.find(p => p.name === "export_csv");
  body.append(renderField(exportP, gVal("export_csv", true), v => gSet("export_csv", v)).wrap);

  const freshP = state.wizard.params.find(p => p.name === "fresh");
  if (freshP) body.append(renderField(freshP, gVal("fresh", false), v => gSet("fresh", v)).wrap);

  if (state.bizMode === "multi") {
    body.append(state.businessId
      ? el("p", { class: "small muted", html: "Will seed into <strong>" +
          escapeHtml(bizName(state.businessId)) + "</strong>." })
      : el("p", { class: "err small", text: "Pick a location in the header above to continue." }));
  } else if (state.bizMode === "single") {
    body.append(el("p", { class: "small muted", html: "Will seed into <strong>" +
      escapeHtml(state.businesses[0].name) + "</strong>." }));
  }

  const actions = el("div", { class: "step-actions" });
  const next = el("button", { class: "btn primary",
    onclick: () => { state.guided.step = 2; renderGuided(); } }, "Next →");
  if (state.bizMode === "multi" && !state.businessId) next.disabled = true;
  actions.append(
    el("button", { class: "btn", onclick: () => { state.guided.step = 0; renderGuided(); } }, "← Back"),
    el("span", { class: "spacer" }), next);
  body.append(actions);
}

function guidedParams() {
  const p = {};
  for (const vp of volumeParams()) {
    const v = state.guided.values[vp.name];
    if (v != null && v !== "") p[vp.name] = v;
  }
  const seed = state.guided.values.seed;
  if (seed != null && seed !== "") p.seed = seed;
  p.export_csv = gVal("export_csv", true) !== false;
  if (gVal("fresh", false) === true) p.fresh = true;
  if (gVal("everything", true) === false) p.layer = gVal("layer", state.wizard.params.find(x => x.name === "layer").default);
  return p;
}

async function guidedStepReview(body) {
  body.append(el("h3", { text: "3 · Review & run" }));

  const params = guidedParams();
  const loc = state.bizMode === "none" ? "the account's business" : bizName(state.businessId) || "—";
  const layerTxt = gVal("everything", true) === false ? `layers 0–${gVal("layer")}` : "every layer";

  let dataTxt;
  if (gVal("fresh", false) === true) {
    dataTxt = "Rebuild all local data from scratch";
  } else {
    const deltas = state.headlineKeys.map(k => {
      const want = Number(gVal(k, planCount(k) != null ? planCount(k) : 0));
      const have = Number(planCount(k) != null ? planCount(k) : 0);
      const d = want - have;
      return d > 0 ? `+${d} ${k.replace("_total", "").replace(/_/g, " ")}` : null;
    }).filter(Boolean);
    dataTxt = deltas.length ? `Add ${deltas.join(", ")}` : "Keep the existing local data";
  }
  body.append(el("p", { class: "review-summary",
    text: `${dataTxt}, then seed ${layerTxt} into ${loc}.` }));

  const cmdBox = el("pre", { class: "review-cmd", text: "building…" });
  body.append(cmdBox);
  const errBox = el("div", { class: "card-err" });
  body.append(errBox);
  try {
    const r = await jpost("/api/argv", {
      command: "wizard", params, business_id: state.businessId, dry_run: true,
    });
    cmdBox.textContent = r.display;
  } catch (e) {
    cmdBox.textContent = "(could not build command)";
    errBox.textContent = e.offline ? "Server unreachable." : e.message;
  }

  const actions = el("div", { class: "step-actions" });
  const preview = el("button", { class: "btn primary",
    onclick: () => runGuided(true) }, "Preview (dry run)");
  const live = liveButton(() => runGuided(false), "Run for real");
  actions.append(
    el("button", { class: "btn", onclick: () => { state.guided.step = 1; renderGuided(); } }, "← Back"),
    el("span", { class: "spacer" }), preview, live);
  body.append(actions);
  updateGuidedRunState(actions);

  if (state.lastRun && state.lastRun.command === "wizard" && state.lastRun.dryRun &&
      state.lastRun.status === "succeeded") {
    const p = el("div", { class: "go-live-prompt" });
    p.append(el("span", { text: "That was a preview. Run it for real now?" }),
      liveButton(() => runGuided(false), "Run for real"));
    body.append(p);
  }
}

function updateGuidedRunState(actions) {
  $$(".run-reason", actions).forEach(n => n.remove());
  const blocked = runBlockedReason({ accepts_business_id: true });
  $$("button", actions).forEach(b => {
    if (/^(←|Next)/.test(b.textContent)) return;  // navigation, not a run trigger
    b.disabled = !!blocked;
    b.title = blocked || "";
  });
  if (blocked) actions.append(el("span", { class: "run-reason", text: blocked }));
}

async function runGuided(dryRun) {
  await startRun({ command: "wizard", params: guidedParams(),
    business_id: state.businessId, dry_run: dryRun }, $("#step-body"));
}

/* ============================================================ command groups */
function renderGroups() {
  if (!state.auth) return;
  const wrap = $("#groups");
  wrap.innerHTML = "";
  const listed = state.commands.filter(c => !c.guided_only);
  $("#commands-wrap > summary").textContent = `Individual commands (${listed.length})`;

  state.groups.forEach((g, i) => {
    const cmds = listed.filter(c => c.group === g.id);
    if (!cmds.length) return;
    const sec = el("section", { class: "group tone-" + g.tone });
    sec.append(el("div", { class: "group-head" },
      el("span", { class: "gnum", text: ["①", "②", "③", "④", "⑤"][i] || String(i + 1) }),
      el("h3", { text: g.label }),
      el("span", { class: "gblurb", text: g.blurb })));
    for (const c of cmds) sec.append(renderCard(c));
    wrap.append(sec);
  });
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
      if (p.name === "mode") f.input.addEventListener("change", () => {
        card._armed = false;
        updateCardRunState(card);
      });
      fields.append(f.wrap);
    }
    card.append(fields);
  }

  const row = el("div", { class: "run-row" });

  let dryToggle = null, modeBadge = null;
  if (c.offers_dry_run) {
    dryToggle = el("input", { type: "checkbox" });
    dryToggle.checked = true; // default to preview, matching the CLI
    modeBadge = el("span", { class: "mode-badge preview", text: "preview" });
    dryToggle.addEventListener("change", () => {
      card._armed = false;
      updateCardRunState(card);
    });
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

function updateCardRunState(card) {
  const c = state.byId[card.dataset.id];
  const { getters, dryToggle, modeBadge, confirmInput, confirmWrap, cleanNote, runBtn, reason } = card._ctl;

  // clean-mode teardown: force dry-run on, it can never run live
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

  // the typed-confirm only matters for a live destructive run
  if (confirmWrap) confirmWrap.hidden = !(c.confirm_phrase && !dry);

  let blocked = runBlockedReason(c);
  if (!blocked && c.confirm_phrase && !dry &&
      (!confirmInput || confirmInput.value !== c.confirm_phrase))
    blocked = `Type "${c.confirm_phrase}" to enable the live delete.`;

  runBtn.disabled = !!blocked;
  runBtn.title = blocked || "";
  reason.textContent = blocked || "";

  if (!blocked && card._armed) {
    runBtn.textContent = "Confirm: create real records";
    runBtn.classList.add("danger");
  } else {
    runBtn.textContent = "Run";
    runBtn.classList.remove("danger");
  }
}

function updateAllRunStates() {
  $$("#groups .card").forEach(updateCardRunState);
  const actions = $("#step-body .step-actions");
  if (actions) updateGuidedRunState(actions);
}

async function onCardRun(c, card) {
  const { dryToggle, confirmInput, cardErr } = card._ctl;
  cardErr.textContent = "";
  const dry = dryToggle ? dryToggle.checked : false;

  // two-stage confirm for a live write that isn't already phrase-gated
  if (c.offers_dry_run && !dry && c.writes_live && !c.confirm_phrase && !card._armed) {
    card._armed = true;
    updateCardRunState(card);
    return;
  }
  card._armed = false;
  updateCardRunState(card);

  await startRun({
    command: c.id,
    params: gatherCardParams(card),
    business_id: c.accepts_business_id ? state.businessId : null,
    dry_run: dry,
    confirm: confirmInput ? confirmInput.value : undefined,
  }, card, cardErr);
  if (confirmInput) { confirmInput.value = ""; }
}

/* ============================================================ run + stream */
async function startRun(payload, errHost, errSlot) {
  const showErr = (msg) => {
    if (errSlot) errSlot.textContent = msg;
    else if (errHost) {
      let s = $(".card-err", errHost);
      if (!s) { s = el("div", { class: "card-err" }); errHost.append(s); }
      s.textContent = msg;
    }
  };
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
    appendConsole("(stream ended — loading the saved log)");
    try {
      const res = await fetch(`/api/runs/${runId}/log`);
      $("#console").textContent = await res.text();
    } catch (_) {}
  }
}

function onRunEnd(info) {
  setBadge(info.status || "done", info.exit_code);
  if (state.lastRun) state.lastRun.status = info.status;

  const hint = $("#run-hint");
  if (info.status === "failed" || info.status === "error") {
    hint.hidden = false;
    hint.className = "run-hint failed";
    let msg = `Exit code ${info.exit_code}. Re-running is safe — tracked records mean it resumes where it stopped.`;
    if (state.lastRun && ["wizard", "pipeline"].includes(state.lastRun.command))
      msg += " A whole layer may have failed, or it fell short of target — see Results / last-run-report.txt.";
    hint.textContent = msg;
  } else {
    hint.hidden = true;
  }

  loadReport();
  loadRuns();
  loadPlan().then(() => { if (!$("#main").hidden) renderGuided(); });
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

function renderActiveRun(run) {
  const bar = $("#active-run");
  if (!run) { bar.hidden = true; return; }
  bar.hidden = false;
  const secs = Math.max(0, Math.round(Date.now() / 1000 - run.started_at));
  $("#active-run-text").textContent = `Running ${run.command} · ${secs}s`;
  const btn = $("#cancel-btn");
  if (!btn.dataset.cancelling) { btn.disabled = false; btn.textContent = "Cancel"; }
}

async function onCancel() {
  if (!state.activeRunId) return;
  if (!confirm("Stop the current run? Partial data stays tracked; a re-run resumes.")) return;
  const btn = $("#cancel-btn");
  btn.dataset.cancelling = "1";
  btn.disabled = true;
  btn.textContent = "cancelling…";
  try { await jpost(`/api/runs/${state.activeRunId}/cancel`, {}); }
  catch (e) { appendConsole("! " + e.message); }
  setTimeout(() => { delete btn.dataset.cancelling; }, 2000);
}

/* ============================================================ results + runs */
async function loadReport() {
  let r;
  try { r = await api("/api/report"); } catch (_) { return; }
  $("#report").textContent = (r.report_lines || []).join("\n");
  $("#last-run").textContent = r.last_run_report || "(no live seeding run yet)";
  const ul = $("#outputs");
  ul.innerHTML = "";
  if (!(r.outputs || []).length) {
    ul.append(el("li", { class: "empty", text: "No CSVs exported yet." }));
    return;
  }
  for (const o of r.outputs) {
    ul.append(el("li", {},
      el("a", { href: `/api/output/${o.name}` }, o.name),
      el("span", { class: "muted small",
        text: `${(o.size / 1024).toFixed(1)} kB · ${new Date(o.mtime * 1000).toLocaleString()}` })));
  }
}

async function loadRuns() {
  let rows;
  try { rows = await api("/api/runs?limit=40"); } catch (_) { return; }
  const tb = $("#runs-body");
  tb.innerHTML = "";
  if (!rows.length) {
    tb.append(el("tr", {}, el("td", { colspan: "4", class: "empty", text: "No runs yet." })));
    return;
  }
  for (const r of rows) {
    const when = r.started_at ? new Date(r.started_at * 1000).toLocaleTimeString() : "";
    const tr = el("tr", { role: "button", tabindex: "0" },
      el("td", { class: "st " + r.status, text: r.status }),
      el("td", { text: r.command }),
      el("td", { class: "muted", text: when }),
      el("td", { class: "muted", text: r.exit_code == null ? "" : "exit " + r.exit_code }));
    const open = () => {
      if (r.status === "running") attachStream(r.run_id, true);
      else openLog(r.run_id);
    };
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });
    tb.append(tr);
  }
}

async function openLog(runId) {
  try {
    const res = await fetch(`/api/runs/${runId}/log`);
    $("#console").textContent = await res.text();
    $("#status-badge").hidden = true;
    $("#console").scrollIntoView({ block: "nearest" });
  } catch (_) {}
}

/* ============================================================ helpers */
function bizName(id) {
  const b = state.businesses.find(x => x.id === id);
  return b ? b.name : null;
}
function liveButton(fn, label) {
  const b = el("button", { class: "btn danger" }, label);
  let armed = false;
  b.addEventListener("click", () => {
    if (!armed) { armed = true; b.textContent = "Click again to confirm"; return; }
    armed = false; b.textContent = label; fn();
  });
  return b;
}
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
refreshStatus();                     // first load; triggers loadAll() when authed
setInterval(refreshStatus, 3000);
