#!/usr/bin/env bash
# =============================================================================
# install.sh — one-shot installer for codex-agent on a fresh Linux VPS
#
# Usage (as root):
#   curl -fsSL https://raw.githubusercontent.com/lesmanae/codex-agent/main/install.sh | bash
#   # or, if you've already cloned:
#   bash install.sh
#
# Authenticated install (private repo):
#   GITHUB_TOKEN=ghp_xxx bash <(curl -fsSL https://raw.githubusercontent.com/lesmanae/codex-agent/main/install.sh)
#   # ...or just run interactively and the script will prompt for a PAT.
#
# What it does:
#   1. Installs system deps (curl, git, util-linux for nsenter, nodejs+npm).
#   2. Installs docker + docker compose v2 if missing.
#   3. Installs Codex CLI globally (npm i -g @openai/codex).
#   4. Clones the repo to /opt/codex-agent if not already present.
#      Falls back to interactive PAT prompt if the repo is private.
#   5. Creates .env from .env.example with random API_PIN, API_JWT_SECRET,
#      ENCRYPTION_SECRET if missing.
#   6. Prints next-step instructions.
#
# It does NOT auto-start the API — you must fill in .env first (or accept
# the auto-generated values it scaffolded for you).
# =============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/lesmanae/codex-agent.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/codex-agent}"
GITHUB_USERNAME="${GITHUB_USERNAME:-lesmanae}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

[[ $EUID -eq 0 ]] || { err "must run as root"; exit 1; }

# Build an authenticated clone URL when the repo is private. Either GITHUB_TOKEN
# is already set (env or .netrc), or we'll prompt the operator below.
auth_url() {
    local bare="${REPO_URL#https://}"
    bare="${bare#*@}"
    if [[ -n "$GITHUB_TOKEN" ]]; then
        printf 'https://%s:%s@%s\n' "$GITHUB_USERNAME" "$GITHUB_TOKEN" "$bare"
    else
        printf 'https://%s\n' "$bare"
    fi
}

# --------------------------------------------------------------------------
# 1. System packages
# --------------------------------------------------------------------------
log "updating apt cache"
apt-get update -qq

log "installing base packages"
apt-get install -y --no-install-recommends \
    ca-certificates curl git jq util-linux \
    iputils-ping dnsutils less

# --------------------------------------------------------------------------
# 2. Docker + Docker Compose v2
# --------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    log "installing docker"
    apt-get install -y --no-install-recommends docker.io
    systemctl enable --now docker
else
    log "docker already installed: $(docker --version)"
fi

if ! docker compose version >/dev/null 2>&1; then
    log "installing docker compose plugin"
    apt-get install -y --no-install-recommends docker-compose-v2 \
        || apt-get install -y --no-install-recommends docker-compose-plugin
fi
log "docker compose: $(docker compose version || echo 'NOT FOUND')"

# --------------------------------------------------------------------------
# 3. Node.js + Codex CLI on the host
# --------------------------------------------------------------------------
if ! command -v node >/dev/null 2>&1; then
    log "installing nodejs + npm"
    if curl -fsSL https://deb.nodesource.com/setup_20.x | bash -; then
        apt-get install -y --no-install-recommends nodejs
    else
        warn "NodeSource setup failed; using distro nodejs"
        apt-get install -y --no-install-recommends nodejs npm
    fi
fi
log "node: $(node -v)   npm: $(npm -v)"

if ! command -v codex >/dev/null 2>&1; then
    log "installing @openai/codex globally"
    npm install -g @openai/codex
fi
log "codex: $(codex --version 2>&1 | head -1)"

# --------------------------------------------------------------------------
# 4. Clone repo (falls back to PAT prompt if the repo is private)
# --------------------------------------------------------------------------
try_clone() {
    GIT_TERMINAL_PROMPT=0 git clone --depth 1 -b "$REPO_BRANCH" "$1" "$INSTALL_DIR" 2>&1
}

if [[ ! -d "$INSTALL_DIR" ]]; then
    log "cloning $REPO_URL ($REPO_BRANCH) → $INSTALL_DIR"
    if ! try_clone "$(auth_url)" >/tmp/clone.log; then
        if grep -qE '(Authentication failed|could not read Username|terminal prompts disabled|repository .* not found|403|401)' /tmp/clone.log; then
            cat /tmp/clone.log >&2 || true
            warn "clone failed — repo is probably private and needs a PAT"
            if [[ -t 0 ]]; then
                read -rsp "Enter GitHub Personal Access Token (PAT) for $GITHUB_USERNAME: " GITHUB_TOKEN; echo
            else
                err "no PAT and no TTY available — re-run with: GITHUB_TOKEN=ghp_xxx bash install.sh"
                exit 1
            fi
            try_clone "$(auth_url)" || { err "clone still failed; check the PAT and its 'repo' scope"; exit 1; }
        else
            cat /tmp/clone.log >&2
            err "git clone failed; see error above"
            exit 1
        fi
    fi
