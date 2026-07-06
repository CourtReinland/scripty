#!/usr/bin/env bash
# Freeze the Scripty backend into a self-contained onedir bundle with PyInstaller.
# Output: packaging/dist/scripty-backend/scripty-backend  (+ _internal/ deps).
# Runs on Apple Silicon: every Mach-O it emits is ad-hoc codesigned so Gatekeeper
# does not kill modified/unsigned binaries.
set -euo pipefail

# Resolve repo root from this script's location so it works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
PIP="$ROOT/.venv/bin/pip"

if [ ! -x "$PY" ]; then
  echo "error: $PY not found (project venv missing)" >&2
  exit 1
fi

# Ensure pyinstaller is available in the project venv (do NOT touch pyproject deps).
if ! "$PY" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "==> installing pyinstaller into .venv"
  "$PIP" install --quiet pyinstaller
fi

echo "==> running PyInstaller (onedir)"
"$PY" -m PyInstaller --noconfirm --clean --onedir --name scripty-backend \
  --collect-submodules scripty \
  --collect-all uvicorn --collect-all anthropic --collect-all pydantic \
  --collect-all pydantic_core --collect-all fastapi --collect-all starlette \
  --collect-all rapidfuzz --collect-all typer --collect-all click \
  --collect-all jinja2 --collect-all anyio --collect-all sniffio --collect-all h11 \
  --collect-all httpx --collect-all httpcore --collect-all certifi \
  --collect-all websockets --collect-all watchfiles \
  --collect-all python_multipart --collect-all multipart \
  --add-data "$ROOT/src/scripty/server/static:scripty/server/static" \
  --distpath packaging/dist --workpath packaging/build --specpath packaging \
  packaging/backend_entry.py

DIST="$ROOT/packaging/dist/scripty-backend"
if [ ! -x "$DIST/scripty-backend" ]; then
  echo "error: expected frozen binary at $DIST/scripty-backend" >&2
  exit 1
fi

echo "==> ad-hoc codesigning every Mach-O in the bundle"
# Sign dylibs/.so first (bottom-up), then the main executable.
find "$DIST" \( -name '*.so' -o -name '*.dylib' \) -print0 |
  while IFS= read -r -d '' f; do
    codesign --force -s - --timestamp=none "$f" >/dev/null 2>&1 || true
  done
# Sign any nested Mach-O executables (e.g. Python.framework helpers) then the entry exe.
find "$DIST" -type f -perm -u+x -print0 |
  while IFS= read -r -d '' f; do
    if file "$f" | grep -q 'Mach-O'; then
      codesign --force -s - --timestamp=none "$f" >/dev/null 2>&1 || true
    fi
  done
codesign --force -s - --timestamp=none "$DIST/scripty-backend" >/dev/null 2>&1 || true

echo "==> frozen backend ready: $DIST/scripty-backend"
