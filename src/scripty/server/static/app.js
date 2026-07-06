/* Scripty dashboard — vanilla JS, no dependencies.
   All rendering uses DOM construction (createElement / textContent / append)
   so untrusted values (LLM output, server strings) are always inert text and
   never parsed as HTML. The screenplay tab renders server HTML inside a
   fully sandboxed iframe. */
"use strict";

const state = {
  projects: [],
  projectId: null,
  projectDetail: null,
  passId: null,
  passData: null,
  activeTab: "script",
};

const $ = (sel) => document.querySelector(sel);

/* Coerce a possibly-null value to display text (mirrors old esc() nulls). */
const text = (v) => (v == null ? "" : String(v));

/* Build an element. Attributes go through setAttribute; children are
   appended as text nodes (strings) or nodes — never parsed as markup. */
function elt(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const key of Object.keys(attrs)) {
      if (attrs[key] == null) continue;
      node.setAttribute(key, String(attrs[key]));
    }
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(child);
  }
  return node;
}

function timecode(seconds, fps) {
  fps = fps || 24;
  if (!seconds || seconds < 0) seconds = 0;
  const fpsInt = Math.max(Math.round(fps), 1);
  const totalFrames = Math.round(seconds * fps);
  const total = Math.floor(totalFrames / fpsInt);
  const frames = totalFrames % fpsInt;
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}:${pad(frames)}`;
}

function status(msg, isError) {
  const el = $("#statusline");
  el.textContent = msg || "";
  el.classList.toggle("error", !!isError);
  if (msg) setTimeout(() => { if (el.textContent === msg) el.textContent = ""; }, 4000);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* noop */ }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/* ---------- projects / passes ------------------------------------------- */

async function init() {
  try {
    state.projects = await api("/api/projects");
  } catch (e) { status("failed to load projects: " + e.message, true); return; }
  const sel = $("#projectSelect");
  sel.replaceChildren(...state.projects.map(
    (p) => elt("option", { value: p.id }, `#${p.id} ${text(p.name)}`)));
  sel.onchange = () => loadProject(Number(sel.value));
  $("#passSelect").onchange = () => loadPass(Number($("#passSelect").value));
  document.querySelectorAll("#tabs button").forEach((b) => {
    b.onclick = () => switchTab(b.dataset.tab);
  });
  if (state.projects.length) loadProject(state.projects[0].id);
  else status("no projects yet — run: scripty demo", false);
}

async function loadProject(pid) {
  state.projectId = pid;
  try { state.projectDetail = await api(`/api/projects/${pid}`); }
  catch (e) { status(e.message, true); return; }
  $("#player").src = `/media/video/${pid}`;
  const passes = state.projectDetail.passes || [];
  const sel = $("#passSelect");
  sel.replaceChildren(...passes.map(
    (p) => elt("option", { value: p.id },
      `pass ${text(p.number)} — ${text(p.provider)} (${text(p.status)})`)));
  renderBundle();
  if (passes.length) {
    const last = passes[passes.length - 1];
    sel.value = String(last.id);
    loadPass(last.id);
  } else {
    state.passId = null;
    $("#shotRows").replaceChildren();
    $("#passMeta").textContent = "no passes yet";
  }
}

async function loadPass(passId) {
  state.passId = passId;
  try { state.passData = await api(`/api/passes/${passId}`); }
  catch (e) { status(e.message, true); return; }
  const p = state.passData.pass, m = state.passData.metrics || {};
  $("#passMeta").replaceChildren(
    `shots ${text(m.n_shots ?? state.passData.shots.length)} · ` +
    `scenes ${text(m.n_scenes ?? state.passData.scenes.length)} · ` +
    `dialogue ${text(m.n_dialogue ?? state.passData.dialogue.length)}`,
    elt("br"),
    `provider ${text(p.provider)} · status ${text(p.status)}`);
  renderShots();
  renderTab(state.activeTab);
}

/* ---------- shot table ---------------------------------------------------- */

const EDITABLE = ["scale", "subject", "angle", "move", "int_ext",
                  "location", "time_of_day", "characters", "action_text"];

function confBadge(c) {
  const cls = c >= 0.7 ? "hi" : c >= 0.4 ? "mid" : "lo";
  return elt("span", { class: `conf ${cls}` }, (c ?? 0).toFixed(2));
}

function cellValue(shot, field) {
  if (field === "characters") return (shot.characters || []).join(", ");
  return shot[field] == null ? "" : String(shot[field]);
}

