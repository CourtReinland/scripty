# Building the macOS desktop app

Scripty's desktop shell is an Electron app (`desktop/`) that spawns the Python backend (`scripty serve`) on a free localhost port, waits for it to answer, and opens the dashboard — including the four-panel **Compare Bay** — in a native window. This guide covers building and packaging it as an unsigned macOS `.app`.

## Table of contents

- [Prerequisites](#prerequisites)
- [Install](#install)
- [The icon step](#the-icon-step)
- [Smoke test](#smoke-test)
- [Package the app](#package-the-app)
- [Build a DMG](#build-a-dmg)
- [How main.js finds and runs the backend](#how-mainjs-finds-and-runs-the-backend)
- [Where the data lives](#where-the-data-lives)
- [Gatekeeper (unsigned app)](#gatekeeper-unsigned-app)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

- **Node.js** (with `npm`) — the app is built with Electron 43 + electron-builder 26. Apple-silicon (arm64) macOS is the target.
- **ffmpeg / ffprobe** on your `PATH` — the backend needs them at runtime (`brew install ffmpeg`).
- **The Python backend installed in a venv.** The desktop app runs the `scripty` console script, so you need a working install first:

  ```bash
  cd /Users/blue/Projects/scripty
  python3.11 -m venv .venv
  source .venv/bin/activate
  pip install -e ".[dev]"
  ```

  Confirm the CLI exists at `.venv/bin/scripty` — that's the default path the app looks for.

---

## Install

```bash
cd desktop
npm install
```

This downloads Electron and electron-builder into `desktop/node_modules/` (git-ignored). The scripts available (from `desktop/package.json`) are:

| Script | Command | What it does |
| --- | --- | --- |
| `start` | `electron .` | Launch the headed app against your local backend. |
| `smoke` | `electron . --smoke` | Headless boot check — spawn the backend, health-poll, print `SMOKE OK`, exit 0. |
| `pack` | `electron-builder --mac dir` | Build an unsigned `.app` bundle. |
| `dist` | `electron-builder --mac dmg` | Build a `.dmg` installer. |

---

## The icon step

The app icon is generated with a pure-ffmpeg one-liner — a dark slate (`#141414`) rounded square with diagonal white clapper stripes across the top and a large monospace **S**. Run it once during setup:

```bash
cd desktop
bash build/gen_icon.sh      # writes desktop/build/icon.png (1024x1024)
```

electron-builder picks up `build/icon.png` automatically (via `directories.buildResources: "build"`). If your ffmpeg build lacks `libfreetype` (Homebrew's often does), `gen_icon.sh` falls back to drawing a blocky **S** from `drawbox` segments, so it still produces a deterministic icon.

---

## Smoke test

The fastest way to verify the whole stack wires together:

```bash
cd desktop
npm run smoke
```

`--smoke` mode: no window is created. The app finds a free port, spawns `scripty serve --host 127.0.0.1 --port <port>`, polls `http://127.0.0.1:<port>/api/projects` until it returns 200 (10-second timeout), prints `SMOKE OK`, tears the backend down, and exits 0. Any failure prints `SMOKE FAIL: …` and exits 1. This is the check to run in CI or after changing `main.js`.

---

## Package the app

```bash
cd desktop
npm run pack
```

This produces an **unsigned** `.app` bundle. On Apple silicon it lands at:

```
desktop/dist/mac-arm64/Scripty.app
```

(electron-builder may write to `dist/mac-arm64` or `dist/mac` depending on the host arch — check both.) The build config in `package.json` sets `identity: null` (no code signing), `category: public.app-category.video`, and bundles only `main.js` and `preload.js` — the backend is *not* embedded; the app shells out to your installed `scripty` (see below).

---

## Build a DMG

```bash
cd desktop
npm run dist       # -> desktop/dist/Scripty-<version>.dmg
```

Same bundle as `npm run pack` above (a thin shell that runs the backend from your local `.venv`), wrapped in a `.dmg`. Good for your own machine; **not** portable to a Mac that lacks the venv or ffmpeg. For that, use the self-contained build below.

---

## Self-contained build (portable installer)

`packaging/build_standalone.sh` produces a `.dmg` that runs on a **bare Mac** — no Python, no venv, no Homebrew, no ffmpeg required on the target machine. One command:

```bash
bash packaging/build_standalone.sh
# -> desktop/dist/mac-arm64/Scripty.app   (~370 MB, self-contained)
# -> desktop/dist/Scripty-<version>-arm64.dmg   (~160 MB)
```

It runs three stages:

1. **`packaging/build_backend.sh`** — freezes the Python backend with **PyInstaller** (onedir) into `packaging/dist/scripty-backend/`. The whole `scripty` package is force-collected (`--collect-submodules scripty`) because the code uses lazy imports that PyInstaller's static analysis would otherwise miss, and the dashboard's `static/` assets are bundled with `--add-data`. Every emitted Mach-O is ad-hoc signed (required on Apple Silicon).
2. **`packaging/bundle_ffmpeg.sh`** — copies your Homebrew `ffmpeg`/`ffprobe` and **relocates** their dylibs with `dylibbundler` into `packaging/ffmpeg/{bin,libs}`, rewriting load paths to `@executable_path/../libs` so there is no dependency on `/opt/homebrew`. Everything is ad-hoc signed. (Bundling your already-trusted Homebrew build avoids downloading an unknown static binary.)
3. **`electron-builder`** copies both trees into the app via `extraResources`, and an `afterPack` hook (`desktop/build/afterpack-sign.js`) re-signs them inside the `.app` (electron-builder does not sign extraResources itself).

When packaged, `main.js` runs `Contents/Resources/backend/scripty-backend/scripty-backend` and prepends `Contents/Resources/ffmpeg/bin` to the backend's `PATH`, so the bare-name `ffmpeg`/`ffprobe` calls resolve to the bundled binaries. **No Python source changes are needed** — the ffmpeg seam is purely `PATH`-based.

The build outputs under `packaging/` are git-ignored (regenerated each run); only the build scripts are committed.

---

## How main.js finds and runs the backend

A **dev** `.app` (from `npm run pack`) is a thin shell — it does not contain Python. A **self-contained** `.app` (from `packaging/build_standalone.sh`) bundles the frozen backend and ffmpeg inside `Contents/Resources/`. At launch, `main.js`:

1. **Resolves the backend binary**: `SCRIPTY_BACKEND_BIN` wins if set; otherwise a packaged app uses the bundled `Contents/Resources/backend/scripty-backend/scripty-backend`, and a dev/unpackaged run falls back to `/Users/blue/Projects/scripty/.venv/bin/scripty`. If the resolved file doesn't exist, it opens an error window explaining how to fix it.
2. **Finds a free port** by listening on `127.0.0.1:0`.
3. **Spawns** `scripty serve --host 127.0.0.1 --port <port>`, inheriting the environment and piping backend logs to the console (prefixed `[backend]`).
4. **Health-polls** `GET /api/projects` until it returns 200 (10-second timeout).
5. **Opens** a `1600×1000` `BrowserWindow` (min `1200×760`, `titleBarStyle: hiddenInset`, `#141414` background) sandboxed (`contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`) and loads the backend root.
6. On **quit**, sends the backend `SIGTERM`, then `SIGKILL` after 3 seconds.

If your backend lives somewhere other than the default venv path — e.g. you cloned the repo elsewhere — point the app at it:

```bash
SCRIPTY_BACKEND_BIN=/path/to/your/.venv/bin/scripty open -a Scripty
```

The native menu adds **Open Film…** (a file dialog that POSTs to `/api/projects/from-path`) and **Link Known Script…** (sends the chosen path to the renderer via IPC so it can attach it to the selected project).

---

## Where the data lives

The app selects its data directory the same way the CLI does, via `SCRIPTY_HOME`. When launched by the desktop shell, if `SCRIPTY_HOME` isn't already set, `main.js` defaults it to the real `~/.scripty` — so the desktop app and the CLI share one database and one set of project artifacts. Override it to sandbox the app's data:

```bash
SCRIPTY_HOME=~/scripty-scratch open -a Scripty
```

---

## Gatekeeper (unsigned app)

The app is **unsigned and un-notarized** (`identity: null`). macOS Gatekeeper will refuse to open it normally on first launch. Two ways past it:

- **Right-click → Open** in Finder, then confirm in the dialog. (macOS remembers the choice.)
- **Clear the quarantine bit** from the terminal:

  ```bash
  xattr -dr com.apple.quarantine desktop/dist/mac-arm64/Scripty.app
  ```

Running `codesign -dv` on the bundle is *not* expected to succeed — there's no signature by design.

---

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Error window: **SCRIPTY BACKEND NOT FOUND** | The `scripty` binary isn't at the expected path. Install it (`pip install -e .` in the venv) or set `SCRIPTY_BACKEND_BIN`. |
| Error window: **SCRIPTY BACKEND FAILED TO START** | The backend spawned but didn't answer. Check the terminal's `[backend]` log lines — usually ffmpeg missing from `PATH` or a bad `SCRIPTY_HOME`. |
| `npm run smoke` prints **SMOKE FAIL** | Same root causes as above; the message names the failure (missing binary, health timeout, backend exited during startup). |
| Blank or non-loading window | The renderer is pinned to the backend origin by a `will-navigate` guard; a blank window usually means the backend health-poll passed but the page failed to load — check `[backend]` logs. |
| `gen_icon.sh` exits with a font error | No monospace system font found; it should fall back to `drawbox`. Ensure you're running the current `build/gen_icon.sh`. |
