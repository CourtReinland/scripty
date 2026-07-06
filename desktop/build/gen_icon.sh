#!/bin/bash
# Generate build/icon.png (1024x1024) for the Scripty desktop app:
# dark slate (#141414) rounded square, diagonal white clapper stripes across
# the top, and a large monospace "S". Pure ffmpeg lavfi — no other tooling.
#
# Prefers drawtext (Menlo / SFNSMono); if this ffmpeg build lacks libfreetype
# (homebrew's does), falls back to a blocky segment-built "S" via drawbox so
# the icon still generates deterministically.
set -euo pipefail
cd "$(dirname "$0")"

FFMPEG="${FFMPEG:-ffmpeg}"

# Top ~204px: diagonal clapper stripes (white 232 / slate 20); below: slate.
STRIPE="if(lt(Y,204),if(lt(mod(X+Y,150),75),232,20),20)"
# Rounded-rect alpha mask, corner radius 160.
ALPHA="if(lte(hypot(X-clip(X,160,W-161),Y-clip(Y,160,H-161)),160),255,0)"
BASE="geq=r='${STRIPE}':g='${STRIPE}':b='${STRIPE}':a='${ALPHA}',drawbox=x=0:y=204:w=1024:h=10:color=0x0b0b0b:t=fill"

INK="0xf2f2ee"
if "$FFMPEG" -hide_banner -filters 2>/dev/null | grep -q " drawtext "; then
  FONT="/System/Library/Fonts/Menlo.ttc"
  [ -f "$FONT" ] || FONT="/System/Library/Fonts/SFNSMono.ttf"
  if [ ! -f "$FONT" ]; then
    echo "gen_icon.sh: no monospace system font found (Menlo/SFNSMono)" >&2
    exit 1
  fi
  GLYPH="drawtext=fontfile='${FONT}':text='S':fontcolor=${INK}:fontsize=560:x=(w-text_w)/2:y=204+((h-204-text_h)/2)"
else
  # Blocky monospace "S" from five drawbox segments (stroke 96), centered in
  # the region below the clapper stripes.
  GLYPH="drawbox=x=292:y=334:w=440:h=96:color=${INK}:t=fill"
  GLYPH="${GLYPH},drawbox=x=292:y=334:w=96:h=280:color=${INK}:t=fill"
  GLYPH="${GLYPH},drawbox=x=292:y=566:w=440:h=96:color=${INK}:t=fill"
  GLYPH="${GLYPH},drawbox=x=636:y=614:w=96:h=280:color=${INK}:t=fill"
  GLYPH="${GLYPH},drawbox=x=292:y=798:w=440:h=96:color=${INK}:t=fill"
fi

"$FFMPEG" -hide_banner -loglevel error -y \
  -f lavfi -i "color=c=0x141414:s=1024x1024:d=1,format=rgba" \
  -vf "${BASE},${GLYPH}" \
  -frames:v 1 icon.png

echo "wrote $(pwd)/icon.png"
