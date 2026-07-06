#!/usr/bin/env bash
# One-command self-contained build for Scripty.
#
# Produces a macOS .app + .dmg that run on a fresh Mac with NO Python, venv,
# Homebrew, or ffmpeg installed:
#   1. Freeze the backend with PyInstaller       -> packaging/dist/scripty-backend/
#   2. Bundle a relocatable ffmpeg/ffprobe        -> packaging/ffmpeg/
#   3. electron-builder packages both as extraResources; the afterPack hook
#      (desktop/build/afterpack-sign.js) ad-hoc re-signs every bundled Mach-O.
#
# electron-builder's dmg target emits both the .app (dist/mac-arm64/) and the
# .dmg (dist/). Ad-hoc signed (identity: null); no notarization.
set -euo pipefail

# Resolve repo root from this script's location so it works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

echo "==> [1/3] Freezing backend (PyInstaller onedir)"
bash "$ROOT/packaging/build_backend.sh"

echo "==> [2/3] Bundling relocatable ffmpeg/ffprobe"
bash "$ROOT/packaging/bundle_ffmpeg.sh"

# Sanity: extraResources sources must exist or electron-builder will error out.
if [ ! -x "$ROOT/packaging/dist/scripty-backend/scripty-backend" ]; then
  echo "error: frozen backend missing at packaging/dist/scripty-backend/scripty-backend" >&2
  exit 1
fi
if [ ! -x "$ROOT/packaging/ffmpeg/bin/ffmpeg" ]; then
  echo "error: bundled ffmpeg missing at packaging/ffmpeg/bin/ffmpeg" >&2
  exit 1
fi

echo "==> [3/3] Packaging Electron app (electron-builder --mac dmg)"
(
  cd "$ROOT/desktop"
  npm install >/dev/null 2>&1 || true
  npm run dist:standalone
)

echo
echo "==> Build complete. Artifacts under desktop/dist:"
DIST="$ROOT/desktop/dist"
# Report the produced .app(s) and .dmg(s) with sizes.
if [ -d "$DIST" ]; then
  find "$DIST" -maxdepth 2 -name '*.app' -print 2>/dev/null | while read -r appPath; do
    size=$(du -sh "$appPath" 2>/dev/null | cut -f1)
    echo "  APP: $appPath  ($size)"
  done
  find "$DIST" -maxdepth 1 -name '*.dmg' -print 2>/dev/null | while read -r dmgPath; do
    size=$(du -sh "$dmgPath" 2>/dev/null | cut -f1)
    echo "  DMG: $dmgPath  ($size)"
  done
else
  echo "  (warning: $DIST not found — check electron-builder output above)" >&2
fi
