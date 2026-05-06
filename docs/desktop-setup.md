# Desktop control (Xvfb + x11vnc + noVNC) — host setup

Added in v0.8.0. The codex-agent backend itself doesn't run any of this — it
all lives on the **VPS host** because the agent already runs on the host (via
`nsenter`) and we want the X stack to outlive container restarts.

---

## What you're installing

```
mobile WebView ──► https://codex.<your-domain>/vnc/?…
                  │
                  └── Cloudflare Tunnel ingress (/vnc/* → :6080)
                  │
                  └── websockify (:6080)  ──► x11vnc (:5900) ──► Xvfb (:99)
                                                                │
                                                                └── google-chrome,
                                                                    xdotool, scrot, …
```

Three systemd units, one Cloudflare ingress rule, two apt installs. ~2-3 GB
of disk including Chromium.

---

## 1. Install packages on the VPS host

```bash
ssh root@<vps>
apt-get update
apt-get install -y \
  xvfb x11vnc novnc websockify \
  fonts-noto-color-emoji fonts-liberation \
  imagemagick scrot \
  xdotool wmctrl \
  xauth dbus-x11
# Chromium-or-Chrome (any one)
apt-get install -y chromium-browser || \
  apt-get install -y chromium || {
    # Last resort: Google Chrome stable from Google's APT
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
      | gpg --dearmor -o /etc/apt/keyrings/google-linux-signing.gpg
    echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/google-linux-signing.gpg] https://dl.google.com/linux/chrome/deb/ stable main' \
      > /etc/apt/sources.list.d/google-chrome.list
    apt-get update && apt-get install -y google-chrome-stable
  }
```

`apt` ships an older noVNC, but it's fine for our use — the websockify HTTP
endpoint at `/` includes a usable `vnc.html` and the `websockify` Python
process accepts the noVNC web socket protocol.

---

## 2. Systemd units (drop-in)

### `/etc/systemd/system/xvfb@.service`

```ini
[Unit]
Description=Xvfb virtual display :%i
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :%i -screen 0 1280x800x24 -ac +extension RANDR -nolisten tcp
Restart=on-failure
RestartSec=2
User=root

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/x11vnc@.service`

```ini
[Unit]
Description=x11vnc bridge for display :%i
After=xvfb@%i.service
Requires=xvfb@%i.service

[Service]
Type=simple
# -nopw is OK because we expose this only via the cloudflared tunnel which
# runs on the same hostname as the API and is gated by the API's own auth
# in front (the noVNC URL itself is unguessable enough for our threat model
# — if you want to harden, switch to -passwdfile and bake a token).
ExecStart=/usr/bin/x11vnc -display :%i -forever -shared -rfbport 5900 -nopw -noxdamage -bg -o /var/log/x11vnc.log
ExecStop=/usr/bin/pkill -x x11vnc
Restart=on-failure
RestartSec=2
User=root

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/novnc.service`

```ini
[Unit]
Description=noVNC websockify HTTP→VNC bridge
After=x11vnc@99.service
Requires=x11vnc@99.service

[Service]
Type=simple
ExecStart=/usr/bin/websockify --web /usr/share/novnc 6080 127.0.0.1:5900
Restart=on-failure
RestartSec=2
User=root

[Install]
WantedBy=multi-user.target
```

Enable + start (display `:99`):

```bash
systemctl daemon-reload
systemctl enable --now xvfb@99.service x11vnc@99.service novnc.service
systemctl status xvfb@99 x11vnc@99 novnc --no-pager | head -30

# Smoke test
curl -sf http://127.0.0.1:6080/vnc.html >/dev/null && echo "novnc OK" || echo "novnc DOWN"
DISPLAY=:99 google-chrome --no-sandbox --headless=new --dump-dom https://example.com >/dev/null && echo "chrome OK"
```

---

## 3. Cloudflare Tunnel ingress

