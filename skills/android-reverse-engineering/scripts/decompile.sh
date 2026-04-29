#!/usr/bin/env bash
# decompile.sh — decompile APK / XAPK / AAB / JAR / AAR using jadx, vineflower, or both,
# plus apktool for smali + original manifest.
#
# Usage: decompile.sh [--engine jadx|vineflower|both] [--deobf] [--no-res]
#                     [--apktool] [-o OUT_DIR] <file>
set -euo pipefail

usage() {
  sed -n '/^# /,/^$/p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

ENGINE="jadx"
DEOBF=false
NO_RES=false
RUN_APKTOOL=true
OUTPUT_DIR=""
INPUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --engine)       ENGINE="$2"; shift 2 ;;
    --deobf)        DEOBF=true; shift ;;
    --no-res)       NO_RES=true; shift ;;
    --apktool)      RUN_APKTOOL=true; shift ;;
    --no-apktool)   RUN_APKTOOL=false; shift ;;
    -o|--output)    OUTPUT_DIR="$2"; shift 2 ;;
    -h|--help)      usage ;;
    -*)             echo "Unknown option: $1" >&2; exit 2 ;;
    *)              INPUT="$1"; shift ;;
  esac
done

[[ -z "$INPUT" ]] && { echo "Error: no input file" >&2; usage; }
[[ -f "$INPUT" ]] || { echo "Error: file not found: $INPUT" >&2; exit 1; }

INPUT_ABS=$(realpath "$INPUT")
EXT_LOWER=$(echo "${INPUT##*.}" | tr '[:upper:]' '[:lower:]')
BASENAME=$(basename "$INPUT" ".${INPUT##*.}")

case "$EXT_LOWER" in
  apk|xapk|aab|jar|aar) ;;
  *) echo "Error: unsupported file type: .$EXT_LOWER" >&2; exit 1 ;;
esac

case "$ENGINE" in
  jadx|vineflower|fernflower|both) ;;
  *) echo "Error: unknown engine '$ENGINE' (use jadx|vineflower|both)" >&2; exit 1 ;;
esac

# Treat 'fernflower' as alias for 'vineflower' (backward compat with original skill).
[[ "$ENGINE" == "fernflower" ]] && ENGINE="vineflower"

[[ -z "$OUTPUT_DIR" ]] && OUTPUT_DIR="${BASENAME}-decompiled"
mkdir -p "$OUTPUT_DIR"
OUTPUT_ABS=$(realpath "$OUTPUT_DIR")

VINEFLOWER_JAR="${VINEFLOWER_JAR:-/opt/vineflower.jar}"

