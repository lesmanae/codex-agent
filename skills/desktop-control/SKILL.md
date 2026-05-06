---
name: "desktop-control"
description: "Use when the user asks the agent to drive a real GUI browser on the codex-agent VPS — e.g. log into a site, click through a multi-step web flow, take a screenshot of a rendered page, or test JS-heavy SPAs that headless tools can't reach. The mobile app's Desktop tab streams the same X display live, so the user can watch and intervene. NOT for headless web scraping (use browser-automation), API testing (use api-test-suite-builder), or unit/E2E tests (use playwright-pro)."
---

# Desktop Control — drive the VPS Chrome live

The codex-agent VPS runs an `Xvfb` virtual display (`:99`) plus `x11vnc` and
`noVNC`, served behind the same Cloudflare Tunnel as the API:

```
mobile  ─►  https://codex.o69o.qzz.io/vnc/?autoconnect=1&resize=scale&path=websockify
            └─► cloudflared ─► localhost:6080 (websockify) ─► localhost:5900 (x11vnc) ─► :99 (Xvfb)
```

When the agent runs `DISPLAY=:99 google-chrome ...` on the host (via `nsenter`),
the mobile **Desktop** tab shows it live. The user can click and type in the
WebView; noVNC forwards those inputs straight to the X server.

## When to use

- **Logged-in workflows.** Agent needs to authenticate to a site that requires
  real browser cookies / 2FA / SSO.
- **Visual debugging.** "Why does this page render weird on my phone?" — open it
  on the desktop, screenshot, agent + user inspect together.
- **Mid-task user takeover.** Agent gets stuck on a CAPTCHA or a flaky modal.
  Pause, ask user via `ask_user`, user clicks through on the Desktop tab,
  agent resumes.
- **Form filling at scale where Playwright is overkill.** When the workflow
  is one-off and the user wants to watch.

## When NOT to use

- **Headless scraping / batch automation.** Use the **browser-automation**
  skill (Playwright). It's faster, has auto-wait, and doesn't tie up the GUI.
- **Writing browser tests / E2E suites.** Use **playwright-pro**.
- **API-only tasks.** If the target speaks HTTP, just `curl` it.

## How to drive it

The Xvfb display lives on the *host*, not in the codex-agent container. Codex
already runs on the host via `nsenter -t 1 -a --` so plain commands work:

```bash
# Open a URL
DISPLAY=:99 google-chrome --no-sandbox --disable-dev-shm-usage \
  --window-size=1280,800 --start-maximized https://example.com &

# Type / click via xdotool
DISPLAY=:99 xdotool key Return
DISPLAY=:99 xdotool type --delay 50 'hello@example.com'
DISPLAY=:99 xdotool key Tab
DISPLAY=:99 xdotool type --delay 50 'super-secret'
DISPLAY=:99 xdotool key Return

# Move/click at coordinates (browser content area is roughly y=120+)
DISPLAY=:99 xdotool mousemove 640 400 click 1

# Screenshot the whole display
DISPLAY=:99 import -window root /tmp/desktop.png
# (or: DISPLAY=:99 scrot /tmp/desktop.png)
```

If `import` / `scrot` are missing, install with `apt-get install -y imagemagick`
or `apt-get install -y scrot`.

### Healthchecks before driving

Before opening a browser, verify the X stack is alive:

```bash
# Is Xvfb running on :99?
pgrep -af 'Xvfb :99' >/dev/null && echo "xvfb ok" || echo "xvfb DOWN"

# Is x11vnc bound to 5900?
ss -ltn 'sport = :5900' | grep -q LISTEN && echo "x11vnc ok" || echo "x11vnc DOWN"

# Is noVNC reachable?
curl -sf http://127.0.0.1:6080/ | grep -qi novnc && echo "novnc ok" || echo "novnc DOWN"
```

If any are DOWN, restart with systemd:

```bash
systemctl restart xvfb@99.service x11vnc@99.service novnc.service
```

(If the units don't exist yet, see the install playbook in
`docs/desktop-setup.md` in the repo.)

### Talking to the user about it

The mobile app exposes a **Desktop** icon in the chat header (next to Folder
and Git). Tapping it opens a WebView pointing at the noVNC URL. A first-time
user sees a black screen until the agent opens something — that's expected,
not a bug. If you've just run `google-chrome ...`, mention it in your next
assistant message ("Aku udah buka [URL] di Desktop, bisa kamu cek tab
Desktop di app?").

## Anti-patterns

- Don't run multiple Chrome instances on `:99` simultaneously — the window
  manager is barebones and overlapping windows get hard to drive. Close the
  previous one before opening a new tab/page (`pkill -f 'google-chrome.*--user-data-dir=/root/.codex-chrome' ; sleep 1`).
- Don't use `--remote-debugging-port` to script Chrome via CDP from inside
  here — the mobile WebView has no way to attach. Use the **browser-automation**
  skill in a separate Playwright process if you need CDP.
- Don't paste secrets into Chrome via `xdotool type` if the user might be
  watching the noVNC stream — they will literally see the keystrokes.
  Use Chrome's password manager or a clipboard paste (`xdotool key ctrl+v`)
  after writing to the X clipboard with `xclip -selection clipboard`.

## Quick recipe — log into a site, leave it for the user

```bash
# 1. Open the login page
DISPLAY=:99 google-chrome --no-sandbox --start-maximized \
  --user-data-dir=/root/.codex-chrome \
  https://app.example.com/login &
sleep 4

# 2. Tell the user
codex-ask-user --question "Login form udah kebuka di Desktop tab. Login pakai akun kamu, terus reply 'lanjut' di sini." \
  --option "lanjut" --option "batal"

# 3. After user replies "lanjut", continue with whatever needs the auth
DISPLAY=:99 google-chrome --no-sandbox \
  --user-data-dir=/root/.codex-chrome \
  https://app.example.com/dashboard &
```

The `--user-data-dir=/root/.codex-chrome` flag persists cookies between agent
turns, so subsequent Codex turns don't have to re-login.

## Where the systemd units live

| Unit | What | File |
|---|---|---|
| `xvfb@99.service` | virtual X display `:99` | `/etc/systemd/system/xvfb@.service` |
| `x11vnc@99.service` | VNC bridge to `:99` on `:5900` | `/etc/systemd/system/x11vnc@.service` |
| `novnc.service` | websockify HTTP→VNC on `:6080` | `/etc/systemd/system/novnc.service` |

All three are `Restart=on-failure` and start at boot. The Cloudflare ingress
maps `/vnc/*` → `http://localhost:6080` on the same `codex.o69o.qzz.io`
hostname as the API.
