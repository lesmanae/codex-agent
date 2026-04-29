#!/usr/bin/env bash
# run-mobsf.sh — upload an APK to a local MobSF container, kick off a static scan,
# pull the JSON report, and convert the critical-findings section to markdown.
#
# Requires docker. The container is started on-demand (or reused if already running)
# on port 8000 with a random-ish API key.
#
# Usage: run-mobsf.sh <apk-file> [--output DIR] [--pdf]
set -euo pipefail

APK="${1:-}"
OUTPUT="./mobsf-report"
EMIT_PDF=false

shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) OUTPUT="$2"; shift 2 ;;
    --pdf)       EMIT_PDF=true; shift ;;
    -h|--help)
      echo "Usage: run-mobsf.sh <apk-file> [--output DIR] [--pdf]"; exit 0 ;;
    *)           echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ -z "$APK"  ]] && { echo "Usage: $0 <apk-file>" >&2; exit 2; }
[[ -f "$APK"  ]] || { echo "Error: APK not found: $APK" >&2; exit 1; }

command -v docker >/dev/null || { echo "Error: docker is required" >&2; exit 1; }
command -v curl   >/dev/null || { echo "Error: curl is required"   >&2; exit 1; }
command -v jq     >/dev/null || { echo "Error: jq is required"     >&2; exit 1; }

mkdir -p "$OUTPUT"
OUTPUT=$(realpath "$OUTPUT")
APK_ABS=$(realpath "$APK")

MOBSF_PORT="${MOBSF_PORT:-8000}"
MOBSF_API_KEY="${MOBSF_API_KEY:-$(head -c 32 /dev/urandom | xxd -p -c 32)}"
MOBSF_IMAGE="${MOBSF_IMAGE:-opensecurity/mobile-security-framework-mobsf:latest}"
CONTAINER="${MOBSF_CONTAINER:-mobsf}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo ">>> starting MobSF container ($CONTAINER, port $MOBSF_PORT)"
  docker run -d --rm \
      --name "$CONTAINER" \
      -p "$MOBSF_PORT:8000" \
      -e MOBSF_API_KEY="$MOBSF_API_KEY" \
      "$MOBSF_IMAGE" >/dev/null

  echo ">>> waiting for MobSF to become ready..."
  for _ in $(seq 1 90); do
    if curl -fsS "http://localhost:${MOBSF_PORT}/api_docs" >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
fi

API_URL="http://localhost:${MOBSF_PORT}"

echo ">>> uploading $APK_ABS"
UPLOAD_JSON=$(curl -fsS -X POST -H "Authorization: $MOBSF_API_KEY" \
    -F "file=@$APK_ABS" "$API_URL/api/v1/upload")
SCAN_HASH=$(echo "$UPLOAD_JSON" | jq -r '.hash')
[[ -z "$SCAN_HASH" || "$SCAN_HASH" == "null" ]] && { echo "Upload failed: $UPLOAD_JSON" >&2; exit 1; }

echo ">>> scan hash: $SCAN_HASH"
echo ">>> running static analysis (may take a minute)"
curl -fsS -X POST -H "Authorization: $MOBSF_API_KEY" \
    --data "hash=$SCAN_HASH&scan_type=apk&file_name=$(basename "$APK_ABS")" \
    "$API_URL/api/v1/scan" > "$OUTPUT/mobsf-full.json"

echo ">>> writing summary"
jq -r '
  "## MobSF Summary\n",
  "Package: " + (.package_name // "?"),
  "Main activity: " + (.main_activity // "?"),
  "Security score: " + ((.appsec.security_score // "?") | tostring),
  "",
  "### High-severity findings",
  (.appsec.high // [] | map("- " + (.title // "?")) | join("\n")),
  "",
  "### Warnings",
  (.appsec.warning // [] | map("- " + (.title // "?")) | join("\n"))
' "$OUTPUT/mobsf-full.json" > "$OUTPUT/mobsf-summary.md"

if $EMIT_PDF; then
  echo ">>> exporting PDF report"
  curl -fsS -X POST -H "Authorization: $MOBSF_API_KEY" \
      --data "hash=$SCAN_HASH" \
      "$API_URL/api/v1/download_pdf" -o "$OUTPUT/mobsf-report.pdf"
fi

echo ">>> done → $OUTPUT"