log() { printf '[decompile] %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Step 1 — Normalise input to an APK/JAR/AAR we can feed the engines.
# ---------------------------------------------------------------------------

WORK_INPUT="$INPUT_ABS"
WORK_INPUT_EXT="$EXT_LOWER"

if [[ "$EXT_LOWER" == "xapk" ]]; then
  log "XAPK detected — extracting"
  XAPK_DIR="$OUTPUT_ABS/.xapk-extracted"
  mkdir -p "$XAPK_DIR"
  unzip -q -o "$INPUT_ABS" -d "$XAPK_DIR"
  mapfile -t APKS < <(find "$XAPK_DIR" -maxdepth 2 -type f -name '*.apk' | sort)
  if (( ${#APKS[@]} == 0 )); then
    echo "Error: no APK inside XAPK" >&2; exit 1
  fi
  # Pick the base APK (largest non-"config" split, or the one named base.apk)
  BASE_APK=""
  for a in "${APKS[@]}"; do
    name=$(basename "$a")
    [[ "$name" == "base.apk" ]] && { BASE_APK="$a"; break; }
  done
  if [[ -z "$BASE_APK" ]]; then
    # Using `head -1 | sed` so paths containing spaces are preserved (awk $2
    # would truncate at the first space).
    BASE_APK=$(stat -c '%s %n' "${APKS[@]}" | sort -rn | head -1 | sed 's/^[0-9]* //')
  fi
  log "using base APK: $BASE_APK"
  WORK_INPUT="$BASE_APK"
  WORK_INPUT_EXT="apk"
fi

if [[ "$EXT_LOWER" == "aab" ]]; then
  log "AAB detected — converting to universal APK via bundletool"
  AAB_DIR="$OUTPUT_ABS/.aab-extracted"
  mkdir -p "$AAB_DIR"
  bundletool build-apks --bundle="$INPUT_ABS" --output="$AAB_DIR/app.apks" --mode=universal --overwrite
  unzip -q -o "$AAB_DIR/app.apks" -d "$AAB_DIR"
  UNI_APK="$AAB_DIR/universal.apk"
  if [[ ! -f "$UNI_APK" ]]; then
    UNI_APK=$(find "$AAB_DIR" -maxdepth 2 -type f -name '*.apk' | head -1)
  fi
  log "universal APK: $UNI_APK"
  WORK_INPUT="$UNI_APK"
  WORK_INPUT_EXT="apk"
fi

# ---------------------------------------------------------------------------
# Step 2 — jadx
# ---------------------------------------------------------------------------
run_jadx() {
  local target_dir="$1"
  local input_file="$2"
  local args=(-d "$target_dir")
  [[ "$DEOBF" == true ]] && args+=(--deobf)
  [[ "$NO_RES" == true ]] && args+=(--no-res)
  log "jadx $input_file -> $target_dir"
  if ! jadx "${args[@]}" "$input_file" 2> "$target_dir/.jadx.log"; then
    log "jadx exited non-zero (see $target_dir/.jadx.log) — continuing"
  fi
}

# ---------------------------------------------------------------------------
# Step 3 — vineflower (APK needs dex2jar first)
# ---------------------------------------------------------------------------
run_vineflower() {
  local target_dir="$1"
  local input_file="$2"
  local vf_input="$input_file"
  local tmp_jar=""

  if [[ "$WORK_INPUT_EXT" == "apk" ]]; then
    if ! command -v d2j-dex2jar >/dev/null 2>&1; then
      echo "Error: d2j-dex2jar not installed — vineflower cannot process APK" >&2
      return 1
    fi
    tmp_jar="$target_dir/.from-dex.jar"
    mkdir -p "$target_dir"
    log "dex2jar $input_file -> $tmp_jar"
    d2j-dex2jar -f -o "$tmp_jar" "$input_file" 2> "$target_dir/.dex2jar.log"
    vf_input="$tmp_jar"
  fi

  mkdir -p "$target_dir/sources"
  log "vineflower $vf_input -> $target_dir/sources"
  java -jar "$VINEFLOWER_JAR" "$vf_input" "$target_dir/sources" \
      > "$target_dir/.vineflower.log" 2>&1 || \
      log "vineflower exited non-zero (see $target_dir/.vineflower.log) — continuing"
}

# ---------------------------------------------------------------------------
# Step 4 — apktool (smali + original manifest + resources)
# ---------------------------------------------------------------------------
run_apktool() {
  if [[ "$WORK_INPUT_EXT" != "apk" ]]; then
    log "skipping apktool (input is $WORK_INPUT_EXT, not apk)"
    return 0
  fi
  local target_dir="$OUTPUT_ABS/smali"
  log "apktool d -> $target_dir"
  apktool d -f -o "$target_dir" "$WORK_INPUT" > "$OUTPUT_ABS/.apktool.log" 2>&1 || \
      log "apktool exited non-zero (see .apktool.log) — continuing"
}

# ---------------------------------------------------------------------------
# Dispatch engines
# ---------------------------------------------------------------------------
if [[ "$ENGINE" == "both" ]]; then
  run_jadx       "$OUTPUT_ABS/jadx"       "$WORK_INPUT"
  run_vineflower "$OUTPUT_ABS/vineflower" "$WORK_INPUT"
  # Provide a unified `sources/` link pointing at jadx for downstream scripts.
  [[ -d "$OUTPUT_ABS/jadx/sources" ]] && ln -sfn "jadx/sources" "$OUTPUT_ABS/sources"
  [[ -d "$OUTPUT_ABS/jadx/resources" ]] && ln -sfn "jadx/resources" "$OUTPUT_ABS/resources"
elif [[ "$ENGINE" == "jadx" ]]; then
  run_jadx "$OUTPUT_ABS" "$WORK_INPUT"
elif [[ "$ENGINE" == "vineflower" ]]; then
  run_vineflower "$OUTPUT_ABS" "$WORK_INPUT"
fi

[[ "$RUN_APKTOOL" == true ]] && run_apktool

# ---------------------------------------------------------------------------
# Split-APK wrapper detection: if jadx produced very few Java files and the
# APK contains base.apk / split_config.*, decompile base.apk separately.
# ---------------------------------------------------------------------------
if [[ "$ENGINE" != "vineflower" && "$WORK_INPUT_EXT" == "apk" ]]; then
  SOURCES_DIR="$OUTPUT_ABS/sources"
  [[ "$ENGINE" == "both" ]] && SOURCES_DIR="$OUTPUT_ABS/jadx/sources"

  if [[ -d "$SOURCES_DIR" ]]; then
    JAVA_COUNT=$(find "$SOURCES_DIR" -name '*.java' 2>/dev/null | wc -l)
    if (( JAVA_COUNT <= 15 )); then
      INNER_BASE=$(unzip -l "$WORK_INPUT" 2>/dev/null | awk '/base\.apk$/ {print $NF}' | head -1)
      if [[ -n "$INNER_BASE" ]]; then
        log "split-APK wrapper detected (only $JAVA_COUNT .java files); re-decompiling $INNER_BASE"
        WRAP_DIR="$OUTPUT_ABS/.wrapper-extracted"
        mkdir -p "$WRAP_DIR"
        unzip -q -o "$WORK_INPUT" "$INNER_BASE" -d "$WRAP_DIR"
        BASE_TARGET="$OUTPUT_ABS/base"
        mkdir -p "$BASE_TARGET"
        run_jadx "$BASE_TARGET" "$WRAP_DIR/$INNER_BASE"
      fi
    fi
  fi
fi

log "decompile complete → $OUTPUT_ABS"
