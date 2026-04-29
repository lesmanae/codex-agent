#!/usr/bin/env bash
# audit-security.sh — produce a markdown security report from a decompiled APK.
# Reads from apktool output (smali/AndroidManifest.xml) AND jadx decompiled sources.
#
# Usage: audit-security.sh <decompiled-dir> [apk-file]
#   <decompiled-dir>  Directory containing jadx output + a "smali/" sibling from apktool.
#   [apk-file]        Optional: original APK, used for androguard permission sweep.
set -euo pipefail

DECOMP="${1:-}"
APK="${2:-}"

[[ -z "$DECOMP" ]] && { echo "Usage: $0 <decompiled-dir> [apk-file]" >&2; exit 2; }
[[ -d "$DECOMP" ]] || { echo "Error: not a directory: $DECOMP" >&2; exit 1; }

# Locate the real manifest. Prefer apktool (original AXML decoded), fall back to jadx's.
MANIFEST=""
for candidate in \
  "$(dirname "$DECOMP")/smali/AndroidManifest.xml" \
  "$DECOMP/../smali/AndroidManifest.xml" \
  "$DECOMP/smali/AndroidManifest.xml" \
  "$DECOMP/resources/AndroidManifest.xml" \
  "$DECOMP/jadx/resources/AndroidManifest.xml"; do
  if [[ -f "$candidate" ]]; then MANIFEST="$candidate"; break; fi
done

SRC_DIR=""
for candidate in \
  "$DECOMP/sources" \
  "$DECOMP/jadx/sources" \
  "$DECOMP/base/sources"; do
  if [[ -d "$candidate" ]]; then SRC_DIR="$candidate"; break; fi
done

NETSEC=""
for candidate in \
  "$(dirname "$DECOMP")/smali/res/xml/network_security_config.xml" \
  "$DECOMP/../smali/res/xml/network_security_config.xml" \
  "$DECOMP/resources/res/xml/network_security_config.xml" \
  "$DECOMP/jadx/resources/res/xml/network_security_config.xml"; do
  if [[ -f "$candidate" ]]; then NETSEC="$candidate"; break; fi
done

# -------------------- helpers --------------------
sec()   { printf '\n## %s\n\n' "$*"; }
sub()   { printf '\n### %s\n\n' "$*"; }
code()  { printf '```\n%s\n```\n' "$1"; }
ok()    { printf -- '- OK: %s\n' "$*"; }
warn()  { printf -- '- WARN: %s\n' "$*"; }
fail()  { printf -- '- FAIL: %s\n' "$*"; }
note()  { printf -- '- %s\n' "$*"; }

gs() {
  # grep in source, return file:line:match
  [[ -z "$SRC_DIR" ]] && return 0
  grep -rnE --include='*.java' --include='*.kt' --include='*.smali' \
       "$1" "$SRC_DIR" 2>/dev/null | head -50 || true
}

# -------------------- report --------------------
printf '# Security Audit Report\n\n'
printf '_Generated: %s_\n\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
printf 'Decompiled dir: `%s`\n' "$DECOMP"
[[ -n "$APK"      ]] && printf 'APK: `%s`\n'      "$APK"
[[ -n "$MANIFEST" ]] && printf 'Manifest: `%s`\n' "$MANIFEST"
[[ -n "$NETSEC"   ]] && printf 'Network security config: `%s`\n' "$NETSEC"

