#!/usr/bin/env bash
# Run every exported pre-generation input through Claude Code (headless) and
# save the JSON it returns. Uses the Claude plan of the account logged in to
# `claude`, not API credit. Safe to stop and re-run: finished items are skipped.
#
#   python scripts/pregenerate.py export documents
#   bash pregen/run_with_claude_code.sh            # all inputs
#   bash pregen/run_with_claude_code.sh 3          # just the first 3 (trial)
#   python scripts/pregenerate.py import
#
# Settings (environment variables):
#   CITED_CC_MODEL   model alias for claude --model (default: opus)
#   CITED_CC_PAUSE   seconds to wait between items (default: 2)
set -uo pipefail

cd "$(dirname "$0")/.."
IN_DIR="data/pregen_inputs"
OUT_DIR="data/pregen_outputs"
MODEL="${CITED_CC_MODEL:-opus}"
PAUSE="${CITED_CC_PAUSE:-2}"
LIMIT="${1:-0}"

command -v claude >/dev/null || { echo "Claude Code ('claude') is not on PATH."; exit 1; }
[ -f "$IN_DIR/manifest.json" ] || { echo "Run: python scripts/pregenerate.py export documents"; exit 1; }
mkdir -p "$OUT_DIR"

INSTRUCTION='The input contains instructions followed by a SOURCE. Follow the instructions exactly and reply with only the JSON object they describe: no prose, no code fences. Do not use any tools.'

count=0
for input in "$IN_DIR"/*.md; do
  name="$(basename "$input" .md)"
  out="$OUT_DIR/$name.json"
  if [ -s "$out" ] && python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$out" 2>/dev/null; then
    continue
  fi
  if [ "$LIMIT" -gt 0 ] && [ "$count" -ge "$LIMIT" ]; then
    break
  fi
  echo "[$(date +%H:%M:%S)] $name"
  if claude -p "$INSTRUCTION" --model "$MODEL" --output-format text < "$input" > "$out.tmp"; then
    # Keep only the JSON object, in case the reply has extra text around it.
    if python3 - "$out.tmp" "$out" <<'PY'
import json, re, sys
text = open(sys.argv[1]).read()
m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
text = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
json.loads(text)
open(sys.argv[2], "w").write(text)
PY
    then
      rm -f "$out.tmp"
    else
      echo "  reply was not valid JSON; kept in $out.tmp, will retry next run"
    fi
  else
    echo "  claude exited with an error (usage limit?). Re-run later to continue."
    rm -f "$out.tmp"
    break
  fi
  count=$((count + 1))
  sleep "$PAUSE"
done
done_n=$(ls "$OUT_DIR"/*.json 2>/dev/null | wc -l)
total=$(ls "$IN_DIR"/*.md | wc -l)
echo "Done: $done_n of $total items have output. Next: python scripts/pregenerate.py import"
