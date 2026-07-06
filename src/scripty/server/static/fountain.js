/* Scripty fountain classification for the COMPARE BAY (window.scriptyFountain).
   Kept in lockstep with the server classifier (scripty/script/fountain.py
   classify_lines): same slug regex (case-insensitive, INT/EXT variants), same
   character rule (uppercase after stripping (...) extensions, <= 60 chars,
   unicode letters allowed), same structural gates (a character cue needs a
   preceding blank line and a following non-blank, non-slug line). Scene
   anchors are matched against the canonical sluglines of the pass's DB scene
   rows, so slug-looking action/dialogue lines never shift the FILM-thumbnail
   -> scene scroll mapping. XSS-safe: everything is built via the shared elt()
   DOM helper; no innerHTML anywhere. */
"use strict";

(() => {
  const SLUG_RE = /^(INT\.?\/EXT\.?|I\/E|INT|EXT)[. ]/i;

  /* Mirror of fountain._is_character (Python). */
  function isCharacterLine(line) {
    if (line.startsWith("@")) return true;
    if (line.endsWith(":") || line.length > 60) return false;
    const core = line.replace(/\(.*?\)/g, "").trim();
    if (!core || core !== core.toUpperCase()) return false;
    return /\p{L}/u.test(core);
  }

  /* Canonical slugline for a DB scene row — mirrors fountain._slugline for
     the canonical enum values group_scenes stores (INT./EXT./INT./EXT.,
     upper-cased location and time). Non-canonical values simply fail the
     match below and trigger the positional fallback. */
  function expectedSlug(scene) {
    const raw = String(scene.int_ext || "").trim().toUpperCase();
    const ie = raw.includes("/") ? "INT./EXT."
      : raw.startsWith("INT") ? "INT." : "EXT.";
    const loc = String(scene.location || "").trim().toUpperCase() || "UNKNOWN";
    const tod = String(scene.time_of_day || "").trim().toUpperCase() || "DAY";
    return `${ie} ${loc} - ${tod}`;
  }

  /* Map DB scenes to rendered headings by canonical slugline, consuming
     matches left to right; if any expected slugline is missing (e.g.
     non-canonical text), fall back to plain positional mapping. */
  function matchScenes(expected, slugElts, slugLines) {
    if (!expected.length) return slugElts;
    const norm = (s) => s.replace(/\s+/g, " ").trim().toUpperCase();
    const out = [];
    let cursor = 0;
    for (const slug of expected) {
      let found = -1;
      for (let j = cursor; j < slugLines.length; j++) {
        if (norm(slugLines[j]) === slug) { found = j; break; }
      }
      if (found === -1) return slugElts;   // fallback: positional
      out.push(slugElts[found]);
      cursor = found + 1;
    }
    return out;
  }

  /* Classify fountain text into DOM nodes. Returns {nodes, sceneElts};
     sceneElts[i] is the heading element anchoring the i-th DB scene row. */
  function render(elt, fountainText, scenes) {
    const lines = fountainText.split(/\r?\n/);
    const nodes = [];
    const slugElts = [];
    const slugLines = [];
    let current = elt("div", { class: "q-preamble" });
    nodes.push(current);
    let mode = "action", prevBlank = true;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) {
        mode = "action";
        prevBlank = true;
        current.append(elt("div", { class: "q-blank" }));
        continue;
      }
      if (SLUG_RE.test(line)) {
        current = elt("div", { class: "q-scene", id: `q-scene-${slugElts.length}` },
          elt("div", { class: "q-slug" }, line));
        nodes.push(current);
        slugElts.push(current);
        slugLines.push(line);
        mode = "action";
        prevBlank = false;
        continue;
      }
      if (mode === "char" || mode === "dial") {
        current.append(elt("div", { class: /^\(.*\)$/.test(line) ? "q-paren" : "q-dial" }, line));
        mode = "dial";
        prevBlank = false;
        continue;
      }
      const next = (lines[i + 1] || "").trim();
      if (prevBlank && isCharacterLine(line) && next && !SLUG_RE.test(next)) {
        current.append(elt("div", { class: "q-char" }, line));
        mode = "char";
        prevBlank = false;
        continue;
      }
      current.append(elt("div", { class: /^\(.*\)$/.test(line) ? "q-paren" : "q-act" }, line));
      prevBlank = false;
    }
    const expected = (scenes || []).map(expectedSlug);
    return { nodes, sceneElts: matchScenes(expected, slugElts, slugLines) };
  }

  window.scriptyFountain = { render, SLUG_RE, isCharacterLine, expectedSlug };
})();
