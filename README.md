# Scripty

![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![Tests](https://img.shields.io/badge/tests-239%20passing-2ea44f)
![Offline](https://img.shields.io/badge/runs-offline%20by%20default-8a8a8a)
![Platform](https://img.shields.io/badge/desktop-macOS%20(unsigned)-141414)

> A machine script supervisor. Point it at a film; it watches the footage, detects every cut, labels each shot the way a working script supervisor would (`MS CAMP FIRE`), assembles a Hollywood-formatted screenplay, emits a parallel track of generative-video prompts, and takes field-by-field corrections from a dashboard. Every correction is re-read on the next pass, so the machine gets the vocabulary of *your* film right over time — retrieval-based recursive learning, no weight training.

Scripty runs fully offline with a deterministic mock provider, or uses Claude vision + text when Anthropic credentials resolve. It ships as a FastAPI web dashboard and as a packaged macOS Electron app with a four-panel **Compare Bay**.

---

## Table of contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [CLI reference](#cli-reference)
- [The recursive learning loop](#the-recursive-learning-loop)
- [Ground truth mode](#ground-truth-mode)
- [The five-artifact bundle](#the-five-artifact-bundle)
- [Desktop app](#desktop-app)
- [Claude vs. the offline mock](#claude-vs-the-offline-mock)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Running the tests](#running-the-tests)
- [A note on trust](#a-note-on-trust)
- [Further reading](#further-reading)

---

## What it does

- **Detects cuts** with ffmpeg's scene filter and splits the film into shots.
- **Labels each shot** the standard script-supervisor way — scale, subject, angle, movement, INT/EXT, location, time of day, characters, action, mood, confidence — either with Claude vision or a deterministic offline provider.
- **Assembles a screenplay** in [Fountain](https://fountain.io/) markup and renders it to plain-text and standalone HTML in correct Hollywood layout.
- **Transcribes and attributes dialogue** (optional, via faster-whisper) and threads it into the script under the shot it belongs to.
- **Emits a "describe" track** — one generative-video prompt per shot plus an umbrella prompt per scene, threaded with a continuity block (characters, locations, style).
- **Takes field-by-field corrections** from the dashboard — click any cell, fix it, and it's recorded as a `Correction`.
- **Learns recursively.** Corrections are distilled into imperative `Lesson`s; both are recalled and injected into the vision prompt on every future pass. The dashboard plots the rising **agreement rate**.
- **Aligns against ground truth.** Link a known script; Scripty runs a Needleman–Wunsch alignment of its scenes against the real ones and auto-emits corrections from the diffs.
- **Closes the loop.** Feed a generated video back in; Scripty measures SSIM/PSNR, critiques it shot-by-shot, and revises the describe prompts (revision 2).
- **Produces a five-artifact bundle per project:** source video, known script, generated script, describe prompts, and generated video + comparison report.

---

## Architecture

```
                        ┌──────────────────────────────────────────────┐
                        │                  ONE PASS                     │
                        │                                              │
  ┌────────┐  cuts,   ┌─┴──────┐   keyframes   ┌────────┐   shots    ┌─┴──────┐
  │ FILM   │─frames──▶│ INGEST │──────────────▶│ VISION │───────────▶│ SCRIPT │
  │ (.mp4) │  audio   │ ffmpeg │   transcript  │ Claude │  labelled  │fountain│
  └────────┘          └────────┘   ┌────────┐  │ / mock │            │ + HTML │
                                   │ AUDIO  │─▶└───┬────┘            └───┬────┘
                                   │whisper │      │                    │
                                   └────────┘      │              ┌─────▼──────┐
                                                   │              │  DESCRIBE  │
                             recall bundle ────────┤              │ gen-AI     │
                             (lessons + examples)  │              │ prompts    │
                                   ▲               │              └────────────┘
                                   │               ▼
                              ┌────┴─────┐   ┌───────────┐   ┌──────────────┐
                              │  LEARN   │◀──│ CORRECTIONS│◀──│  DASHBOARD   │
                              │ distill  │   │  (human /  │   │ click-to-fix │
                              │ recall   │   │  truth)    │   └──────────────┘
                              │ agreement│   └─────┬──────┘
                              └────┬─────┘         │
                                   │          ┌────▼─────┐    ┌──────────┐
                          next pass│          │  TRUTH   │◀───│  KNOWN   │
                          honors   │          │ align    │    │  SCRIPT  │
                          the fix  │          │ (N-W)    │    └──────────┘
                                   │          └──────────┘
                                   │
                              ┌────▼──────┐   ┌───────────────┐
                              │  COMPARE  │◀──│ GENERATED     │
                              │ SSIM/PSNR │   │ VIDEO         │
                              │ re-prompt │   └───────────────┘
                              └───────────┘
```

The pipeline is: **ingest → vision → script / describe → learn → truth → compare.** Learning is a *loop*, not a stage: corrections recorded in the dashboard (or auto-emitted by ground-truth alignment) feed the recall bundle that the vision provider reads on the next pass.

For the module-by-module breakdown, the event-sourced schema, and the protocol seams, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Quick start

**Prerequisites:** Python 3.11, [ffmpeg](https://ffmpeg.org/) and `ffprobe` on your `PATH`, and a POSIX shell. macOS or Linux.

```bash
# 1. Clone
git clone <your-fork-url> scripty
cd scripty

# 2. Create a virtualenv and install (editable, with dev extras)
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. ffmpeg must be on PATH
brew install ffmpeg           # macOS; use your package manager on Linux

# 4. Build a synthetic film and run a full offline pass over it
scripty demo

# 5. Open the dashboard
scripty serve                 # http://127.0.0.1:8787/
```

`scripty demo` builds a tiny multi-shot test film with ffmpeg (no download, no API key), runs a supervision pass with the mock provider, and writes a full artifact set. Then `scripty serve` gives you the dashboard to inspect it, correct it, and re-run.

Optional real transcription:

```bash
pip install -e ".[whisper]"   # adds faster-whisper; auto-selected when present
```

---

## CLI reference

The console entry point is `scripty` (defined in `pyproject.toml` as `scripty.cli:main`). Every command below exists exactly as written.

| Command | What it does |
| --- | --- |
| `scripty create VIDEO [--name NAME]` | Probe a video with ffprobe and register it as a project. Prints the new project id. |
| `scripty pass PROJECT_ID [--provider P] [--transcriber T] [--brain B]` | Run one full supervision pass over a project. Prints progress lines and `pass N complete`. |
| `scripty serve [--port 8787] [--host 127.0.0.1]` | Start the FastAPI dashboard. |
| `scripty truth PROJECT_ID SCRIPT_PATH` | Link a known ground-truth script (`.fountain`/`.txt`) to a project. |
| `scripty align PASS_ID [--emit-corrections / --no-emit-corrections]` | Align a pass against the linked truth and (by default) emit corrections from the diffs. |
| `scripty lessons distill [--project PID]` | Distill fresh corrections into imperative lessons. |
| `scripty lessons list [--project PID]` | List active, weight-ranked lessons. |
| `scripty describe export PASS_ID [--target T] [--revision R]` | Print the describe-prompt sheet for a pass. |
| `scripty compare PROJECT_ID PASS_ID GENERATED_VIDEO` | Compare a generated video back against the original; revises describe prompts. |
| `scripty demo` | Build a synthetic film and run a fully offline pass on it. |
| `scripty log PASS_ID` | Print the classic cut log for a pass. |
| `scripty export-canon PROJECT_ID -o FILE [--pass ID] [--tier S] …` | Export a pass as a director-bot canon work bundle (JSON). |

Notes on the flags:

- `--provider` / `--transcriber` / `--brain` accept `anthropic`, `mock`, or (for the transcriber) `whisper` / `none`. When omitted, Scripty auto-selects: `anthropic` if credentials resolve, else `mock`; `whisper` if faster-whisper is installed, else `none`.
- `align --emit-corrections` is the default; pass `--no-emit-corrections` to align without recording anything.
- `describe export --target` filters by generative target (`generic`, `sora`, `veo`, `runway`, …); `--revision` selects a specific revision of the prompt set.

A typical run:

```bash
scripty create ~/footage/short.mp4 --name "Wooded Road"   # -> created project 1
scripty pass 1                                             # -> pass 1 complete
scripty log 1                                              # inspect the cut log
scripty serve                                              # correct it in the dashboard
scripty pass 1                                             # -> pass 2 honors your corrections
```

---

## The recursive learning loop

Scripty does **not** fine-tune a model. It learns by *retrieval*: your corrections and the lessons distilled from them are stored in one SQLite database, recalled at pass time, and injected into the vision prompt where they explicitly outrank the model's first instinct.

### Worked example — WOODED ROAD

**Pass 1.** Scripty looks at a shot of trees with a dirt track running through them and labels the scene heading:

```
EXT. WOODS - NIGHT
```

Close, but a script supervisor would call that location a **wooded road** — the road is the point. In the dashboard, you click the location cell and correct it:

| field | model value | your value |
| --- | --- | --- |
| `location` | `WOODS` | `WOODED ROAD` |

That single edit does three things:

1. It's applied to the live row, so the script re-renders immediately as `EXT. WOODED ROAD - NIGHT`.
2. It's stored as a `Correction` in the global database.
3. On **Distill**, it's condensed into an imperative `Lesson`, e.g. *"When trees line both sides of a visible road, label the location WOODED ROAD, not WOODS."*

**Pass 2.** Before analyzing each shot, Scripty calls `recall()` for the hot fields and builds a `RecallBundle`. Its `format_for_prompt()` renders your history into the system prompt as **SUPERVISOR NOTES (learned from prior passes — apply these)**:

```
- RULE [location]: When trees line both sides of a visible road, label the location WOODED ROAD, not WOODS.
- CORRECTION [location]: you previously said 'WOODS'; the supervisor corrected it to 'WOODED ROAD'
```

The vision provider is contractually required to honor recall over instinct. Pass 2 labels the shot `EXT. WOODED ROAD - NIGHT` on its own.

**The metric.** After every pass, `agreement_metrics()` checks each prior correction against the corresponding entity in the new pass: does the same field now equal the human value? The fraction that agree is the **agreement rate** — the headline learning-curve number the dashboard plots per pass. Pass 1 disagreed with you; pass 2 agrees; the curve rises. That is the whole point of the tool made visible.

The mock provider honors recall too (it substitutes the corrected value when an example matches), so you can watch the entire loop rise from `null` to `1.0` completely offline: run `scripty demo` for the first pass, correct a field in the dashboard, distill, then `scripty pass <id>` again and read the agreement rate climb — no API key required.

---

## Ground truth mode

If you already have the real script for a film, you don't have to correct by hand. Link it and let Scripty diff itself against it:

```bash
scripty truth 1 ~/scripts/wooded-road.fountain   # link the known script
scripty align 2                                   # align pass 2 against it
```

`align` runs a **Needleman–Wunsch** global alignment between Scripty's scene sequence and the truth's, scoring each candidate pair as `0.5·fuzz(location) + 0.25·(int_ext match) + 0.25·(time match)`. It also fuzzy-matches dialogue line by line. Then, unless you pass `--no-emit-corrections`, every scene-heading disagreement becomes a `source='ground_truth'` correction — consumed by the learning loop exactly like a human dashboard edit. Run `scripty lessons distill` and the next pass improves without a single manual click.

The alignment report drives the dashboard's **Truth** tab (green/amber/red per scene heading) and the **agreement / slugline-accuracy** curves.

---

## The five-artifact bundle

Every project accumulates up to five artifact kinds (tracked in the `artifacts` table, surfaced in the dashboard's **Bundle** tab):

1. **Source video** — the film you ingested.
2. **Known script** — the ground-truth screenplay, if linked.
3. **Generated script** — Scripty's `passN.fountain` (+ `passN.html` render).
4. **Describe set** — `passN_prompts.txt`, the generative-video prompt series.
5. **Generated video + comparison report** — a video produced from the prompts, plus the SSIM/PSNR + critique JSON from `scripty compare`.

They land on disk under `$SCRIPTY_HOME/projects/<slug>/` (see [Project layout](#project-layout)).

---

## Desktop app

Scripty ships a macOS Electron shell that boots the Python backend on a free localhost port and opens the dashboard in a native window — including the four-panel **Compare Bay** view (`FILM | KNOWN SCRIPT | SCRIPTY'S SCRIPT | DESCRIBE`) with whole-window drag-and-drop.

There are two build flavors:

```bash
cd desktop
npm install
npm run smoke     # headless boot check -> prints "SMOKE OK"

# Dev build — thin shell; runs the backend from your local .venv
npm run pack      # unsigned .app at desktop/dist/mac-arm64/Scripty.app
npm run dist      # .dmg (needs Python venv + ffmpeg on the target machine)
```

```bash
# Self-contained build — the one to hand to someone else.
# Freezes the Python backend (PyInstaller) and bundles a relocated
# ffmpeg/ffprobe, so the .app runs on a bare Mac with no Python,
# no venv, and no Homebrew installed.
bash packaging/build_standalone.sh   # -> desktop/dist/Scripty-<ver>-arm64.dmg
```

The self-contained `.dmg` on the [Releases page](https://github.com/CourtReinland/scripty/releases) needs **nothing** installed — download, drag to Applications, open. The only requirement is Apple Silicon.

The app is **unsigned**, so on first launch Gatekeeper will block it; right-click the app and choose **Open**, or clear the quarantine bit:

```bash
xattr -dr com.apple.quarantine /Applications/Scripty.app
```

A packaged app resolves its backend and ffmpeg from inside the bundle (`Contents/Resources/backend` and `Contents/Resources/ffmpeg`); a dev build falls back to `SCRIPTY_BACKEND_BIN` or the local `.venv`. Full build details, the freeze/bundle steps, and the icon step are in [`docs/BUILD.md`](docs/BUILD.md).

---

## Claude vs. the offline mock

Every real provider in Scripty has a **mock twin**, so the whole pipeline runs offline and every one of the 239 tests passes with no API key.

| Concern | Real | Mock / offline |
| --- | --- | --- |
| Vision (shot labels) | `AnthropicVision` — Claude vision, model from `SCRIPTY_VISION_MODEL` (default `claude-opus-4-8`) | `MockVision` — deterministic labels from frame-byte hashes; still applies recall |
| Text (distill / polish) | `AnthropicBrain` — Claude, model from `SCRIPTY_TEXT_MODEL` | `MockBrain` — echoes the first sentences; distill has a deterministic fallback |
| Transcription | `WhisperTranscriber` — faster-whisper (optional extra) | `NullTranscriber` — returns no dialogue |

**How the provider is chosen:** `config.default_provider()` returns `anthropic` when Anthropic credentials resolve (an `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` env var, or a populated `~/.config/anthropic/credentials`), otherwise `mock`. Force it either way with `SCRIPTY_PROVIDER=mock` (or `anthropic`), or per-command with `--provider`.

No secrets live in the code: the Anthropic SDK resolves credentials itself and is constructed with no arguments.

---

## Configuration

Everything is overridable by environment variable.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SCRIPTY_HOME` | `~/.scripty` | Root data directory (one global SQLite db + all project artifacts). |
| `SCRIPTY_PROVIDER` | auto | Force the vision/brain provider (`anthropic` / `mock`). |
| `SCRIPTY_TRANSCRIBER` | auto | Force the transcriber (`whisper` / `none`). |
| `SCRIPTY_VISION_MODEL` | `claude-opus-4-8` | Claude model for vision. |
| `SCRIPTY_TEXT_MODEL` | `claude-opus-4-8` | Claude model for text. |
| `SCRIPTY_SCENE_THRESHOLD` | `0.30` | ffmpeg scene-cut sensitivity. |
| `SCRIPTY_FRAMES_PER_SHOT` | `3` | Keyframes sampled per shot (at 15% / 50% / 85%). |
| `SCRIPTY_MIN_SHOT_SECONDS` | `0.40` | Minimum shot length; shorter cuts are merged. |
| `SCRIPTY_RECALL_LESSONS` | `8` | Lessons pulled into the recall bundle. |
| `SCRIPTY_RECALL_EXAMPLES` | `5` | Correction examples pulled into the recall bundle. |
| `SCRIPTY_PORT` | `8787` | Default dashboard port. |
| `SCRIPTY_MAX_UPLOAD_MB` | `4096` | Cap on dashboard/desktop file uploads. |

Data lives in **one** SQLite database at `$SCRIPTY_HOME/scripty.db` on purpose: lessons distilled on one film transfer to the next.

---

## Project layout

```
scripty/
├── pyproject.toml               # deps, python 3.11, `scripty` entry point
├── src/scripty/
│   ├── core/                    # frozen contract: models, interfaces, db, config
│   │   ├── models.py            #   dataclasses + enums (Shot, Scene, Correction, Lesson, …)
│   │   ├── interfaces.py        #   VisionProvider / TextBrain / Transcriber Protocols
│   │   ├── db.py                #   event-sourced SQLite wrapper
│   │   └── config.py            #   env vars, paths, provider selection
│   ├── ingest/                  # ffmpeg: probe, detect cuts, keyframes, audio, test film
│   ├── vision/                  # shot taxonomy + AnthropicVision / MockVision
│   ├── audio/                   # whisper transcription + speaker assignment
│   ├── script/                  # scene grouping, Fountain, text/HTML render, cut log
│   ├── describe/                # generative-video prompt generation
│   ├── learn/                   # recall, distill, agreement metrics
│   ├── truth/                   # Needleman–Wunsch alignment + auto-corrections
│   ├── compare/                 # SSIM/PSNR + shot critique + re-prompting
│   ├── server/                  # FastAPI app, ingest API, static dashboard (+ Compare Bay)
│   ├── brain.py                 # AnthropicBrain / MockBrain
│   ├── pipeline.py              # create_project, run_pass, demo
│   └── cli.py                   # the `scripty` command
├── desktop/                     # Electron shell (main.js, preload.js, build/gen_icon.sh)
├── scripts/make_test_film.py    # standalone synthetic-film generator
├── tests/                       # 239 tests, all offline
└── docs/                        # ARCHITECTURE.md, BUILD.md, USAGE.md
```

---

## Running the tests

```bash
source .venv/bin/activate
python -m pytest -q
```

239 tests pass **offline** with no API key. They mock the LLM / ffmpeg / network boundary (London-school), except for one `@pytest.mark.integration` test that builds a real 2-shot film with ffmpeg and runs cut detection on it — it runs by default and finishes in under 20 seconds, so ffmpeg must be installed.

---

## A note on trust

Scripty is a **local-first, single-user** tool. Some deliberate consequences:

- The dashboard binds to loopback only and rejects non-local `Host` headers (defeats DNS rebinding) and cross-origin state-changing requests (defeats CSRF).
- The keyframe media endpoint is path-traversal-locked — it only serves files under `$SCRIPTY_HOME/projects`.
- The Electron renderer runs sandboxed with `contextIsolation` on and navigation guards that keep it on the local backend origin.
- The macOS app is **unsigned and un-notarized** — it's meant to run on your own machine, not to be distributed. Expect the Gatekeeper prompt on first launch.

---

## Further reading

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — modules, contract, event-sourced schema, protocol seams, the learning design, Needleman–Wunsch alignment.
- [`docs/BUILD.md`](docs/BUILD.md) — building and packaging the macOS app.
- [`docs/USAGE.md`](docs/USAGE.md) — task-oriented walkthroughs: analyze a film, correct it, train against ground truth, read the describe prompts, close the compare loop.
