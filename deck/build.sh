#!/usr/bin/env bash
# Build the submission deck from the pipeline outputs, then (optionally) render it with PowerPoint for QA.
#   bash deck/build.sh            # facts.json + .pptx
#   bash deck/build.sh --render   # also export a PDF via Microsoft PowerPoint and PNGs into deck/render/
# PPTX_SKILL (optional) = folder holding scripts/apply_theme.js, to write the brand colours into the theme.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -W ignore scripts/11_deck_facts.py
NODE_PATH="$PWD/deck/node_modules" node deck/build_deck.js | tee /tmp/deck_build_$$.log
OUT=$(sed -n 's/^wrote //p' /tmp/deck_build_$$.log | tail -1); rm -f /tmp/deck_build_$$.log

if [[ "${1:-}" == "--render" ]]; then
  # PowerPoint is sandboxed: it may write only where it was granted access (the project folder), and
  # "save as PDF" returns before the file is written, so wait for the file before closing.
  PDF="$PWD/deck/_render.pdf"
  mkdir -p deck/render && rm -f deck/render/*.png "$PDF"
  # open via LaunchServices: it hands the sandboxed app an access grant for this exact file
  open -a "Microsoft PowerPoint" "$OUT"
  osascript <<EOF
set outPdf to "$PDF"
with timeout of 240 seconds
  tell application "Microsoft PowerPoint"
    repeat 60 times
      if (count of presentations) > 0 then exit repeat
      delay 1
    end repeat
    delay 3
    save presentation 1 in POSIX file outPdf as save as PDF
  end tell
  repeat 120 times
    try
      do shell script "test -s " & quoted form of outPdf
      exit repeat
    end try
    delay 1
  end repeat
  delay 2
  tell application "Microsoft PowerPoint" to close presentation 1 saving no
end timeout
EOF
  mv "$PDF" deck/render/deck.pdf
  python3 - deck/render/deck.pdf <<'PY'
import sys, pymupdf
d = pymupdf.open(sys.argv[1])
for i, p in enumerate(d):
    p.get_pixmap(dpi=110).save(f"deck/render/slide-{i + 1:02d}.png")
print(f"rendered {len(d)} slides to deck/render/")
PY
fi
