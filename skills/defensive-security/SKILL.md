# Defensive Security & Server Hardening — Skill

**Trigger phrases**: harden vps, harden server, audit my server, audit my vps,
audit own system, lockdown, security hardening, server hardening, defensive,
defense, fail2ban, ufw setup, firewall config, sshd config, ssh hardening,
disable root login, ssh key only, modsecurity, waf config, log analysis,
analisa log auth, login attempt, brute force attempt, intrusion detection,
who logged in, last login, lastb, faillog, suspicious login, unusual activity,
unauthorized access on my server, rotate keys, rotate secrets, rotate password,
backup my server, backup config, restore from backup, lynis audit, chkrootkit,
rkhunter, file integrity, aide, tripwire, audit logs, journalctl audit,
patch level, apt upgrade security, unattended-upgrades, fix CVE on my server,
permission cleanup, world-writable, suid, sgid, capability, apparmor, selinux.

This skill helps the operator harden, audit, and monitor servers and
applications **that they own** (the VPS this bot runs on, plus any
infrastructure they have explicit authority over). Scope is purely
defensive: configuring protective controls, analyzing logs from the
operator's own systems, validating their patch level, and recovering
from incidents on systems they administer.

---

## Authorization assumption

This skill assumes — and the bot's persona enforces — that every target
discussed in the chat is a system the operator owns or has been
explicitly contracted to defend. Frame all work in terms of
"my server", "our infrastructure", or "the system I administer".

---

## Operating principles

- **Backup before changing config.** Always
  `cp /etc/<file>{,.bak.$(date +%s)}` before editing.
- **Validate before reload.** `sshd -t`, `nginx -t`, `apparmor_parser
  --check`, `systemd-analyze verify` — broken configs lock you out.
- **Never disable the only access path on a remote box.** Test the new
  config in a second SSH session before closing the first.
- **Log everything.** Date-stamp output to `/var/log/` or `/tmp/audit/`
  so a later audit can reproduce the state you saw.
- **Defense in depth.** Firewall + fail2ban + sshd hardening + monitoring
  + backup. None of them alone is enough.

---

## Quick "is my server in shape?" sweep

```bash
# basic info
hostnamectl
uptime
free -h
df -h

# patch level
apt list --upgradable 2>/dev/null | head -20
unattended-upgrade --dry-run -d 2>&1 | tail -20

# who's logged in / recent logins
who -a
last -n 10
lastb -n 10                # failed logins (needs btmp readable)

# anything listening
ss -tlnp

# firewall state
ufw status verbose
iptables -L -n -v --line-numbers | head

# systemd failed units
systemctl --failed
```

## Lynis (full self-audit)

```bash
apt-get install -y lynis
lynis audit system --quick --no-colors > /tmp/lynis.txt
grep -E '^\s*(Suggestion|Warning)' /tmp/lynis.txt
# Score:
grep 'Hardening index' /tmp/lynis.txt
```

Address Warnings first, then Suggestions in priority order.

---

## SSH hardening (`/etc/ssh/sshd_config`)

```
PermitRootLogin prohibit-password    # SSH-key only for root, no pw
PasswordAuthentication no            # whole server
PubkeyAuthentication yes
ChallengeResponseAuthentication no
KbdInteractiveAuthentication no
UsePAM yes
X11Forwarding no
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 20
ClientAliveInterval 300
ClientAliveCountMax 2
AllowUsers ubuntu admin              # whitelist
```

Test, then reload:
```bash
sudo sshd -t && sudo systemctl reload ssh
# keep your current SSH session open; open a NEW one to confirm login still works
```

## ufw (Ubuntu) — minimal allowlist

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'ssh'
ufw allow 80,443/tcp comment 'web'
ufw enable
ufw status numbered
```

## fail2ban (auto-block brute-force on your services)

```bash
apt-get install -y fail2ban
cat > /etc/fail2ban/jail.local <<'CFG'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true

[nginx-http-auth]
enabled = true

[nginx-botsearch]
enabled = true
CFG
systemctl enable --now fail2ban
fail2ban-client status sshd
```

## Automatic security updates

```bash
apt-get install -y unattended-upgrades
dpkg-reconfigure --priority=low unattended-upgrades
systemctl status unattended-upgrades --no-pager
```

---

## Log triage on your own box

```bash
# auth failures by source IP (your /var/log/auth.log)
grep 'Failed password' /var/log/auth.log | \
  awk '{for (i=1;i<=NF;i++) if ($i=="from") print $(i+1)}' | \
  sort | uniq -c | sort -rn | head -20

# successful root logins
grep 'Accepted' /var/log/auth.log | grep -i root | tail

# nginx 4xx/5xx top offending IPs (your nginx log)
awk '$9 ~ /^[45]/ {print $1, $9}' /var/log/nginx/access.log | \
  sort | uniq -c | sort -rn | head

# systemd-journal anomalies
journalctl -p err --since '24 hours ago' --no-pager | head -50
journalctl -k --since '24 hours ago' | grep -iE 'oom|kill|panic|fatal'
```

## Incident-response: "did someone get in?"

```bash
# any new users in /etc/passwd recently?
ls -la /etc/passwd /etc/shadow
awk -F: '$3 >= 1000 && $3 < 65534 {print}' /etc/passwd

# unusual cron entries
ls -la /etc/cron.* /etc/cron.d/ /var/spool/cron/
for u in $(cut -d: -f1 /etc/passwd); do crontab -u "$u" -l 2>/dev/null; done

# SUID files (look for newly added ones)
find / -xdev -perm -4000 -type f 2>/dev/null

# files modified in /etc in the last 24h
find /etc -xdev -type f -mtime -1 -ls 2>/dev/null

# active connections + processes
ss -tnp state established
ps -eo user,pid,ppid,etime,cmd --sort=-etime | head -20
```

---

## File integrity baseline

```bash
apt-get install -y aide
aideinit                                          # creates initial DB (slow)
mv /var/lib/aide/aide.db.new /var/lib/aide/aide.db
aide --check | head -100                          # later, to detect drift
```

## Rotate secrets you may have leaked

If a secret was committed to git, exposed in a screenshot, or pasted to
a chat:
1. **Rotate first**, then clean up. Don't reverse the order.
2. SSH key: regenerate (`ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_new`),
   add new pubkey to `~/.ssh/authorized_keys` on every server, remove old.
3. API token: revoke in provider console, generate new, update env / vault.
4. DB password: `ALTER USER ... WITH PASSWORD ...;`, update connection
   strings, restart consumers.

## Backup (encrypted, your data, your storage)

```bash
# tar + age (modern encryption)
tar -czf - /etc /var/lib/myapp | age -r age1xyz... > backup-$(date +%F).tar.gz.age
# decrypt later:
age -d -i ~/.age/key.txt backup-2025-12-31.tar.gz.age | tar -xzf -

# rsync to remote server you also control
rsync -aHAXxv --delete /etc/ root@backup-host:/srv/backups/$(hostname)/etc/
```

---

## Pitfalls

- Disabling the only sshd_config access method while logged in remotely.
  Always keep a second session open while reloading sshd.
- Setting `ufw deny` for port 22 before adding `ufw allow 22` → instant
  lockout.
- Running `chmod 600` on `/etc/shadow` is right; running it on
  `/etc/passwd` breaks login. Know which is which.
- "I rotated the key" without updating consumers → service outage.
- Aggressive fail2ban `bantime = -1` (permanent) on a production-facing
  box bans real users. Start at 1h.
