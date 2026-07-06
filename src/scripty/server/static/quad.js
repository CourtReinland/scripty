/* Scripty COMPARE BAY — four-panel quad view:
   FILM | KNOWN SCRIPT | SCRIPTY'S SCRIPT | DESCRIBE.
   Uses the XSS-safe elt() DOM builder shared by app.js (window.scriptyShared);
   no innerHTML anywhere — untrusted values (LLM output, server strings,
   filenames) are only ever appended as inert text. Adds whole-window
   drag-drop ingest (native paths via scriptyNative.pathForFile under the
   Electron preload, multipart upload as browser fallback), a RUN PASS runner
   with progress polling, and a bottom-left toast stack. */
"use strict";

(() => {
  const shared = window.scriptyShared;
  if (!shared) return;
  const { elt, fetchJSON, state, setView, onViewChange, loadProject } = shared;

  const VIDEO_EXTS = [".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"];
  const SCRIPT_EXTS = [".fountain", ".txt"];
  const txt = (v) => (v == null ? "" : String(v));
  const jsonReq = (body) => ({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const qd = {
    shots: [], scenes: [], prompts: [], truth: null, alignment: null,
    revision: null, activeIdx: -1, cardByShot: new Map(), sceneElts: [],
    pollTimer: null, progressPid: null,
  };

  /* ---- toast stack (bottom-left, auto-dismiss) ---------------------------- */

  const toasts = elt("div", { id: "qToasts" });

  function toast(msg, isError) {
    const t = elt("div", { class: "q-toast" + (isError ? " err" : "") }, msg);
    toasts.append(t);
    setTimeout(() => {
      t.classList.add("gone");
      setTimeout(() => t.remove(), 400);
    }, isError ? 6000 : 3800);
  }

  /* ---- drop overlay + layout ------------------------------------------------ */

  const overlay = elt("div", { id: "qOverlay" },
    elt("div", { class: "q-drop-tile" }, "DROP FILM",
      elt("small", null, VIDEO_EXTS.join("  "))),
    elt("div", { class: "q-drop-tile" }, "DROP SCRIPT",
      elt("small", null, SCRIPT_EXTS.join("  "))));

  let grid, player, runBtn, progressEl, stripEl, truthBody, scriptBody, descBody, revSel;
  const widths = [1.15, 1, 1, 1];
  const hint = (msg) => elt("p", { class: "dim q-hint" }, msg);

  function applyWidths() {
    grid.style.gridTemplateColumns = widths.map((w) => w.toFixed(3) + "fr").join(" 6px ");
  }

  function divider(i) {
    const d = elt("div", { class: "q-divider" });
    d.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      const total = widths[i] + widths[i + 1];
      const frPerPx = widths.reduce((a, b) => a + b, 0) /
        Math.max(grid.getBoundingClientRect().width, 1);
      const startX = ev.clientX, w0 = widths[i];
      d.classList.add("dragging");
      const move = (e) => {
        widths[i] = Math.min(Math.max(w0 + (e.clientX - startX) * frPerPx, 0.35), total - 0.35);
        widths[i + 1] = total - widths[i];
        applyWidths();
      };
      const up = () => {
        d.classList.remove("dragging");
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    });
    return d;
  }

  function buildLayout() {
    player = elt("video", { id: "qPlayer", controls: "", preload: "metadata" });
    player.addEventListener("timeupdate", onTime);
    runBtn = elt("button", { id: "qRunBtn", type: "button", disabled: "" }, "RUN PASS ▶");
    runBtn.onclick = startPass;
    progressEl = elt("div", { id: "qProgress" });
    stripEl = elt("div", { id: "qStrip" });
    truthBody = elt("div", { class: "q-col-body q-doc", id: "qTruthBody" });
    scriptBody = elt("div", { class: "q-col-body q-doc", id: "qScriptBody" });
    descBody = elt("div", { class: "q-col-body", id: "qDescribeBody" });
    revSel = elt("select", { class: "q-rev", hidden: "" });
    revSel.onchange = () => { qd.revision = Number(revSel.value); renderDescribe(); };
    const filmBody = elt("div", { class: "q-col-body" }, player,
      elt("div", { class: "q-runrow" }, runBtn), progressEl, stripEl);
    const col = (id, label, body, ...extras) => elt("div", { class: "q-col", id },
      elt("div", { class: "q-col-head" }, label, ...extras), body);
    grid = elt("div", { id: "qGrid" },
      col("qColFilm", "FILM", filmBody), divider(0),
      col("qColTruth", "KNOWN SCRIPT", truthBody), divider(1),
      col("qColScript", "SCRIPTY'S SCRIPT", scriptBody), divider(2),
      col("qColDescribe", "DESCRIBE", descBody, revSel));
    applyWidths();
    document.querySelector("#quadLayout").replaceChildren(grid);
    document.body.append(overlay, toasts);
  }

  /* ---- data refresh ----------------------------------------------------------- */

  let refreshGen = 0;

  async function refreshAll() {
    const gen = ++refreshGen;
    const pid = state.projectId, passId = state.passId;
    if (pid) player.src = `/media/video/${pid}`;
    else player.removeAttribute("src");
    runBtn.disabled = !pid || qd.pollTimer != null;
    /* Progress log belongs to one run of one project: clear it once no
       poll is live and the bay shows a different project. */
    if (qd.pollTimer == null && qd.progressPid !== pid) {
      qd.progressPid = null;
      progressEl.classList.remove("on");
      progressEl.replaceChildren();
    }
    if (!pid) {
      stripEl.replaceChildren();
      truthBody.replaceChildren(hint("no project yet — drop a film anywhere in this window"));
      scriptBody.replaceChildren(hint("no project yet"));
      descBody.replaceChildren(hint("no project yet"));
      return;
    }
    const [truth, passData, script, prompts, alignment] = await Promise.all([
      fetchJSON(`/api/projects/${pid}/truth`).catch(() => null),
      passId ? fetchJSON(`/api/passes/${passId}`).catch(() => null) : null,
      passId ? fetchJSON(`/api/passes/${passId}/script`).catch(() => null) : null,
      passId ? fetchJSON(`/api/passes/${passId}/prompts`).catch(() => []) : [],
      passId ? fetchJSON(`/api/passes/${passId}/alignment`).catch(() => null) : null,
    ]);
    if (gen !== refreshGen) return; /* superseded — never paint stale data */
    qd.truth = truth;
    qd.shots = (passData && passData.shots) || [];
    qd.scenes = (passData && passData.scenes) || [];
    qd.prompts = prompts || [];
    qd.alignment = alignment;
    renderStrip();
    renderTruth();
    renderScript(script ? script.fountain : null);
    renderDescribe();
  }

  /* ---- FILM: shot strip + active-shot tracking ----------------------------------- */

  function renderStrip() {
    qd.activeIdx = -1;
    stripEl.replaceChildren(...qd.shots.map((s, i) => {
      const kf = (s.keyframes || [])[1] || (s.keyframes || [])[0];
      const img = kf
        ? elt("img", { alt: "", loading: "lazy",
            src: `/media/keyframe?path=${encodeURIComponent(kf)}` })
        : elt("div", { class: "q-noframe" });
      const tile = elt("div",
        { class: "q-thumb", "data-idx": i, title: `${txt(s.scale)} ${txt(s.subject)}`.trim() || "shot" },
        img, elt("div", { class: "q-tc" },
          `${String((s.idx ?? i) + 1).padStart(3, "0")} · ${(s.start_s || 0).toFixed(1)}s`));
      tile.onclick = () => {
        player.currentTime = (s.start_s || 0) + 0.05;
        player.play().catch(() => {});
        jumpToScene(s.id);
      };
      return tile;
    }));
  }

  function onTime() {
    const t = player.currentTime;
    const i = qd.shots.findIndex((s) => t >= s.start_s && t < s.end_s);
    if (i === qd.activeIdx) return;
    qd.activeIdx = i;
    stripEl.querySelectorAll(".q-thumb").forEach((el, j) => el.classList.toggle("active", j === i));
    descBody.querySelectorAll(".card.q-active").forEach((c) => c.classList.remove("q-active"));
    if (i < 0) return;
    const card = qd.cardByShot.get(qd.shots[i].id);
    if (card) {
      card.classList.add("q-active");
      card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function jumpToScene(shotId) {
    const idx = qd.scenes.findIndex((sc) => (sc.shot_ids || []).includes(shotId));
    const target = idx >= 0 ? qd.sceneElts[idx] : null;
    if (!target) return;
    target.scrollIntoView({ block: "start", behavior: "smooth" });
    target.classList.remove("q-flash");
    void target.offsetWidth; /* restart the flash animation */
    target.classList.add("q-flash");
  }

  /* ---- KNOWN SCRIPT (truth) -------------------------------------------------------- */

  function renderTruth() {
    if (!qd.truth || !qd.truth.parsed) {
      truthBody.replaceChildren(elt("div", { class: "q-dropzone" }, "DROP THE REAL SCRIPT",
        elt("small", null, "(.fountain/.txt) — anywhere in this window")));
      return;
    }
    const pairsByTruth = new Map();
    for (const p of (qd.alignment && qd.alignment.scene_pairs) || []) pairsByTruth.set(p.truth_idx, p);
    const out = [];
    if (qd.truth.parsed.title) out.push(elt("div", { class: "q-title" }, txt(qd.truth.parsed.title)));
    (qd.truth.parsed.scenes || []).forEach((sc, i) => {
      const slug = `${txt(sc.int_ext)} ${txt(sc.location)} - ${txt(sc.time_of_day)}`
        .replace(/\s+/g, " ").trim();
      const headEl = elt("div", { class: "q-slug" }, slug);
      if (qd.alignment) verdict(headEl, pairsByTruth.get(sc.idx == null ? i : sc.idx));
      const block = elt("div", { class: "q-scene" }, headEl);
      for (const a of sc.action || []) block.append(elt("div", { class: "q-act" }, txt(a)));
      for (const d of sc.dialogue || []) {
        block.append(elt("div", { class: "q-char" }, txt(d && d[0])),
          elt("div", { class: "q-dial" }, txt(d && d[1])));
      }
      out.push(block);
    });
    if (!out.length) out.push(hint("known script parsed, but no scenes found"));
    truthBody.replaceChildren(...out);
  }

  /* Color a truth slugline by its alignment verdict; tooltip lists diffs. */
  function verdict(headEl, pair) {
    if (!pair) {
      headEl.classList.add("t-red");
      headEl.setAttribute("title", "unmatched — no corresponding scene in Scripty's script");
      return;
    }
    const diffs = pair.diffs || {}, keys = Object.keys(diffs);
    const score = pair.score == null ? 0 : pair.score;
    if (!keys.length && score >= 0.8) {
      headEl.classList.add("t-green");
      headEl.setAttribute("title",
        `paired with model scene ${txt(pair.model_idx)} — score ${score.toFixed(2)}`);
      return;
    }
    headEl.classList.add("t-amber");
    headEl.setAttribute("title", keys.length
      ? keys.map((k) => `${k}: scripty "${txt(diffs[k].model)}" vs truth "${txt(diffs[k].truth)}"`).join("\n")
      : `paired with low score ${score.toFixed(2)}`);
  }

  /* ---- SCRIPTY'S SCRIPT: fountain classification (shared, server-parity) ------------- */

  /* Classification lives in /static/fountain.js (window.scriptyFountain) and
     mirrors the server's classify_lines, so KNOWN SCRIPT and SCRIPTY'S SCRIPT
     agree on the same document; scene anchors are matched against qd.scenes
     sluglines so phantom slug-like body lines can't shift jump targets. */
  function renderScript(fountainText) {
    qd.sceneElts = [];
    if (!fountainText) {
      scriptBody.replaceChildren(hint(state.passId
        ? "no generated script for this pass yet"
        : "no pass yet — RUN PASS ▶ in the FILM column"));
      return;
    }
    const r = window.scriptyFountain.render(elt, fountainText, qd.scenes);
    qd.sceneElts = r.sceneElts;
    scriptBody.replaceChildren(...r.nodes);
  }

  /* ---- DESCRIBE: prompt cards -------------------------------------------------------- */

  function renderDescribe() {
    qd.cardByShot = new Map();
    const revs = [...new Set(qd.prompts.map((p) => p.revision))].sort((a, b) => a - b);
    if (qd.revision == null || !revs.includes(qd.revision)) {
      qd.revision = revs.length ? revs[revs.length - 1] : null;
    }
    revSel.hidden = revs.length <= 1;
    if (revs.length > 1) {
      revSel.replaceChildren(...revs.map((r) => elt("option", { value: r }, `rev ${r}`)));
      revSel.value = String(qd.revision);
    }
    const show = qd.prompts.filter((p) => p.revision === qd.revision);
    if (!show.length) {
      descBody.replaceChildren(hint(state.passId ? "no describe prompts for this pass" : "no pass yet"));
      return;
    }
    descBody.replaceChildren(...show.map(promptCard));
  }

  function promptCard(p) {
    const body = elt("div", { class: "card-body" }, txt(p.prompt));
    const editBtn = elt("button", { class: "ghost", type: "button" }, "edit");
    const where = p.shot_id ? "shot prompt" : "scene umbrella";
    const card = elt("div", { class: "card" },
      elt("div", { class: "card-head" }, `#${txt(p.id)} · ${where} · rev ${txt(p.revision)}`, editBtn),
      body);
    if (p.shot_id) qd.cardByShot.set(p.shot_id, card);
    editBtn.onclick = () => beginPromptEdit(card, body, p);
    return card;
  }

  function beginPromptEdit(card, body, p) {
    if (card.querySelector("textarea")) return;
    const ta = elt("textarea", null);
    ta.value = txt(p.prompt);
    const save = elt("button", { class: "q-save", type: "button" }, "save correction");
    const cancel = elt("button", { class: "ghost", type: "button" }, "cancel");
    const actions = elt("div", { class: "q-edit-actions" }, save, cancel);
    body.replaceChildren(ta);
    card.append(actions);
    ta.focus();
    const done = () => { actions.remove(); body.replaceChildren(txt(p.prompt)); };
    cancel.onclick = done;
    save.onclick = async () => {
      const val = ta.value.trim();
      if (!val || val === txt(p.prompt).trim()) { done(); return; }
      try {
        await fetchJSON("/api/corrections", jsonReq({
          project_id: state.projectId, pass_id: state.passId,
          entity_type: "describe_prompt", entity_id: p.id, field: "prompt",
          model_value: txt(p.prompt), human_value: val, note: "", scope: "project",
        }));
        p.prompt = val;
        card.classList.add("learned");
        done();
        toast(`prompt #${p.id} corrected`);
      } catch (e) { toast("correction failed: " + e.message, true); }
    };
  }

  /* ---- drag-drop ingest (whole window, both views) ------------------------------------- */

  const hasFiles = (ev) => !!ev.dataTransfer && [...(ev.dataTransfer.types || [])].includes("Files");
  let dragDepth = 0;

  window.addEventListener("dragenter", (ev) => {
    if (!hasFiles(ev)) return;
    ev.preventDefault();
    dragDepth += 1;
    overlay.classList.add("on");
  });
  window.addEventListener("dragleave", (ev) => {
    if (!hasFiles(ev)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) overlay.classList.remove("on");
  });
  window.addEventListener("dragover", (ev) => { if (hasFiles(ev)) ev.preventDefault(); });
  window.addEventListener("drop", (ev) => {
    if (!hasFiles(ev)) return;
    ev.preventDefault();
    dragDepth = 0;
    overlay.classList.remove("on");
    handleDrop([...ev.dataTransfer.files]);
  });

  async function handleDrop(files) {
    for (const file of files) {
      const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
      try {
        if (VIDEO_EXTS.includes(ext)) await ingestVideo(file);
        else if (SCRIPT_EXTS.includes(ext)) await ingestScript(file);
        else toast(`unsupported file type: ${file.name}`, true);
      } catch (e) { toast(`${file.name}: ${e.message}`, true); }
    }
  }

  const nativePath = (file) =>
    (window.scriptyNative && typeof window.scriptyNative.pathForFile === "function")
      ? window.scriptyNative.pathForFile(file) : null;

  async function ingestVideo(file) {
    toast(`ingesting film ${file.name}…`);
    const path = nativePath(file);
    let out;
    if (path) {
      out = await fetchJSON("/api/projects/from-path", jsonReq({ path: path, name: null }));
    } else {
      const fd = new FormData();
      fd.append("file", file);
      out = await fetchJSON("/api/projects/upload", { method: "POST", body: fd });
    }
    toast(`project #${out.project_id} created — RUN PASS ▶ when ready`);
    setView("quad");
    await selectProject(out.project_id);
  }

  async function ingestScript(file) {
    const pid = state.projectId;
    if (!pid) { toast("no project selected — drop or pick a film first", true); return; }
    toast(`linking known script ${file.name}…`);
    const path = nativePath(file);
    if (path) {
      await fetchJSON(`/api/projects/${pid}/truth/from-path`, jsonReq({ path: path }));
    } else {
      const fd = new FormData();
      fd.append("file", file);
      await fetchJSON(`/api/projects/${pid}/truth/upload`, { method: "POST", body: fd });
    }
    toast("known script linked");
    await refreshAll();
  }

  /* Refresh the shared project list, select pid in the supervisor picker,
     load it (updates shared state + supervisor view), then redraw the bay. */
  async function selectProject(pid) {
    try {
      state.projects = await fetchJSON("/api/projects");
      const sel = document.querySelector("#projectSelect");
      if (sel) {
        sel.replaceChildren(...state.projects.map(
          (pr) => elt("option", { value: pr.id }, `#${pr.id} ${txt(pr.name)}`)));
        sel.value = String(pid);
      }
    } catch (e) { /* keep the stale list; loadProject still works */ }
    await loadProject(pid);
    await refreshAll();
  }

  /* ---- RUN PASS + progress polling ------------------------------------------------------ */

  async function startPass() {
    const pid = state.projectId;
    if (!pid) { toast("no project selected", true); return; }
    try {
      await fetchJSON(`/api/projects/${pid}/passes`, jsonReq({ provider: null, transcriber: null }));
    } catch (e) {
      toast("pass not started: " + e.message, true);
      return;
    }
    toast("pass started");
    runBtn.disabled = true;
    qd.progressPid = pid;
    progressEl.classList.add("on");
    progressEl.replaceChildren("starting pass…");
    let seenRunning = false, polls = 0, errors = 0;
    const poll = async () => {
      polls += 1;
      let prog;
      try {
        prog = await fetchJSON(`/api/projects/${pid}/progress`);
        errors = 0;
      } catch (e) {
        /* transient (dropped connection, sleep tick): keep polling; only a
           sustained outage abandons the watch — never fake "complete". */
        errors += 1;
        if (errors < 8) { qd.pollTimer = setTimeout(poll, 1500); return; }
        qd.pollTimer = null;
        runBtn.disabled = false;
        toast("lost contact with the server while watching the pass", true);
        return;
      }
      if (prog.lines && prog.lines.length) {
        progressEl.replaceChildren(prog.lines.join("\n"));
        progressEl.scrollTop = progressEl.scrollHeight;
      }
      if (prog.running) seenRunning = true;
      /* Done only when the server says not-running AND we saw it run, saw
         its output (fast passes can finish before the first poll), or the
         startup grace window is exhausted. */
      const done = !prog.running &&
        (seenRunning || (prog.lines && prog.lines.length > 0) || polls >= 8);
      if (!done) {
        qd.pollTimer = setTimeout(poll, 1500);
        return;
      }
      qd.pollTimer = null;
      runBtn.disabled = false;
      await selectProject(pid);
      const passes = (state.projectDetail && state.projectDetail.passes) || [];
      const last = passes[passes.length - 1];
      if (last && last.status === "failed") toast(`pass ${last.number} failed`, true);
      else toast(`pass ${last ? last.number : ""} complete`.trim());
    };
    qd.pollTimer = setTimeout(poll, 1000);
  }

  /* ---- boot ------------------------------------------------------------------------------ */

  /* File > Link Known Script… (Electron menu) sends the chosen path here. */
  if (window.scriptyNative && typeof window.scriptyNative.onLinkScript === "function") {
    window.scriptyNative.onLinkScript(async (path) => {
      const pid = state.projectId;
      if (!pid) { toast("no project selected — open or drop a film first", true); return; }
      try {
        await fetchJSON(`/api/projects/${pid}/truth/from-path`, jsonReq({ path: path }));
        toast("known script linked");
        await refreshAll();
      } catch (e) { toast("link script failed: " + e.message, true); }
    });
  }

  onViewChange((name) => { if (name === "quad") refreshAll(); });
  buildLayout();
})();
