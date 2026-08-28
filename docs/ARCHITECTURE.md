# Architecture

The product surface is a **human-in-the-loop fiction trainer** (`scripty.write`). The film script-supervisor remains in the same package as a legacy pipeline.

## Writer loop (current product)

```
start_session(genre, tone, length, summary)
        │
        ▼
   generate()  → first draft becomes CHAMPION
        │
        │  roll seed + temperature + prompt mutation
        ▼
   generate()  → CHALLENGER (signals are display-only)
        │
        ▼
   judge(better|worse)   # human is the only scorer
        │
        ├─ better → challenger is champion; old champion retired
        └─ worse  → challenger discarded; champion unchanged
        │
        ▼
   desk lesson distilled from the verdict → recalled on the next generate
```

Genre desks (`write/desks.py`) are style cards in our own words (short horror, literary, romance, thriller, slice-of-life, sci-fi, custom, plus fantasy and mystery). Optional references are `user_excerpt` or `public_domain` chunks the user uploads. They are stored privately and reduced to abstract style notes (pace, speech, opening move) before they touch a prompt. Raw reference sentences are scrubbed out of drafts and never returned by the API.

Persistence is additive SQLite tables (`write_desks`, `write_sessions`, `write_drafts`, `write_verdicts`, `write_lessons`, `write_refs`) on the same database as the film stack. Live fiction uses `XaiBrain` (`grok-4.6`, `reasoning_effort=xhigh`, `https://api.x.ai/v1`) when `XAI_API_KEY` is set. `AnthropicBrain` remains for the legacy film/distill path. `MockBrain` is the pytest twin: it detects `SCRIPTY_WRITER` and emits seed-varied original prose. Challenger generates roll a new seed, a higher temperature, and a combined prose+plan mutation so randomness is exploration, not decoration.

The dashboard at `/` is the pairwise compare UI. `/film` is the legacy supervisor.

---

The remainder of this document describes the **legacy film supervisor**: a pipeline of ten modules around a frozen core contract. Every module that touches an external system (Claude, ffmpeg, whisper) sits behind a `Protocol` and ships a deterministic mock twin.

## Table of contents

