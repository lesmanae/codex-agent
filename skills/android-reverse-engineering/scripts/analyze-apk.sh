#!/usr/bin/env bash
# analyze-apk.sh — end-to-end orchestrator. Runs decompile → inventory → security audit →
# secret extraction → API extraction → consolidate REPORT.md. No dynamic analysis.
#
# Usage: analyze-apk.sh <file.apk|xapk|aab> [--output DIR] [--engine jadx|vineflower|both]
#                      [--deobf] [--no-res] [--app-name NAME]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

INPUT=""
OUTPUT=""
ENGINE="jadx"
DEOBF=false
NO_RES=false
APP_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output)  OUTPUT="$2"; shift 2 ;;
    --engine)     ENGINE="$2"; shift 2 ;;
    --deobf)      DEOBF=true; shift ;;
    --no-res)     NO_RES=true; shift ;;
    --app-name)   APP_NAME="$2"; shift 2 ;;
    -h|--help)
      sed -n '/^# /,/^$/p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*)           echo "Unknown option: $1" >&2; exit 2 ;;
    *)            INPUT="$1"; shift ;;
  esac
done

[[ -z "$INPUT" ]] && { echo "Error: no input" >&2; exit 2; }
[[ -f "$INPUT" ]] || { echo "Error: file not found: $INPUT" >&2; exit 1; }

INPUT_ABS=$(realpath "$INPUT")
BASE=$(basename "$INPUT" | sed 's/\.[^.]*$//')
[[ -z "$APP_NAME" ]] && APP_NAME="$BASE"
[[ -z "$OUTPUT"   ]] && OUTPUT="./analysis-$BASE"
mkdir -p "$OUTPUT"
OUTPUT=$(realpath "$OUTPUT")

log() { printf '[analyze] %s\n' "$*"; }

log "input:  $INPUT_ABS"
log "output: $OUTPUT"
log "engine: $ENGINE"

# --- Phase 1: check deps ---
log "phase 1/6: check-deps"
bash "$HERE/check-deps.sh" || {
  echo "Error: required dependencies missing. Run install-deps.sh first." >&2
  exit 1
}

# --- Phase 2: decompile ---
log "phase 2/6: decompile"
DC_ARGS=(--engine "$ENGINE" -o "$OUTPUT/decompiled")
[[ "$DEOBF"  == true ]] && DC_ARGS+=(--deobf)
[[ "$NO_RES" == true ]] && DC_ARGS+=(--no-res)
bash "$HERE/decompile.sh" "${DC_ARGS[@]}" "$INPUT_ABS"

# Pick the sources dir that downstream scripts will consume.
SRC_DIR=""
for candidate in "$OUTPUT/decompiled/sources" \
                 "$OUTPUT/decompiled/jadx/sources" \
                 "$OUTPUT/decompiled/base/sources"; do
  [[ -d "$candidate" ]] && { SRC_DIR="$candidate"; break; }
done
if [[ -z "$SRC_DIR" ]]; then
  log "WARN: no sources directory found; downstream regex scans will be limited"
fi

# --- Phase 3: inventory ---
EXT_LOWER=$(echo "${INPUT_ABS##*.}" | tr '[:upper:]' '[:lower:]')
APK_FOR_INVENTORY="$INPUT_ABS"
if [[ "$EXT_LOWER" != "apk" ]]; then
  # Try to locate an APK artefact produced by decompile.sh (for xapk/aab)
  for candidate in "$OUTPUT/decompiled/.xapk-extracted/"*.apk \
                   "$OUTPUT/decompiled/.aab-extracted/universal.apk"; do
    [[ -f "$candidate" ]] && { APK_FOR_INVENTORY="$candidate"; break; }
  done
fi
if [[ -f "$APK_FOR_INVENTORY" && "${APK_FOR_INVENTORY##*.}" == "apk" ]]; then
  log "phase 3/6: inventory (androguard)"
  if command -v androguard >/dev/null 2>&1; then
    python3 "$HERE/gen-api-spec.py" --mode inventory --apk "$APK_FOR_INVENTORY" --out "$OUTPUT" \
        || log "inventory step failed (non-fatal)"
  else
    log "androguard missing; skipping inventory"
  fi
else
  log "phase 3/6: skipped (no APK artefact for inventory)"
fi

# --- Phase 4: security audit ---
log "phase 4/6: security audit"
bash "$HERE/audit-security.sh" "$OUTPUT/decompiled" "$APK_FOR_INVENTORY" \
    > "$OUTPUT/security-report.md" || log "audit step had errors"

# --- Phase 5: secrets ---
log "phase 5/6: extract-secrets"
if [[ -f "$APK_FOR_INVENTORY" ]]; then
  bash "$HERE/extract-secrets.sh" "$APK_FOR_INVENTORY" "$SRC_DIR" \
      > "$OUTPUT/secrets-report.md" || log "secrets step had errors"
fi

# --- Phase 6: API extraction ---
log "phase 6/6: API extraction"
if [[ -n "$SRC_DIR" ]]; then
  bash "$HERE/find-api-calls.sh" "$SRC_DIR" > "$OUTPUT/api-matches.txt" || true
  python3 "$HERE/gen-api-spec.py" --mode api --src "$SRC_DIR" --out "$OUTPUT" \
      --app-name "$APP_NAME" || log "API extraction had errors"
fi

# --- Consolidate REPORT.md ---
log "writing REPORT.md"
{
  printf '# Analysis Report — %s\n\n' "$APP_NAME"
  printf '_Generated: %s_\n\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  printf 'Input: `%s`\n\n' "$INPUT_ABS"
  printf 'Output dir: `%s`\n\n' "$OUTPUT"

  printf '## Inventory\n\n'
  if [[ -f "$OUTPUT/inventory.json" ]]; then
    printf '```json\n'
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); summary={k:d[k] for k in ["package","app_name","version_code","version_name","min_sdk","target_sdk","main_activity"] if k in d}; print(json.dumps(summary, indent=2))' "$OUTPUT/inventory.json"
    printf '\n```\n\n'
    printf 'Full inventory: [`inventory.json`](inventory.json)\n\n'
  else
    printf '_No inventory data._\n\n'
  fi

  if [[ -f "$OUTPUT/security-report.md" ]]; then
    printf '## Security\n\n_See [`security-report.md`](security-report.md)._ Summary:\n\n'
    # Pull just the "FAIL:" and "WARN:" lines for a quick summary.
    grep -E '^-( FAIL|- WARN| \*\*FAIL|- fail)' "$OUTPUT/security-report.md" 2>/dev/null || \
      grep -E '^- (FAIL|WARN):' "$OUTPUT/security-report.md" 2>/dev/null || true
    printf '\n'
  fi

  if [[ -f "$OUTPUT/secrets-report.md" ]]; then
    printf '## Secrets\n\n_See [`secrets-report.md`](secrets-report.md)._\n\n'
  fi

  if [[ -f "$OUTPUT/api-report.md" ]]; then
    printf '## API\n\n_See [`api-report.md`](api-report.md), [`api.openapi.yaml`](api.openapi.yaml), [`api.postman.json`](api.postman.json)._\n\n'
    head -30 "$OUTPUT/api-report.md" 2>/dev/null || true
    printf '\n'
  fi
} > "$OUTPUT/REPORT.md"

log "done. REPORT: $OUTPUT/REPORT.md"
