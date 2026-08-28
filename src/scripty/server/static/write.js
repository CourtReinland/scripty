/* Scripty writer dashboard — pairwise drafts, human as only judge. */
(function () {
  const $ = (id) => document.getElementById(id);
  const state = { view: null, desks: [] };

  function status(msg, isError) {
    const el = $("status");
    el.textContent = msg || "";
    el.style.color = isError ? "#8b3a3a" : "";
  }

  async function api(path, opts) {
    const res = await fetch(path, Object.assign({
      headers: { "Content-Type": "application/json" },
    }, opts || {}));
    let body = null;
    try { body = await res.json(); } catch (e) { body = null; }
    if (!res.ok) {
      const detail = body && body.detail ? body.detail : res.statusText;
      throw new Error(detail);
    }
    return body;
  }

  function fillDesks(desks) {
    state.desks = desks;
    const sel = $("genre");
    sel.innerHTML = "";
    desks.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.slug;
      opt.textContent = d.name;
      sel.appendChild(opt);
    });
    const prefer = desks.find((d) => d.slug === "horror") || desks[0];
    if (prefer) sel.value = prefer.slug;
    updateHint();
  }

  function updateHint() {
    const desk = state.desks.find((d) => d.slug === $("genre").value);
    $("deskHint").textContent = desk ? (desk.hint + " " + desk.style_card) : "";
  }

  function words(draft) {
    if (!draft || !draft.signals) return "";
    const s = draft.signals;
    const bits = [];
    if (s.word_count != null) bits.push(s.word_count + " words");
    if (draft.seed != null) bits.push("seed " + draft.seed);
    if (draft.temperature != null) bits.push("temp " + draft.temperature);
    if (draft.mutation) bits.push(draft.mutation);
    if (s.lexical_novelty != null) bits.push("novelty " + s.lexical_novelty);
    if (s.notes && s.notes.length) bits.push(s.notes.join(" · "));
    return bits.join(" · ");
  }

  function render(view) {
    state.view = view;
    const s = view.session;
    const desk = view.desk || {};
    $("startCard").classList.add("hidden");
    $("sessionPane").classList.remove("hidden");
    $("sessionTitle").textContent = desk.name || s.genre;
    $("sessionMeta").textContent =
      "Session " + s.id + " · " + s.length + " · " + s.unit_kind + " " +
      s.unit_index + " · tone: " + s.tone;
    $("champText").textContent = (view.champion && view.champion.text) || "No champion yet.";
    $("champMeta").textContent = words(view.champion);
    $("challText").textContent = (view.challenger && view.challenger.text) ||
      "Generate a challenger to compare. Randomness is rolled every time.";
    $("challMeta").textContent = words(view.challenger);
    $("verdictBar").classList.toggle("hidden", !view.challenger);
    $("nextBtn").classList.toggle("hidden", s.unit_kind === "story");
    $("generateBtn").textContent = view.champion ? "Generate challenger" : "Write first draft";

    const hist = $("history");
    hist.innerHTML = "";
    (view.history || []).slice().reverse().forEach((d) => {
      const li = document.createElement("li");
      li.textContent = "#" + d.id + " · " + d.role + " · seed " + d.seed +
        (d.mutation ? " · " + d.mutation : "");
      hist.appendChild(li);
    });
    if (!hist.children.length) {
      const li = document.createElement("li");
      li.textContent = "The current champion will collect here as you promote.";
      hist.appendChild(li);
    }

    const ul = $("lessons");
    ul.innerHTML = "";
    (view.lessons || []).forEach((l) => {
      const li = document.createElement("li");
      li.textContent = l.rule;
      ul.appendChild(li);
    });
    if (!ul.children.length) {
      const li = document.createElement("li");
      li.textContent = "Verdicts on this desk become lessons the next generate can recall.";
      ul.appendChild(li);
    }
  }

  async function startSession(ev) {
    ev.preventDefault();
    $("startBtn").disabled = true;
    status("Writing first draft…");
    try {
      const view = await api("/api/write/sessions", {
        method: "POST",
        body: JSON.stringify({
          genre: $("genre").value,
          tone: $("tone").value,
          length: $("length").value,
          summary: $("summary").value,
          draft: true,
        }),
      });
      render(view);
      status("First draft is the champion. Generate a challenger when ready.");
    } catch (err) {
      status(err.message, true);
    } finally {
      $("startBtn").disabled = false;
    }
  }

  async function generate() {
    if (!state.view) return;
    $("generateBtn").disabled = true;
    status("Generating with a fresh seed / temperature / mutation…");
    try {
      const view = await api("/api/write/sessions/" + state.view.session.id + "/generate", {
        method: "POST",
        body: "{}",
      });
      render(view);
      status("Compare, then choose Better or Worse. Signals do not pick the winner.");
    } catch (err) {
      status(err.message, true);
    } finally {
      $("generateBtn").disabled = false;
    }
  }

  async function judge(result) {
    if (!state.view || !state.view.challenger) return;
    status("Recording verdict…");
    try {
      const view = await api("/api/write/sessions/" + state.view.session.id + "/judge", {
        method: "POST",
        body: JSON.stringify({ result: result, note: $("verdictNote").value }),
      });
      $("verdictNote").value = "";
      render(view);
      status(result === "better"
        ? "Challenger is the new champion. Desk remembered the lesson."
        : "Champion stays. Challenger discarded. Desk remembered the lesson.");
    } catch (err) {
      status(err.message, true);
    }
  }

  async function nextChapter() {
    if (!state.view) return;
    status("Opening the next chapter…");
    try {
      const view = await api("/api/write/sessions/" + state.view.session.id + "/next", {
        method: "POST",
        body: "{}",
      });
      render(view);
      status("New chapter champion drafted.");
    } catch (err) {
      status(err.message, true);
    }
  }

  async function addRef(ev) {
    ev.preventDefault();
    if (!state.view) return;
    try {
      await api("/api/write/refs", {
        method: "POST",
        body: JSON.stringify({
          genre: state.view.session.genre,
          kind: $("refKind").value,
          title: $("refTitle").value,
          text: $("refText").value,
        }),
      });
      $("refText").value = "";
      const view = await api("/api/write/sessions/" + state.view.session.id);
      render(view);
      status("Reference stored on this desk. Next generate can recall it.");
    } catch (err) {
      status(err.message, true);
    }
  }

  function reset() {
    state.view = null;
    $("sessionPane").classList.add("hidden");
    $("startCard").classList.remove("hidden");
    status("");
  }

  $("startForm").addEventListener("submit", startSession);
  $("genre").addEventListener("change", updateHint);
  $("generateBtn").addEventListener("click", generate);
  $("betterBtn").addEventListener("click", () => judge("better"));
  $("worseBtn").addEventListener("click", () => judge("worse"));
  $("nextBtn").addEventListener("click", nextChapter);
  $("newSessionBtn").addEventListener("click", reset);
  $("refForm").addEventListener("submit", addRef);

  api("/api/write/desks").then(fillDesks).catch((err) => status(err.message, true));
})();
