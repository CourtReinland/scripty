#!/usr/bin/env bash
# Produce a RELOCATABLE ffmpeg+ffprobe that runs with no Homebrew present.
# Output tree: packaging/ffmpeg/{bin,libs}
# Agent B (SPEC3 §"AGENT B"). Ad-hoc signs every Mach-O it emits.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$REPO_ROOT/packaging/ffmpeg"
BIN="$OUT/bin"
LIBS="$OUT/libs"

# 1. Ensure dylibbundler is present (build-time tool).
if ! command -v dylibbundler >/dev/null 2>&1; then
  echo "[bundle_ffmpeg] installing dylibbundler via brew..."
  brew install dylibbundler
fi

# 2. Resolve the real Homebrew binaries.
FF="$(readlink -f "$(command -v ffmpeg)")"
FP="$(readlink -f "$(command -v ffprobe)")"
echo "[bundle_ffmpeg] ffmpeg  -> $FF"
echo "[bundle_ffmpeg] ffprobe -> $FP"

# 3. Clean + create layout.
rm -rf "$OUT"
mkdir -p "$BIN" "$LIBS"
cp "$FF" "$BIN/ffmpeg"
cp "$FP" "$BIN/ffprobe"
chmod u+w "$BIN/ffmpeg" "$BIN/ffprobe"

# 4. Relocate each binary's dependencies into the SHARED libs dir.
#    -cd create dir, -b bundle, -x fix-file, -d dest, -p install-name prefix, -of overwrite.
dylibbundler -cd -b -x "$BIN/ffmpeg"  -d "$LIBS" -p @executable_path/../libs -of
dylibbundler -cd -b -x "$BIN/ffprobe" -d "$LIBS" -p @executable_path/../libs -of

# 5. Ad-hoc codesign every dylib + both binaries (Apple Silicon kills modified unsigned Mach-O).
echo "[bundle_ffmpeg] ad-hoc signing libs..."
find "$LIBS" -type f -name '*.dylib' -print0 | while IFS= read -r -d '' f; do
  codesign --force -s - "$f"
done
codesign --force -s - "$BIN/ffmpeg"
codesign --force -s - "$BIN/ffprobe"

echo "[bundle_ffmpeg] done. Tree at $OUT"
ls -R "$OUT" | head -60