function renderShots() {
  const fps = (state.projectDetail && state.projectDetail.project.fps) || 24;
  const shots = state.passData.shots || [];
  $("#shotCount").textContent = shots.length ? `(${shots.length})` : "";
  $("#shotRows").replaceChildren(...shots.map((s) => {
    const kf = (s.keyframes || [])[1] || (s.keyframes || [])[0] || "";
    const thumb = kf
      ? elt("img", { class: "thumb", loading: "lazy", alt: "",
                     src: `/media/keyframe?path=${encodeURIComponent(kf)}` })
      : elt("div", { class: "thumb empty" });
    const label = `${text(s.scale)} ${(s.subject || "UNKNOWN").toUpperCase()}`;
    const tr = elt("tr", { "data-start": s.start_s },
      elt("td", null, thumb),
      elt("td", { class: "dim" }, String(s.idx + 1).padStart(4, "0")),
      elt("td", { class: "tc" },
        timecode(s.start_s, fps), elt("br"), timecode(s.end_s, fps)),
      elt("td", { class: "label" }, label));
    for (const f of EDITABLE) {
      tr.append(elt("td", {
        class: "edit", "data-entity": "shot", "data-id": s.id,
        "data-field": f, "data-value": cellValue(s, f),
        title: "click to correct",
      }, cellValue(s, f)));
    }
    tr.append(elt("td", null, confBadge(s.confidence)));
    return tr;
  }));
  document.querySelectorAll("#shotRows tr").forEach((tr) => {
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("td.edit, input, textarea")) return;
      const player = $("#player");
      player.currentTime = Number(tr.dataset.start) + 0.05;
      player.play().catch(() => {});
    });
  });
  document.querySelectorAll("#shotRows td.edit").forEach((td) => {
    td.addEventListener("click", () => beginEdit(td));
  });
}

/* ---------- click-to-edit corrections ------------------------------------- */

function beginEdit(td, entityType, field) {
  if (td.querySelector("input, textarea")) return;
  entityType = entityType || td.dataset.entity;
  field = field || td.dataset.field;
  const old = td.dataset.value || "";
  const long = field === "action_text" || field === "prompt";
  const input = document.createElement(long ? "textarea" : "input");
  input.value = old;
  td.textContent = "";
  td.appendChild(input);
  input.focus();
  input.select();
  let done = false;
  const finish = (commit) => {
    if (done) return;
    done = true;
    const val = input.value.trim();
    if (!commit || val === old.trim()) { td.textContent = old; return; }
    td.textContent = val;
    postCorrection(td, entityType, Number(td.dataset.id), field, old, val);
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !(long && ev.shiftKey)) { ev.preventDefault(); finish(true); }
    if (ev.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}

async function postCorrection(td, entityType, entityId, field, oldVal, newVal) {
  try {
    await api("/api/corrections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: state.projectId, pass_id: state.passId,
        entity_type: entityType, entity_id: entityId, field: field,
        model_value: oldVal, human_value: newVal, note: "", scope: "project",
      }),
    });
    td.dataset.value = newVal;
    td.classList.add("learned");
    status(`correction recorded — ${field}: “${newVal}”`);
  } catch (e) {
    td.textContent = oldVal;
    status("correction failed: " + e.message, true);
  }
}

/* ---------- right-hand tabs ------------------------------------------------ */

