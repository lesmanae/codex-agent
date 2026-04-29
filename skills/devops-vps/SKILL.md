# VPS DevOps & Container Operations — Skill

**Trigger phrases**: deploy, restart container, restart service, edit nginx,
nginx config, reload nginx, sertifikat ssl, certbot, lets encrypt, ufw, firewall,
buka port, tutup port, systemd, service status, journalctl, docker compose,
docker ps, docker logs, build image, push image, registry, traefik, caddy,
reverse proxy, nginx-proxy-manager, npm, fail2ban, swap, sysctl, cron, crontab,
backup vps, restore vps, rsync, snapshot, monitor cpu, monitor disk, htop,
prometheus, grafana, restart bot, restart api, deploy update, blue green.

This skill operates a Linux VPS as root. Use it when the user asks to manage
services (systemd / docker), web servers (nginx / caddy / apache), TLS
certificates (certbot / acme.sh), firewalls (ufw / iptables / nftables),
schedulers (cron / systemd timers), or container deployments (docker /
docker compose / podman). Default audience is admin who lives in the VPS
shell — be terse, show concrete commands, and verify before destructive ops.

---

## Operating principles

- **Read before write.** Cat the config file before editing. Confirm the
  service unit name, port, and user before restarting or replacing.
- **Diff > rewrite.** When editing nginx/systemd/etc, use `sed -i` /
  `patch` / a focused edit, never re-emit whole files unless creating new ones.
- **Test config before reload.** Run `nginx -t`, `apachectl configtest`,
  `caddy validate --config ...`, `systemd-analyze verify <unit>` before
  applying. Never reload broken configs.
- **Backup mutating changes.** Before `rm`, large `sed -i`, or replacing a
  config: `cp /etc/nginx/sites-available/foo{,.bak.$(date +%s)}`.
- **Fail loud, recover fast.** After every change verify with the matching
  health check (`curl -fsS https://...`, `docker ps`, `systemctl is-active`).
- **Don't leak secrets.** Never echo `.env`, private keys, or auth tokens
  back to chat. Mention "loaded from /path/.env" instead.

---

## Toolbox (verify before use)

```bash
which docker docker-compose nginx caddy systemctl journalctl ufw iptables \
      certbot acme.sh fail2ban-client rsync htop iotop iftop ss netstat
```

If a tool is missing, install via `apt-get update && apt-get install -y <pkg>`
(this VPS is Ubuntu/Debian based).

---

## Common recipes

### Restart a docker compose stack and verify

```bash
cd /opt/<project> && \
  docker compose pull && \
  docker compose up -d --remove-orphans && \
  docker compose ps && \
  docker compose logs --tail=50 -f
```

### Edit nginx vhost safely

```bash
F=/etc/nginx/sites-available/<site>
cp "$F" "$F.bak.$(date +%s)"
$EDITOR "$F"           # or sed/patch
nginx -t && systemctl reload nginx
```

### Issue/renew TLS with certbot (nginx)

```bash
certbot --nginx -d example.com -d www.example.com --redirect --agree-tos \
        -m admin@example.com --no-eff-email
certbot renew --dry-run
```

### Open a port with ufw

```bash
ufw allow 443/tcp comment 'https'
ufw status numbered
```

### Investigate a flaky systemd service

```bash
systemctl status <unit> --no-pager
journalctl -u <unit> -n 200 --no-pager
journalctl -u <unit> --since '1 hour ago' -p err
```

### Disk pressure investigation

```bash
df -h
du -sh /* 2>/dev/null | sort -h | tail
du -sh /var/log/* /var/lib/docker/* 2>/dev/null | sort -h | tail
journalctl --vacuum-size=200M     # if /var/log/journal is huge
docker system df
docker system prune -f            # ONLY if user agrees
```

### Restart this bot itself

```bash
cd /opt/codex-agent && docker compose restart && \
  docker logs --tail 30 codex-agent
```

---

## Things to never do without explicit confirmation

- `rm -rf` on anything outside `/tmp` or the obvious target dir
- `iptables -F`, `ufw --force reset`, `ufw disable` on a remote VPS — you
  will lock yourself out
- Reboot or `shutdown` (no out-of-band access here)
- Force-delete docker volumes, prune `--all`, `prune -a --volumes`
- Edit `/etc/passwd`, `/etc/shadow`, sshd keys/config without backup +
  re-validation in another session
- Disable/remove the only sshd_config `PermitRootLogin` line while logged
  in as root over SSH

---

## Verification checklist after any change

1. The intended service is `active (running)` and listening on the right
   port (`ss -tlnp | grep <port>`).
2. The user-facing URL responds with the expected status (`curl -I`).
3. Logs in the last 60s show no new tracebacks.
4. Disk and memory are not pressured (`df -h`, `free -h`).