The codex-agent named tunnel already serves `codex.<your-domain>` on path `/`
to `http://localhost:8001`. Add a path-prefix rule for `/vnc/*` that points
at the noVNC port. **The new rule must come BEFORE the catch-all** in
`config.yml`:

```yaml
# /etc/cloudflared/<your-tunnel-name>.yml (or wherever your tunnel config lives)
tunnel: <tunnel-id>
credentials-file: /etc/cloudflared/<tunnel-id>.json

ingress:
  - hostname: codex.<your-domain>
    path: ^/vnc/?.*$
    service: http://127.0.0.1:6080
    originRequest:
      noTLSVerify: true
  - hostname: codex.<your-domain>
    service: http://127.0.0.1:8001
  - service: http_status:404
```

Validate + reload:

```bash
cloudflared tunnel --config /etc/cloudflared/<file>.yml ingress validate
systemctl restart cloudflared@<tunnel-name>.service
journalctl -u cloudflared@<tunnel-name>.service -n 30 --no-pager
```

Smoke test from your laptop:

```bash
curl -sf https://codex.<your-domain>/vnc/vnc.html | grep -qi 'noVNC' && echo "tunnel OK"
```

The mobile app's DesktopScreen WebView points at:

```
https://codex.<your-domain>/vnc/vnc.html?autoconnect=1&resize=scale&path=websockify
```

---

## 4. Verify end-to-end

From a browser (or the mobile WebView):

```
https://codex.<your-domain>/vnc/vnc.html?autoconnect=1&resize=scale&path=websockify
```

You should see a black 1280×800 desktop. From a shell on the VPS:

```bash
DISPLAY=:99 google-chrome --no-sandbox --start-maximized https://example.com &
```

Within 1-2 seconds the WebView (and any noVNC tab) renders Example.com inside
the desktop. Tap/click in the WebView — the cursor should move on the desktop.

---

## 5. Persistent Chrome profile (cookies survive)

When the agent uses the desktop to log into a site, persist the cookies so
later turns don't have to re-login:

```bash
DISPLAY=:99 google-chrome --no-sandbox --start-maximized \
  --user-data-dir=/root/.codex-chrome \
  https://app.example.com/login &
```

The `/root/.codex-chrome` directory survives reboots (it's on the host disk).
Don't commit it; it contains real session cookies.

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `xvfb@99` unit shows `(code=exited, status=1)` immediately | Another X server is already on `:99` | `pgrep -af 'Xvfb :99'` then kill stale process |
| WebView shows "Connection failed" | `novnc.service` is down or websockify can't reach `:5900` | `systemctl status novnc` and `ss -ltn 'sport = :5900'` |
| WebView connects but desktop is black & nothing renders Chrome | Chrome opening on host display, not `:99` | Always set `DISPLAY=:99` explicitly |
| Cloudflare returns Cloudflare HTML challenge instead of noVNC | Bot Fight Mode / Browser Integrity Check ON | Same fix as the API: turn them OFF in the zone settings |
| `xdotool` types nothing visible | Wrong window has focus | `DISPLAY=:99 xdotool search --name 'Chrome' windowactivate` first |

---

## 7. Security notes

- The noVNC bridge is **unauthenticated** — anyone with the
  `https://codex.<your-domain>/vnc/...` URL can drive the desktop. The Cloudflare
  hostname is non-public and the path is non-discoverable, but this is still
  the weakest link in the stack. To harden:
  - Switch x11vnc to `-passwdfile /etc/x11vnc.passwd` and bake the password
    into the WebView URL via the noVNC `password=...` query param.
  - Or: put a Cloudflare Access policy in front of `/vnc/*` so only your
    user can hit it.
- The desktop runs as **root** with full host filesystem access. Don't open
  arbitrary URLs the user hasn't asked for, and treat anything typed via
  `xdotool` as live-streamed to whoever's watching the WebView.
- The persistent Chrome profile (`/root/.codex-chrome`) contains real
  session cookies — back it up like any other secret material, and clear
  it (`rm -rf /root/.codex-chrome`) when handing the VPS off.