function switchTab(name) {
  state.activeTab = name;
  document.querySelectorAll("#tabs button").forEach(
    (b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll("#panels .tab").forEach(
    (t) => t.classList.toggle("active", t.id === `tab-${name}`));
  renderTab(name);
}

function renderTab(name) {
  if (!state.passId) return;
  ({ script: renderScript, describe: renderDescribe, truth: renderTruth,
     lessons: renderLessons, metrics: renderMetrics, bundle: renderBundle,
  }[name] || (() => {}))();
}

async function renderScript() {
  try {
    const data = await api(`/api/passes/${state.passId}/script`);
    const frame = $("#scriptFrame");
    /* Server-generated screenplay HTML is display-only: the empty sandbox
       blocks scripts, forms, popups, and same-origin access entirely. */
    frame.sandbox = "";
    frame.srcdoc = data.html;
  } catch (e) { status(e.message, true); }
}

async function renderDescribe() {
  const el = $("#tab-describe");
  let prompts;
  try { prompts = await api(`/api/passes/${state.passId}/prompts`); }
  catch (e) { el.replaceChildren(elt("p", { class: "dim" }, e.message)); return; }
  if (!prompts.length) {
    el.replaceChildren(elt("p", { class: "dim" }, "no describe prompts for this pass."));
    return;
  }
  el.replaceChildren(...prompts.map((p) => {
    const where = p.shot_id ? "shot prompt" : "scene umbrella";
    const body = elt("div", {
      class: "card-body edit", "data-entity": "describe_prompt",
      "data-id": p.id, "data-field": "prompt", "data-value": text(p.prompt),
      title: "click to correct",
    }, text(p.prompt));
    body.addEventListener("click", () => beginEdit(body, "describe_prompt", "prompt"));
    return elt("div", { class: "card" },
      elt("div", { class: "card-head" },
        `#${p.id} · ${where} · rev ${text(p.revision)} · ${text(p.target)}`),
      body);
  }));
}

async function renderTruth() {
  const el = $("#tab-truth");
  let rep;
  try { rep = await api(`/api/passes/${state.passId}/alignment`); }
  catch (e) {
    el.replaceChildren(elt("p", { class: "dim" }, e.status === 404
      ? "no ground truth linked — use: scripty truth PROJECT_ID script.fountain, then scripty align PASS_ID"
      : e.message));
    return;
  }
  const fields = ["int_ext", "location", "time_of_day"];
  const tbody = elt("tbody");
  for (const pair of rep.scene_pairs || []) {
    const diffs = pair.diffs || {};
    const tr = elt("tr", null,
      elt("td", null, text(pair.model_idx)),
      elt("td", null, text(pair.truth_idx)),
      elt("td", null, (pair.score ?? 0).toFixed(2)));
    for (const f of fields) {
      const d = diffs[f];
      tr.append(d
        ? elt("td", { class: "bad" }, `${text(d.model)} → ${text(d.truth)}`)
        : elt("td", { class: "ok" }, "match"));
    }
    tbody.append(tr);
  }
  if (!tbody.children.length) {
    tbody.append(elt("tr", null,
      elt("td", { colspan: "6", class: "dim" }, "no paired scenes")));
  }
  const dlg = rep.dialogue || {};
  el.replaceChildren(
    elt("div", { class: "statrow" },
      elt("span", null, "slugline accuracy ",
        elt("b", null, `${((rep.slugline_accuracy ?? 0) * 100).toFixed(0)}%`)),
      elt("span", null, "dialogue matched ",
        elt("b", null, `${dlg.matched ?? 0}/${dlg.truth_lines ?? 0}`)),
      elt("span", null, "char accuracy ",
        elt("b", null, `${((dlg.character_accuracy ?? 0) * 100).toFixed(0)}%`))),
    elt("table", { class: "mini" },
      elt("thead", null, elt("tr", null,
        ...["model", "truth", "score", "I/E", "location", "time"].map(
          (h) => elt("th", null, h)))),
      tbody),
    elt("p", { class: "dim" },
      `unmatched model: ${(rep.unmatched_model || []).join(", ") || "—"} ` +
      `· unmatched truth: ${(rep.unmatched_truth || []).join(", ") || "—"}`));
}

async function renderLessons() {
  const el = $("#tab-lessons");
  let lessons;
  try { lessons = await api(`/api/lessons?project_id=${state.projectId}`); }
  catch (e) { el.replaceChildren(elt("p", { class: "dim" }, e.message)); return; }
  const cards = lessons.map((l) => elt("div", { class: "card lesson" },
    elt("div", { class: "card-head" },
      elt("span", { class: "chip" }, text(l.field)),
      elt("span", { class: "dim" }, `w ${(l.weight ?? 1).toFixed(1)} · ${text(l.scope)}`),
      elt("button", { class: "ghost", "data-deact": l.id }, "deactivate")),
    elt("div", { class: "card-body" }, text(l.rule))));
  el.replaceChildren(
    elt("div", { class: "toolbar" },
      elt("button", { id: "distillBtn" }, "Distill lessons from corrections")),
    ...(cards.length ? cards
      : [elt("p", { class: "dim" }, "no lessons yet — correct some fields, then distill.")]));
  $("#distillBtn").onclick = async () => {
    try {
      const out = await api("/api/lessons/distill", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: state.projectId }),
      });
      status(`distilled ${out.count} lesson(s)`);
      renderLessons();
    } catch (e) { status("distill failed: " + e.message, true); }
  };
  el.querySelectorAll("button[data-deact]").forEach((b) => {
    b.onclick = async () => {
      try {
        await api(`/api/lessons/${b.dataset.deact}/deactivate`, { method: "POST" });
        renderLessons();
      } catch (e) { status(e.message, true); }
    };
  });
}

/* ---------- metrics chart (hand-rolled canvas) ----------------------------- */

function agreementOf(m) {
  if (m == null) return null;
  if (typeof m.agreement_rate === "number") return m.agreement_rate;
  if (m.agreement && typeof m.agreement.agreement_rate === "number")
    return m.agreement.agreement_rate;
  return null;
}