else
    log "repo already present at $INSTALL_DIR — pulling latest"
    git -C "$INSTALL_DIR" pull --ff-only || warn "git pull skipped"
fi

# Replace the embedded-credential remote with a clean one + persistent
# credentials in /root/.git-credentials so future `git pull` keeps working.
if [[ -n "$GITHUB_TOKEN" ]]; then
    git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
    git -C "$INSTALL_DIR" config credential.helper store
    bare_host="${REPO_URL#https://}"; bare_host="${bare_host%%/*}"
    printf 'https://%s:%s@%s\n' "$GITHUB_USERNAME" "$GITHUB_TOKEN" "$bare_host" \
        > /root/.git-credentials
    chmod 600 /root/.git-credentials
fi

# --------------------------------------------------------------------------
# 5. .env scaffold with auto-generated secrets
# --------------------------------------------------------------------------
cd "$INSTALL_DIR"

gen_secret() {
    python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null \
        || head -c 64 /dev/urandom | xxd -p -c 64 | head -c 64
}

if [[ ! -f .env ]]; then
    log "creating .env from .env.example"
    cp .env.example .env
    chmod 600 .env

    rand_pin=$(python3 -c 'import secrets; print(secrets.randbelow(900000)+100000)' 2>/dev/null || echo "$(($RANDOM % 900000 + 100000))")
    rand_jwt=$(gen_secret)
    rand_enc=$(gen_secret)
    sed -i "s|^API_PIN=.*|API_PIN=$rand_pin|"                       .env
    sed -i "s|^API_JWT_SECRET=.*|API_JWT_SECRET=$rand_jwt|"         .env
    sed -i "s|^ENCRYPTION_SECRET=.*|ENCRYPTION_SECRET=$rand_enc|"   .env

    log "auto-generated API_PIN (6 digits): $rand_pin"
fi

# --------------------------------------------------------------------------
# 6. Codex workspace + auth dirs on the HOST
# --------------------------------------------------------------------------
mkdir -p /root/.codex /root/.codex-accounts /root/codex-workspace
( cd /root/codex-workspace && [[ -d .git ]] || git init -q )

# --------------------------------------------------------------------------
# 7. Done — print next steps
# --------------------------------------------------------------------------
PIN=$(grep -E '^API_PIN=' "$INSTALL_DIR/.env" | head -1 | cut -d= -f2-)

cat <<EOF

=============================================================================
 codex-agent installed at: $INSTALL_DIR
=============================================================================

Your auto-generated API PIN is:  $PIN
(stored in $INSTALL_DIR/.env — change it any time, then restart the container)

Next steps:

 1. (Optional) Edit .env to customize the PIN, log level, or whisper model:
      \$EDITOR $INSTALL_DIR/.env

 2. Build & start the API:
      cd $INSTALL_DIR
      docker compose up -d
      docker compose logs -f api

 3. The API listens on http://0.0.0.0:8001 (host network mode).
    Smoke test:
      curl -s http://127.0.0.1:8001/api/health
      # → {"ok":true,"version":"0.4.0"}

 4. Login your first ChatGPT Plus account:
      curl -s -X POST -H 'Content-Type: application/json' \\
        -d "{\\"pin\\":\\"$PIN\\"}" \\
        http://127.0.0.1:8001/api/auth/login
      # copy access_token, then:
      curl -s -X POST -H "Authorization: Bearer <token>" \\
        http://127.0.0.1:8001/api/codex/login/start
      # follow the auth_url, finish OAuth in browser, paste the
      # http://localhost:1455/auth/callback?... URL into:
      curl -s -X POST -H "Authorization: Bearer <token>" \\
        -H 'Content-Type: application/json' \\
        -d '{"callback_url":"http://localhost:1455/auth/callback?..."}' \\
        http://127.0.0.1:8001/api/codex/login/complete

 5. Expose to the internet via Cloudflare Tunnel:
      curl -fsSL https://pkg.cloudflare.com/install.sh | sudo bash || true
      apt install -y cloudflared
      cloudflared tunnel --url http://localhost:8001
      # copy the *.trycloudflare.com URL into the Android app settings

For more details see: $INSTALL_DIR/README.md

=============================================================================
EOF
