#!/usr/bin/env bash
# check-deps.sh — verify the Android RE toolchain is installed.
# Exits 0 if all REQUIRED tools are present, 1 otherwise.
set -u

# Required tools — the skill cannot function without these.
REQUIRED=(java jadx apktool d2j-dex2jar bundletool)

# Optional tools — recommended, used by specific phases.
OPTIONAL=(androguard apkleaks trufflehog frida objection mitmproxy)

# JAR files that must exist on disk.
REQUIRED_JARS=(
  "/opt/vineflower.jar"
  "/usr/local/bin/apktool.jar"
  "/usr/local/bin/bundletool.jar"
)

status=0
missing_required=()
missing_optional=()

printf "=== check-deps ===\n"

for cmd in "${REQUIRED[@]}"; do
  if command -v "$cmd" >/dev/null 2>&1; then
    printf "  [OK]       %-15s -> %s\n" "$cmd" "$(command -v "$cmd")"
  else
    printf "  [MISSING]  %-15s (required)\n" "$cmd"
    missing_required+=("$cmd")
    status=1
  fi
done

for jar in "${REQUIRED_JARS[@]}"; do
  if [[ -f "$jar" ]]; then
    printf "  [OK]       %-15s -> %s\n" "$(basename "$jar")" "$jar"
  else
    printf "  [MISSING]  %-15s (required)\n" "$(basename "$jar")"
    missing_required+=("$jar")
    status=1
  fi
done

for cmd in "${OPTIONAL[@]}"; do
  if command -v "$cmd" >/dev/null 2>&1; then
    printf "  [OK]       %-15s -> %s\n" "$cmd" "$(command -v "$cmd")"
  else
    printf "  [OPT]      %-15s (optional, recommended)\n" "$cmd"
    missing_optional+=("$cmd")
  fi
done

# Java version must be >= 17
if command -v java >/dev/null 2>&1; then
  jver=$(java -version 2>&1 | awk -F '"' '/version/ {print $2}' | awk -F . '{print $1}')
  if [[ -z "$jver" || "$jver" -lt 17 ]]; then
    printf "  [WARN]     Java version is %s; jadx / vineflower need 17+\n" "${jver:-unknown}"
    status=1
  fi
fi

echo
if (( ${#missing_required[@]} > 0 )); then
  echo "Missing REQUIRED: ${missing_required[*]}"
  echo "Run: bash $(dirname "$0")/install-deps.sh"
fi
if (( ${#missing_optional[@]} > 0 )); then
  echo "Missing OPTIONAL: ${missing_optional[*]}"
fi

if (( status == 0 )); then
  echo "All required dependencies OK."
fi
exit "$status"
