#!/usr/bin/env bash
# install-deps.sh — install the full Android RE toolchain on Ubuntu/Debian.
# Idempotent: re-runs are safe.
# On Devin with the org-level env config applied, you do NOT need to run this —
# the VM snapshot should already have everything. This script is a fallback for
# fresh machines and for keeping the env config in sync.
set -euo pipefail

log()  { printf '>>> %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1; }

JADX_VER="${JADX_VER:-1.5.0}"
APKTOOL_VER="${APKTOOL_VER:-2.10.0}"
VINE_VER="${VINE_VER:-1.10.1}"
DEX2JAR_VER="${DEX2JAR_VER:-2.4}"
BUNDLETOOL_VER="${BUNDLETOOL_VER:-1.17.2}"

# --- apt packages ---
log "apt update + base packages"
sudo apt-get update -qq
sudo apt-get install -y -qq openjdk-17-jdk unzip wget curl pipx zip \
    2>&1 | tail -3 || true

# ensure pipx PATH
pipx ensurepath >/dev/null 2>&1 || true
export PATH="$HOME/.local/bin:$PATH"

# --- jadx ---
if ! need jadx; then
  log "installing jadx ${JADX_VER}"
  curl -fsSL "https://github.com/skylot/jadx/releases/download/v${JADX_VER}/jadx-${JADX_VER}.zip" -o /tmp/jadx.zip
  sudo mkdir -p /opt/jadx
  sudo unzip -q -o /tmp/jadx.zip -d /opt/jadx
  sudo ln -sf /opt/jadx/bin/jadx /usr/local/bin/jadx
fi

# --- apktool ---
if ! need apktool; then
  log "installing apktool ${APKTOOL_VER}"
  curl -fsSL "https://raw.githubusercontent.com/iBotPeaches/Apktool/master/scripts/linux/apktool" -o /tmp/apktool
  curl -fsSL "https://bitbucket.org/iBotPeaches/apktool/downloads/apktool_${APKTOOL_VER}.jar" -o /tmp/apktool.jar
  sudo install -m 0755 /tmp/apktool /usr/local/bin/apktool
  sudo install -m 0644 /tmp/apktool.jar /usr/local/bin/apktool.jar
fi

# --- vineflower ---
if [[ ! -f /opt/vineflower.jar ]]; then
  log "installing vineflower ${VINE_VER}"
  curl -fsSL "https://github.com/Vineflower/vineflower/releases/download/${VINE_VER}/vineflower-${VINE_VER}.jar" -o /tmp/vineflower.jar
  sudo install -m 0644 /tmp/vineflower.jar /opt/vineflower.jar
fi

# --- dex2jar ---
if ! need d2j-dex2jar; then
  log "installing dex2jar v${DEX2JAR_VER}"
  curl -fsSL "https://github.com/pxb1988/dex2jar/releases/download/v${DEX2JAR_VER}/dex-tools-v${DEX2JAR_VER}.zip" -o /tmp/d2j.zip
  sudo unzip -q -o /tmp/d2j.zip -d /opt/
  sudo chmod +x "/opt/dex-tools-v${DEX2JAR_VER}"/*.sh
  sudo ln -sf "/opt/dex-tools-v${DEX2JAR_VER}/d2j-dex2jar.sh" /usr/local/bin/d2j-dex2jar
  sudo ln -sf "/opt/dex-tools-v${DEX2JAR_VER}/d2j-jar2dex.sh" /usr/local/bin/d2j-jar2dex
fi

# --- bundletool ---
if ! need bundletool; then
  log "installing bundletool ${BUNDLETOOL_VER}"
  curl -fsSL "https://github.com/google/bundletool/releases/download/${BUNDLETOOL_VER}/bundletool-all-${BUNDLETOOL_VER}.jar" -o /tmp/bundletool.jar
  sudo install -m 0644 /tmp/bundletool.jar /usr/local/bin/bundletool.jar
  sudo tee /usr/local/bin/bundletool >/dev/null <<'EOF'
#!/bin/bash
exec java -jar /usr/local/bin/bundletool.jar "$@"
EOF
  sudo chmod +x /usr/local/bin/bundletool
fi

# --- Python tools via pipx ---
# objection needs Python >= 3.11 (uses tomllib). Prefer 3.12 if available.
PY312="$(command -v python3.12 2>/dev/null || true)"
PY311="$(command -v python3.11 2>/dev/null || true)"
PY_OBJ="${PY312:-${PY311:-}}"

install_pipx() {
  local pkg="$1" python="${2:-}"
  if ! pipx list 2>/dev/null | grep -q "^ *package ${pkg} "; then
    log "pipx install $pkg ${python:+(python=$python)}"
    if [[ -n "$python" ]]; then
      pipx install --python "$python" "$pkg"
    else
      pipx install "$pkg"
    fi
  fi
}

install_pipx androguard
install_pipx apkleaks
install_pipx frida-tools
install_pipx mitmproxy
install_pipx objection "$PY_OBJ"

# --- trufflehog ---
if ! need trufflehog; then
  log "installing trufflehog"
  curl -sSfL https://raw.githubusercontent.com/trufflesecurity/trufflehog/main/scripts/install.sh \
      | sudo sh -s -- -b /usr/local/bin
fi

log "done. run check-deps.sh to verify."
