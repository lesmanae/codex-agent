#!/usr/bin/env bash
# extract-secrets.sh — run apkleaks and trufflehog, produce a merged markdown report.
#
# Usage: extract-secrets.sh <apk-file> [decompiled-src-dir]
set -euo pipefail

APK="${1:-}"
SRC="${2:-}"

[[ -z "$APK"  ]] && { echo "Usage: $0 <apk-file> [decompiled-src-dir]" >&2; exit 2; }
[[ -f "$APK"  ]] || { echo "Error: APK not found: $APK" >&2; exit 1; }

export PATH="$HOME/.local/bin:$PATH"

have() { command -v "$1" >/dev/null 2>&1; }

printf '# Secrets & Endpoint Report\n\n'
printf '_Generated: %s_\n\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
printf 'APK: `%s`\n' "$APK"
[[ -n "$SRC" ]] && printf 'Source: `%s`\n' "$SRC"

printf '\n## apkleaks\n\n'
if have apkleaks; then
  tmp=$(mktemp)
  if apkleaks -f "$APK" -o "$tmp" >/dev/null 2>&1; then
    if [[ -s "$tmp" ]]; then
      printf '```\n'
      cat "$tmp"
      printf '\n```\n'
    else
      printf '_No matches._\n'
    fi
  else
    printf '_apkleaks exited non-zero; output may be partial._\n'
    [[ -s "$tmp" ]] && { printf '```\n'; cat "$tmp"; printf '\n```\n'; }
  fi
  rm -f "$tmp"
else
  printf '_apkleaks not installed._\n'
fi

printf '\n## trufflehog (filesystem scan of decompiled source)\n\n'
if have trufflehog && [[ -n "$SRC" && -d "$SRC" ]]; then
  # --only-verified can miss a lot in decompiled code; keep both.
  out=$(trufflehog filesystem "$SRC" --no-update --json 2>/dev/null | head -200 || true)
  if [[ -n "$out" ]]; then
    # Show a simplified table: detector | raw | file
    printf '| Detector | Raw (truncated) | File |\n|---|---|---|\n'
    printf '%s\n' "$out" | python3 -c '
import json, sys
for line in sys.stdin:
    try:
        d = json.loads(line)
    except Exception:
        continue
    det = d.get("DetectorName") or d.get("SourceName") or ""
    raw = (d.get("Raw") or "")[:60].replace("|", "/")
    src = d.get("SourceMetadata", {}).get("Data", {}).get("Filesystem", {}).get("file", "")
    print(f"| {det} | `{raw}` | `{src}` |")
' 2>/dev/null || printf '```\n%s\n```\n' "$out"
  else
    printf '_No verified/unverified secrets detected._\n'
  fi
else
  printf '_trufflehog skipped (no source dir provided or trufflehog missing)._\n'
fi

printf '\n---\n_End of secrets report._\n'