async function renderMetrics() {
  let series;
  try { series = await api(`/api/metrics/${state.projectId}`); }
  catch (e) { status(e.message, true); return; }
  const canvas = $("#metricsCanvas");
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  const padL = 46, padR = 16, padT = 18, padB = 34;
  ctx.clearRect(0, 0, W, H);
  const css = getComputedStyle(document.documentElement);
  const cAmber = css.getPropertyValue("--amber").trim() || "#e8b34b";
  const cCyan = css.getPropertyValue("--cyan").trim() || "#6fc3c9";
  const cGrid = "rgba(255,255,255,0.08)", cText = "rgba(230,228,220,0.65)";
  const n = series.length;
  const x = (i) => n <= 1 ? (padL + (W - padL - padR) / 2)
                          : padL + (i * (W - padL - padR)) / (n - 1);
  const y = (v) => padT + (1 - v) * (H - padT - padB);
  ctx.font = "11px ui-monospace, monospace";
  for (let g = 0; g <= 4; g++) {
    const v = g / 4;
    ctx.strokeStyle = cGrid; ctx.beginPath();
    ctx.moveTo(padL, y(v)); ctx.lineTo(W - padR, y(v)); ctx.stroke();
    ctx.fillStyle = cText; ctx.fillText(v.toFixed(2), 8, y(v) + 4);
  }
  series.forEach((p, i) => {
    ctx.fillStyle = cText;
    ctx.fillText("p" + p.number, x(i) - 8, H - 12);
  });
  const drawLine = (vals, color) => {
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 2;
    let started = false;
    ctx.beginPath();
    vals.forEach((v, i) => {
      if (v == null) return;
      if (!started) { ctx.moveTo(x(i), y(v)); started = true; }
      else ctx.lineTo(x(i), y(v));
    });
    if (started) ctx.stroke();
    vals.forEach((v, i) => {
      if (v == null) return;
      ctx.beginPath(); ctx.arc(x(i), y(v), 3.5, 0, Math.PI * 2); ctx.fill();
    });
  };
  drawLine(series.map((p) => agreementOf(p.metrics)), cAmber);
  drawLine(series.map((p) => (typeof (p.metrics || {}).mean_confidence === "number"
    ? p.metrics.mean_confidence : null)), cCyan);
  $("#metricsNote").replaceChildren(
    elt("span", { style: `color:${cAmber}` }, "●"), " agreement rate  ",
    elt("span", { style: `color:${cCyan}` }, "●"), " mean confidence — per pass");
}

/* ---------- bundle ---------------------------------------------------------- */

const BUNDLE_SLOTS = [
  ["source_video", "1 · Source video"],
  ["known_script", "2 · Known script (ground truth)"],
  ["generated_script", "3 · Generated script"],
  ["describe_set", "4 · Describe prompt set"],
  ["generated_video", "5 · Generated video"],
  ["comparison_report", "+ · Comparison report"],
];

function renderBundle() {
  const el = $("#tab-bundle");
  const artifacts = (state.projectDetail && state.projectDetail.artifacts) || [];
  el.replaceChildren(...BUNDLE_SLOTS.map(([kind, label]) => {
    const hits = artifacts.filter((a) => a.kind === kind);
    const latest = hits[hits.length - 1];
    const slotStatus = elt("div", { class: "slot-status" });
    if (latest) {
      slotStatus.append(
        elt("span", { class: "ok" }, "present"),
        elt("span", { class: "dim path" }, text(latest.ref)));
    } else {
      slotStatus.append(elt("span", { class: "dim" }, "missing"));
    }
    return elt("div", { class: `slot ${latest ? "filled" : "missing"}` },
      elt("div", { class: "slot-label" }, label),
      slotStatus);
  }));
}

/* ---------- shared surface + view toggle (COMPARE BAY lives in quad.js) --- */

const viewListeners = [];

function setView(name) {
  document.body.classList.toggle("view-quad", name === "quad");
  document.querySelectorAll("#viewToggle button").forEach(
    (b) => b.classList.toggle("active", b.dataset.view === name));
  for (const cb of viewListeners) {
    try { cb(name); } catch (e) { /* listeners must not break the app */ }
  }
}

document.querySelectorAll("#viewToggle button").forEach((b) => {
  b.onclick = () => setView(b.dataset.view);
});

window.scriptyShared = {
  elt,
  fetchJSON: api,
  state,
  setView,
  onViewChange: (cb) => viewListeners.push(cb),
  loadProject,
};

init();