- [The shape of a pass](#the-shape-of-a-pass)
- [The core contract](#the-core-contract)
- [The ten modules](#the-ten-modules)
- [The event-sourced database](#the-event-sourced-database)
- [The protocol seam and why every provider has a mock twin](#the-protocol-seam-and-why-every-provider-has-a-mock-twin)
- [Retrieval-based recursive learning](#retrieval-based-recursive-learning)
- [Ground-truth alignment (Needleman–Wunsch)](#ground-truth-alignment-needlemanwunsch)
- [The five-artifact bundle](#the-five-artifact-bundle)
- [Security posture](#security-posture)

---

## The shape of a pass

A **pass** is one complete supervision run over a project's video. `pipeline.run_pass()` orchestrates it:

1. **Create the pass row** (`db.create_pass`, status `running`). The pass number is `MAX(number)+1` computed inside the INSERT so concurrent runs can't mint duplicates.
2. **Detect cuts** and turn them into shot spans (`ingest.detect_cuts` → `ingest.cuts_to_shots`).
3. **Extract keyframes** — 3 per shot at 15% / 50% / 85% — into `keyframes/passN/`.
4. **Extract audio and transcribe** (`ingest.extract_audio` → transcriber → `audio.assign_speakers`).
5. **Build the recall bundle** once (`_merged_recall`), covering the hot shot fields.
6. **Analyze each shot** with the vision provider, passing the recall bundle; write a `shots` row per shot.
7. **Map dialogue to shots** (`audio.dialogue_from_segments`) and persist it.
8. **Group scenes** (`script.group_scenes`) and backfill `scene_id` onto dialogue rows.
9. **Write screenplay artifacts** — `passN.fountain` + `passN.html` — and register a `generated_script` artifact.
10. **Generate describe prompts** (`describe.generate_prompts`) — one per shot, one umbrella per scene — write `passN_prompts.txt`, register a `describe_set` artifact.
11. **Compute metrics** — shot/scene/dialogue counts, mean confidence, and the **agreement metrics**. If a truth script is linked, run alignment and fold in `slugline_accuracy` and dialogue-match numbers.
12. **Finish the pass** (`db.finish_pass`, status `complete` with metrics). Any exception marks the pass `failed` and re-raises.

Cross-module imports happen *inside* function bodies, so `cli.py`, `pipeline.py`, and the server import cleanly even while a sibling module is mid-build, and unit tests can inject mocks via `sys.modules`.

---

## The core contract

`src/scripty/core/` is the frozen contract every other module depends on. Nothing outside `core/` may change these files' public shapes.

### `models.py` — the domain vocabulary

Dataclasses and enums, following working script-supervisor convention:

- **Enums:** `ShotScale` (ECU…EWS, OTS, POV, INSERT, 2-SHOT, AERIAL), `CameraAngle`, `CameraMove`, `IntExt` (`INT.`/`EXT.`/`INT./EXT.`), `TimeOfDay`, `ArtifactKind`.
- **Media / project:** `MediaInfo`, `Project`, `Pass` (status `pending | running | complete | failed`).
- **Shots / scenes / dialogue:** `ShotAnalysis` (what the vision pass believes — all fields correctable), `Shot` (persisted; `.label()` returns `"MS CAMP FIRE"`, `.log_line()` a cut-log row), `CharacterSighting`, `SceneHeading` (`.slugline()` returns `"EXT. WOODED ROAD - NIGHT"`), `Scene`, `TranscriptSegment`, `DialogueLine`.
- **Learning:** `Correction`, `Lesson`, `RecallBundle` (its `.format_for_prompt()` renders lessons + examples into the "SUPERVISOR NOTES" block).
- **Describe / compare:** `DescribePrompt`, `VideoMetrics`, `CritiqueNote`, `ShotContext`.
- **Helpers:** `parse_enum` (lenient coercion of model output — accepts `"int"`, `"Ext."`, `"worms eye"`, `"I/E"`), `timecode` (`HH:MM:SS:FF`), `to_json`, `shot_from_analysis`, and `CORRECTABLE_FIELDS` (the whitelist a correction's `field` must belong to).

### `interfaces.py` — the protocol seam

Three `runtime_checkable` `Protocol`s: `VisionProvider` (`analyze_shot`, `critique_frames`), `TextBrain` (`complete`), `Transcriber` (`transcribe`). Each carries a `name` attribute. Vision providers **must** honor the recall bundle — lessons and prior corrections take precedence over the model's own first impression.

### `db.py` — persistence

The `Database` class: one SQLite connection, one lock, JSON columns transparently encoded/decoded, and an append-only `events` log alongside the projection tables. Detailed below.

### `config.py` — runtime knobs

Paths (`home()`, `db_path()`, `project_dir()`), model names, thresholds, and provider selection (`default_provider()`, `default_transcriber()`). Everything reads from environment variables with sane defaults. No secrets — the Anthropic SDK resolves its own credentials.

---

## The ten modules

| # | Module | Responsibility |
| --- | --- | --- |
| 1 | `ingest` | ffmpeg/ffprobe boundary: `probe`, `detect_cuts`, `cuts_to_shots`, `extract_keyframes`, `extract_audio`, and the synthetic `testfilm` generator. |
| 2 | `vision` | Shot taxonomy + prompt building; `AnthropicVision` and `MockVision` implementing `VisionProvider`; `get_provider()`. |
| 3 | `audio` | `WhisperTranscriber` / `NullTranscriber` / `MockTranscriber`; naive speaker alternation; `dialogue_from_segments` mapping segments to shots. |
| 4 | `script` | `group_scenes`, `cut_log`, `to_fountain`, a tolerant `parse_fountain`, and `render_text` / `render_html` in Hollywood layout. |
| 5 | `describe` | `build_continuity` + `generate_prompts` (deterministic template, optionally brain-polished) + `export_text`. |
| 6 | `learn` | `record_correction`, `recall` (builds the `RecallBundle`), `agreement_metrics`, and `distill` (corrections → lessons). |
| 7 | `truth` | `link_script`, `align_pass` (Needleman–Wunsch), `emit_auto_corrections`. |
| 8 | `compare` | `compare_videos` (SSIM/PSNR), `critique_shots`, `revise_prompts`, `run_comparison`. |
| 9 | `pipeline` + `cli` + `brain` | Orchestration (`create_project`, `run_pass`, `demo`), the Typer CLI, and `AnthropicBrain` / `MockBrain`. |
| 10 | `server` | FastAPI `create_app`, the drag-drop/background-pass `ingest_api` router, and the static dashboard (Supervisor + Compare Bay). |

Each module keeps its files under 500 lines, uses `from __future__ import annotations`, and type-hints its public functions.

---

## The event-sourced database

One global SQLite database at `$SCRIPTY_HOME/scripty.db`. Every state change is written twice: to a **projection table** (fast dashboard queries) and to the append-only **`events`** log (auditable history — the substrate for learning across passes). JSON-typed columns are encoded on write and decoded on read.

### Tables

| Table | Purpose | JSON columns |
| --- | --- | --- |
| `events` | Append-only log: `project_created`, `pass_started`, `shot_analyzed`, `correction_added`, `lesson_distilled`, `truth_linked`, `artifact_saved`, `pass_finished`, … | `payload` |
| `projects` | One row per film. `slug` is unique. | — |
| `passes` | One row per supervision run. `number` is per-project; `status`, `params`, `metrics`. | `params`, `metrics` |
| `shots` | One row per detected shot: timing, the full label vocabulary, `confidence`, `keyframes`, and the provider's `raw` answer. | `characters`, `keyframes`, `raw` |
| `scenes` | Scenes grouped from shots: heading fields, `shot_ids`, `synopsis`. | `shot_ids` |
| `dialogue` | Attributed dialogue lines, linked to shot and scene. | — |
| `corrections` | Field-level human/ground-truth fixes: `entity_type`, `field`, `model_value`, `human_value`, `source`, `scope`. | — |
| `lessons` | Imperative rules distilled from corrections: `field`, `rule`, `source_ids`, `weight`, `active`. | `source_ids` |
| `truth_links` | Linked ground-truth scripts, parsed. | `parsed` |
| `alignments` | One alignment report per pass. | `report` |
| `describe_prompts` | Generative-video prompts: `revision`, `target`, `prompt`, `continuity`. | `continuity` |
| `artifacts` | The five-artifact bundle registry: `kind`, `ref`, optional `pass_id`, `meta`. | `meta` |

Indexes exist on `shots(pass_id, idx)`, `corrections(project_id, field)`, and `lessons(field, active)` — the three hot lookup paths (rendering a pass, recalling corrections by field, recalling active lessons by field).

Two correctness details worth knowing:

- **Pass numbering is race-free.** `create_pass` computes `COALESCE(MAX(number),0)+1` *inside* the INSERT, so two concurrent runs (even from separate processes) never read the same maximum.
- **Corrections and lessons are global-aware.** `db.corrections()` and `db.lessons()` include rows scoped `global` (or with a null `project_id` for lessons) alongside the requested project's rows — this is how a lesson learned on one film transfers to the next.

---

## The protocol seam and why every provider has a mock twin

Vision, transcription, and text generation are the three points where Scripty would otherwise need the network. Each is a `Protocol` in `core/interfaces.py`, and each has two implementations:

| Protocol | Real | Mock twin |
| --- | --- | --- |
| `VisionProvider` | `AnthropicVision` — Claude vision, defensive JSON parse, degraded `ShotAnalysis` (confidence 0.1) on API failure instead of raising | `MockVision` — derives a pseudo-analysis from frame-byte hashes + average pixel color (stdlib only, no PIL) |
| `TextBrain` | `AnthropicBrain` — Claude Messages API, adaptive thinking, no temperature/top-p, raises `RuntimeError` on failure | `MockBrain` — echoes the first sentences of the input |
| `Transcriber` | `WhisperTranscriber` — faster-whisper (guarded import; optional extra) | `NullTranscriber` (no dialogue) and `MockTranscriber` (canned segments for tests) |

The mock twins are not stubs — they are *deterministic real behavior*. Crucially, `MockVision` **applies recall**: if a prior correction's field matches, it emits the corrected `human_value` instead of its own guess, and it best-effort honors "prefer X over Y" lessons by substring. That is what lets the entire recursive-learning loop — including the rising agreement curve — be demonstrated and tested with zero API calls. `config.default_provider()` picks `anthropic` when credentials resolve and `mock` otherwise; `SCRIPTY_PROVIDER` forces either.

This seam is also why the modules could be built in parallel: each agent implemented one module against the frozen `core/` contract, mocking the boundary in its own tests, and the pieces composed.

---

## Retrieval-based recursive learning

Scripty learns **without training a model**. The loop is:

```
correction ──▶ distill ──▶ lesson
    │                          │
    └──────────┐    ┌──────────┘
               ▼    ▼
           recall bundle  ──▶ injected into the vision prompt (outranks instinct)
                                        │
                                        ▼
                              next pass' shot labels
                                        │
                                        ▼
                              agreement_metrics  ──▶ agreement_rate (dashboard curve)
```

### Corrections

A `Correction` is a single field-level fix: `(entity_type, entity_id, field, model_value, human_value)`. `learn.memory.record_correction` validates `field ∈ CORRECTABLE_FIELDS`, stores the row, **and immediately applies it to the live projection row** so the dashboard updates without a re-run (for `shots.characters` it splits on commas; for a scene `slugline` it parses `"INT. X - NIGHT"` back into `int_ext`/`location`/`time_of_day` via `parse_enum`). Corrections come from two sources: `human` (dashboard edits) and `ground_truth` (auto-emitted by alignment).

### Distillation

`learn.distill.distill` groups fresh corrections by field and asks the brain to write 1–3 short imperative rules per group (*"When trees line both sides of a visible road, label the location WOODED ROAD, not WOODS"*). With no brain (or on error) it falls back to a deterministic rule: *"Prefer '{human}' over '{model}' when similar context recurs."* It skips corrections already cited in an existing lesson's `source_ids`, so re-distilling is idempotent.

### Recall

`learn.memory.recall` builds a `RecallBundle` for a given field: field-specific lessons plus general (`field='*'`) lessons ranked by `weight`, and correction examples ranked by `rapidfuzz` token-set similarity to the query text (or by recency). `pipeline._merged_recall` calls it across the six hot shot fields (`scale`, `subject`, `location`, `time_of_day`, `int_ext`, `action_text`), dividing the `k` budget per field and deduplicating. The bundle's `format_for_prompt()` renders it into the vision system prompt under a header instructing the provider to apply the notes.

### The agreement metric

`learn.memory.agreement_metrics` is the learning-curve number. For every prior correction on earlier passes, it finds the corresponding entity in *this* pass (shots by `idx`, scenes by `idx`, dialogue by order) and asks: does that field now equal the `human_value` (case-insensitive)? It returns `corrections_checked`, `now_agreeing`, `agreement_rate`, and a `by_field` breakdown. Plotted per pass, a rising `agreement_rate` is direct evidence the machine is internalizing your corrections.

---

## Ground-truth alignment (Needleman–Wunsch)

`truth.align.align_pass` performs a **global sequence alignment** between the scenes Scripty generated and the scenes of a linked known script — the same DP algorithm used for biological sequence alignment.

- **Scoring.** Each candidate scene pair scores `0.5·fuzz(location) + 0.25·(int_ext match) + 0.25·(time_of_day match)`, where `fuzz` is `rapidfuzz.fuzz.ratio/100`. The gap penalty is `-1`.
- **DP + traceback.** A standard `(n+1)×(m+1)` score matrix is filled, then traced back to produce matched pairs plus lists of unmatched (gapped) model and truth scenes.
- **Dialogue.** Each truth dialogue line is matched to its best model line by `partial_ratio`; a score above 70 counts as a hit, and character names are compared after stripping `(O.S.)` / `(V.O.)` / `(CONT'D)` extensions.
- **Report.** `{scene_pairs: [{model_idx, truth_idx, score, diffs}], unmatched_model, unmatched_truth, dialogue: {truth_lines, matched, avg_score, character_accuracy}, slugline_accuracy}` — stored in `alignments` and returned.

`emit_auto_corrections` then turns the report into corrections: one wrong heading field becomes a correction on that field; two or more wrong become a single `slugline` correction carrying the merged heading. All are `source='ground_truth'`, so from the learning loop's perspective they are indistinguishable from human edits.

---

## The five-artifact bundle

`ArtifactKind` enumerates the bundle Scripty accumulates per project, recorded in the `artifacts` table:

1. `source_video` — registered at `create_project`.
2. `known_script` — registered by `truth.link_script`.
3. `generated_script` — `passN.fountain`, with the HTML render path in `meta`.
4. `describe_set` — `passN_prompts.txt`, with the prompt count in `meta`.
5. `generated_video` + `comparison_report` — registered by `compare.run_comparison`.

The dashboard's **Bundle** tab surfaces these five slots with per-slot status, making a project's completeness legible at a glance.

---

## Security posture

Scripty is local-first and single-user, and the security model reflects that (see `server/app.py`):

- **Loopback + guards.** The dashboard rejects any request whose `Host` header isn't a local name (defeats DNS rebinding) and rejects cross-origin state-changing requests via an `Origin` check (defeats CSRF). Non-browser local clients (the CLI, the Electron main process) send no `Origin` and pass through.
- **Path-locked media.** `/media/keyframe` resolves the requested path and `403`s unless it's under `$SCRIPTY_HOME/projects` (`is_relative_to`). `/media/video/{id}` only serves the exact path recorded for that project.
- **Upload hardening.** Multipart uploads are basename-sanitized, reject control characters and dot-only names, dedupe with `O_EXCL` (no TOCTOU race), enforce a size cap, and never leave partial files behind.
- **Stale-pass reconciliation.** Pass workers are daemon threads; if the server dies mid-pass, `_sweep_stale_passes` fails any lingering `running` rows at the next startup so `RUN PASS` isn't blocked forever.
- **Sandboxed Electron.** The renderer runs with `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, a deny-all window-open handler, and a `will-navigate` guard that keeps it pinned to the local backend origin. The preload exposes only `pathForFile`, `onLinkScript`, and `platform`.