# ---------------- Manifest analysis ----------------
if [[ -n "$MANIFEST" ]]; then
  sec "Manifest flags"

  app_line=$(grep -E '<application\b' "$MANIFEST" | head -1 || true)
  if grep -qE 'android:debuggable="true"' "$MANIFEST"; then
    fail 'android:debuggable="true" — app is debuggable in production'
  else
    ok "android:debuggable not set to true"
  fi

  if grep -qE 'android:allowBackup="true"' "$MANIFEST" || ! grep -qE 'android:allowBackup' "$MANIFEST"; then
    warn "android:allowBackup is true (or unset → defaults to true). Adb backup may expose app data."
  else
    ok "android:allowBackup is false"
  fi

  if grep -qE 'android:usesCleartextTraffic="true"' "$MANIFEST"; then
    fail 'android:usesCleartextTraffic="true" — http:// traffic allowed app-wide'
  else
    ok "android:usesCleartextTraffic not true"
  fi

  sec "Permissions"
  declare -a DANGEROUS=(
    READ_SMS SEND_SMS RECEIVE_SMS READ_PHONE_STATE READ_PHONE_NUMBERS READ_CALL_LOG
    WRITE_CALL_LOG PROCESS_OUTGOING_CALLS ANSWER_PHONE_CALLS
    ACCESS_FINE_LOCATION ACCESS_COARSE_LOCATION ACCESS_BACKGROUND_LOCATION
    CAMERA RECORD_AUDIO
    READ_CONTACTS WRITE_CONTACTS GET_ACCOUNTS
    READ_EXTERNAL_STORAGE WRITE_EXTERNAL_STORAGE MANAGE_EXTERNAL_STORAGE
    SYSTEM_ALERT_WINDOW REQUEST_INSTALL_PACKAGES BIND_ACCESSIBILITY_SERVICE
    BIND_DEVICE_ADMIN BIND_NOTIFICATION_LISTENER_SERVICE
    QUERY_ALL_PACKAGES READ_LOGS WRITE_SECURE_SETTINGS
  )
  perms=$(grep -oE 'android\.permission\.[A-Z_]+' "$MANIFEST" | sort -u)
  if [[ -n "$perms" ]]; then
    while read -r p; do
      name="${p#android.permission.}"
      if printf '%s\n' "${DANGEROUS[@]}" | grep -qx "$name"; then
        warn "Dangerous permission declared: $p"
      else
        note "Permission: $p"
      fi
    done <<< "$perms"
  else
    note "No permissions declared."
  fi

  sec "Exported components without permission guard"
  for kind in activity service receiver provider; do
    while IFS= read -r line; do
      # Skip if explicit permission attr is present.
      grep -qE 'android:permission="' <<< "$line" && continue
      name=$(grep -oE 'android:name="[^"]+"' <<< "$line" | head -1 | cut -d\" -f2)
      warn "Exported <$kind> without permission: $name"
    done < <(grep -E "<$kind\b[^>]*android:exported=\"true\"" "$MANIFEST" || true)
  done

  sec "Deeplink schemes"
  mapfile -t schemes < <(grep -oE 'android:scheme="[^"]+"' "$MANIFEST" | cut -d\" -f2 | sort -u)
  if (( ${#schemes[@]} > 0 )); then
    for s in "${schemes[@]}"; do
      note "scheme: $s"
    done
  else
    note "No custom deeplink schemes."
  fi
fi

# ---------------- Network security config ----------------
if [[ -n "$NETSEC" ]]; then
  sec "network_security_config.xml"
  code "$(cat "$NETSEC")"
  if grep -qE 'cleartextTrafficPermitted="true"' "$NETSEC"; then
    warn "cleartextTrafficPermitted=true in network_security_config (per-domain cleartext allowed)"
  fi
  if grep -qE '<certificates[^/]*src="user"' "$NETSEC"; then
    warn "User-added CA certificates are trusted — facilitates MITM in dev builds"
  fi
fi

# ---------------- Source-level red flags ----------------
if [[ -n "$SRC_DIR" ]]; then
  sec "Source-level red flags"

  sub "WebView misconfigurations"
  hits=$(gs '(setJavaScriptEnabled\s*\(\s*true|addJavascriptInterface|setAllowFileAccess\s*\(\s*true|setAllowContentAccess\s*\(\s*true|setAllowFileAccessFromFileURLs|setAllowUniversalAccessFromFileURLs)')
  [[ -n "$hits" ]] && code "$hits" || ok "No obvious WebView misconfig patterns found."

  sub "Custom TrustManager / HostnameVerifier"
  hits=$(gs '(X509TrustManager|HostnameVerifier|SSLSocketFactory|TrustManager\s*\[\s*\])')
  [[ -n "$hits" ]] && code "$hits" || ok "No custom trust manager references found."

  sub "Certificate-pinning bypass candidates (verify returns true unconditionally)"
  hits=$(gs '(checkServerTrusted\s*\([^)]*\)\s*\{|verify\s*\([^)]*\)\s*\{)')
  [[ -n "$hits" ]] && code "$(printf '%s' "$hits" | head -30)" || note "No empty/trivial trust callbacks spotted."

  sub "Reflection / dynamic loading"
  hits=$(gs '(Runtime\.getRuntime\(\)\.exec|DexClassLoader|PathClassLoader|System\.loadLibrary)')
  [[ -n "$hits" ]] && code "$(printf '%s' "$hits" | head -20)" || ok "No dynamic loading or exec calls found."

  sub "Root / emulator detection"
  hits=$(gs '(test-keys|/system/bin/su|/system/xbin/su|Build\.TAGS|Build\.FINGERPRINT|ro\.debuggable)')
  [[ -n "$hits" ]] && code "$(printf '%s' "$hits" | head -20)"

  sub "Clipboard & screen-capture"
  hits=$(gs '(ClipboardManager|setPrimaryClip|FLAG_SECURE)')
  [[ -n "$hits" ]] && code "$(printf '%s' "$hits" | head -15)"

  sub "Crypto: weak primitives"
  hits=$(gs '(DES/|RC4|MD5|SHA-1\b|ECB\b|Cipher\.getInstance\("AES"\)|IvParameterSpec\(new byte\[)')
  [[ -n "$hits" ]] && code "$(printf '%s' "$hits" | head -20)" || ok "No obvious weak-crypto calls found."
fi

printf '\n---\n_End of security audit._\n'
