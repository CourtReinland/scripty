# Usage

## Fiction trainer (current)

```bash
source .venv/bin/activate
scripty write start -g thriller -t "tight" -l short \
  -s "A courier has ninety minutes to deliver a key she has already lost." \
  --ref-file ./my-cleaned-chunk.txt   # optional, never printed back
scripty write generate 1
scripty write judge 1 better --note "faster cut"
scripty serve    # http://127.0.0.1:8787/
```

In the UI: pick a desk, set tone/length/summary, optionally upload a private chunk, write the first draft, generate a challenger, click Better or Worse. References never reappear as text. Signals are advisory. Live prose uses grok-4.6 / xhigh when `XAI_API_KEY` is set.

Medium/long sessions write chapter 1 first; `scripty write next ID` (or **Next chapter** in the UI) starts the next unit after you are happy with the champion.

---

## Legacy film supervisor

Task-oriented walkthroughs for the older film pipeline. ffmpeg must be on your `PATH`.

## Table of contents

- [Kick the tires: the demo](#kick-the-tires-the-demo)
- [Analyze a real film end to end](#analyze-a-real-film-end-to-end)
- [Correct shots in the dashboard](#correct-shots-in-the-dashboard)
- [The recursive learning loop](#the-recursive-learning-loop)
- [Ground-truth training: truth + align](#ground-truth-training-truth--align)
- [Reading the describe prompts](#reading-the-describe-prompts)
- [The compare / re-prompt loop](#the-compare--re-prompt-loop)
- [The Compare Bay (four-panel view)](#the-compare-bay-four-panel-view)
- [Where artifacts land on disk](#where-artifacts-land-on-disk)

---

## Kick the tires: the demo

The fastest way to see everything working, with no video and no API key:

```bash
scripty demo
```

This builds a small synthetic multi-shot film with ffmpeg (into `$SCRIPTY_HOME/demo.mp4`), registers it as a project, and runs one full offline pass with the mock provider. It prints the project and pass ids and a hint to run `scripty serve`. Because the mock provider honors recall, running a *second* pass after correcting a field (`scripty pass <project_id>`) demonstrates the whole learning loop offline.

```bash
scripty serve        # then open http://127.0.0.1:8787/
```

---

## Analyze a real film end to end

### 1. Register the film

```bash
scripty create ~/footage/wooded-road.mp4 --name "Wooded Road"
# -> created project 1
```

`create` probes the file with ffprobe and records its duration, fps, and dimensions. `--name` is optional (it defaults to the filename stem).

### 2. Run a pass

```bash
scripty pass 1
```

This detects cuts, extracts keyframes, transcribes audio (if a transcriber is available), labels every shot, groups scenes, and writes the screenplay + describe artifacts. Progress lines stream as it goes; it ends with `pass N complete`.

Provider selection is automatic — `anthropic` if credentials resolve, else `mock`. Force it explicitly:

```bash
scripty pass 1 --provider anthropic         # use Claude vision
scripty pass 1 --provider mock              # deterministic, offline
scripty pass 1 --transcriber whisper        # real transcription (needs the whisper extra)
scripty pass 1 --transcriber none           # skip dialogue
```

### 3. Inspect the cut log

```bash
scripty log 1
```

Prints the classic script-supervisor cut log — one line per shot:

```
0001  00:00:00:00 - 00:00:03:12  MS CAMP FIRE
0002  00:00:03:12 - 00:00:07:00  WS WOODED ROAD
...
```

### 4. Open the dashboard

```bash
scripty serve
```

Pick the project and pass from the left rail. The center panel is the shot log; the right rail has **Script**, **Describe**, **Truth**, **Lessons**, **Metrics**, and **Bundle** tabs.

---

## Correct shots in the dashboard

In the **Supervisor** view, the center shot table shows every field the vision pass produced (scale, subject, angle, move, INT/EXT, location, time, characters, action, confidence). Every field is **click-to-edit**:

1. Click a cell (say, the `location` of a shot that reads `WOODS`).
2. Type the correct value (`WOODED ROAD`) and commit.
3. Scripty POSTs a correction (`model_value` = old, `human_value` = new) and applies it to the live row — the cell picks up an amber "learned" outline and the rendered script updates immediately.

Clicking a shot row seeks the video player to that shot, so you can verify against the footage.

Corrections are stored in the global database and drive everything downstream. When you've made a batch, distill them into reusable rules:

```bash
scripty lessons distill --project 1
scripty lessons list --project 1
```

(You can also hit **Distill** in the dashboard's Lessons tab.)

---

## The recursive learning loop

The core promise: **the next pass honors your corrections.**

```bash
# Pass 1: Scripty guesses. You correct "WOODS" -> "WOODED ROAD" in the dashboard.
scripty lessons distill --project 1     # condense corrections into a rule
scripty pass 1                          # Pass 2: recall injects the rule; the label is now correct
```

Before analyzing each shot, Scripty recalls your prior corrections and lessons and injects them into the vision prompt under **"SUPERVISOR NOTES (learned from prior passes — apply these)"**, where they outrank the model's instinct. After the pass, the **Metrics** tab plots the **agreement rate** — the fraction of your prior corrections the new pass now agrees with. Watch it climb from one pass to the next.

Lessons are scoped `project` by default, but global lessons (and globally-scoped corrections) carry across films — so a labeling convention you teach on one project improves the next.

See the worked WOODED ROAD example in the [README](../README.md#the-recursive-learning-loop) for the full narrative.

---

## Ground-truth training: truth + align

If you have the real screenplay for a film, you can train Scripty against it instead of correcting by hand.

### 1. Link the known script

```bash
scripty truth 1 ~/scripts/wooded-road.fountain
# -> linked truth 3
```

Accepts `.fountain` or plain `.txt`. It's parsed and stored as the project's ground truth, and registered as the `known_script` artifact.

### 2. Align a pass against it

```bash
scripty align 2
```

`align` runs a Needleman–Wunsch alignment of Scripty's scenes against the truth's, plus a fuzzy dialogue match, and prints a summary:

```
scene pairs: 8  slugline accuracy: 0.625
dialogue matched: 14/20  character accuracy: 0.71
auto-corrections emitted: 5
```

By default it **emits corrections** from every scene-heading disagreement (marked `source='ground_truth'`). Suppress that to align read-only:

```bash
scripty align 2 --no-emit-corrections
```

### 3. Learn from the diffs

The auto-emitted corrections feed the learning loop exactly like human edits:

```bash
scripty lessons distill --project 1
scripty pass 1                          # the next pass moves toward the real script
```

The dashboard's **Truth** tab shows the alignment as a diff table (green = matched, amber = paired with differences, red = unmatched), and the **Metrics** tab tracks `slugline_accuracy` alongside the agreement rate.

---

## Reading the describe prompts

Every pass emits a parallel **describe track** — generative-video prompts, one per shot plus an umbrella prompt per scene, threaded with a continuity block (characters, locations, style) so a gen-AI video model keeps things consistent.

Print the sheet for a pass:

```bash
scripty describe export 2
scripty describe export 2 --target sora        # filter to a specific gen target
scripty describe export 2 --revision 2         # a specific revision (see compare loop)
```

In the dashboard, the **Describe** tab lists the prompts; each is editable, and edits are recorded as corrections on the `prompt` field — so the describe track learns too.

---

## The compare / re-prompt loop

Once you've generated a video from the describe prompts (in your gen-AI tool of choice), feed it back in to close the loop:

```bash
scripty compare 1 2 ~/renders/wooded-road-gen.mp4
```

`compare` (`run_comparison`):

1. Registers the `generated_video` artifact.
2. Measures **SSIM** and **PSNR** between the generated video and the original.
3. Extracts keyframes from both and critiques them shot-by-shot (via the vision provider).
4. **Revises the describe prompts** — for each critiqued shot it creates a **revision-2** prompt appending "REVISION NOTES (from comparison with generated output): …".
5. Writes a `comparison_report` artifact and prints the result JSON (`metrics`, `critiques`, `revised_prompts`).

Then export the improved prompts and generate again:

```bash
scripty describe export 2 --revision 2
```

That's the recursive re-prompt loop: generate → compare → revise → regenerate.

---

## The Compare Bay (four-panel view)

The dashboard (and the desktop app) has a second view, toggled from the header: **Supervisor** ↔ **Compare Bay**. The Compare Bay is a four-panel side-by-side:

| Panel | Shows |
| --- | --- |
| **FILM** | The video player plus a horizontal shot strip; the active shot highlights as it plays, and clicking a thumb seeks. |
| **KNOWN SCRIPT** | The linked ground-truth script, its scene headings colored by alignment verdict (green/amber/red). If none is linked, a dashed dropzone invites you to drop one. |
| **SCRIPTY'S SCRIPT** | Scripty's generated screenplay; clicking a shot in the FILM strip scrolls to and flashes the matching scene. |
| **DESCRIBE** | The describe-prompt cards; the active shot's card highlights and scrolls into view as the video plays, and each card is editable. |

Both views support **whole-window drag-and-drop**: drop a video to create a project, drop a `.fountain`/`.txt` to link ground truth. In the desktop app, dropped files are resolved to real paths and sent to the from-path endpoints; in the browser they're uploaded. After a video drop you get a **RUN PASS ▶** button that kicks off a background pass and streams progress.

---

## Where artifacts land on disk

Everything lives under `$SCRIPTY_HOME` (default `~/.scripty`), with one global database and a per-project directory:

```
$SCRIPTY_HOME/
├── scripty.db                        # one global SQLite database (projects, passes, corrections, lessons, …)
├── demo.mp4                          # created by `scripty demo`
├── uploads/                          # files dropped/uploaded through the dashboard
└── projects/<slug>/
    ├── keyframes/passN/              # extracted keyframes, shot{idx}_f{n}.jpg
    ├── audio/passN.wav               # extracted audio track
    ├── artifacts/
    │   ├── passN.fountain            # generated screenplay (Fountain)
    │   ├── passN.html                # rendered screenplay (HTML)
    │   └── passN_prompts.txt         # describe-prompt sheet
    └── compare/                      # SSIM/PSNR working files + comparison report JSON
```

The `artifacts` table indexes these into the five-artifact bundle (source video, known script, generated script, describe set, generated video + comparison report), which the dashboard's **Bundle** tab surfaces per project. Point `SCRIPTY_HOME` elsewhere to keep separate workspaces:

```bash
SCRIPTY_HOME=~/scripty-projectX scripty serve
```
