# Linux Troubleshooting — Skill

**Trigger phrases**: vps lambat, vps lemot, server lambat, cpu tinggi, ram
penuh, oom, out of memory, disk penuh, no space left, inode full, load
tinggi, banyak proses, kernel panic, segfault, dmesg, syslog, journalctl,
analisa log, error log, troubleshoot, debug server, network slow, latency,
ping, traceroute, mtr, dns lambat, gak konek, port blocked, connection
refused, connection timeout, ssl error, certificate expired, time skew, ntp,
clock drift, file descriptor, too many open files, fd leak, zombie process,
defunct, swap thrashing, iowait, top, htop, atop, iotop, iftop, nethogs, ss,
netstat, lsof, strace, ltrace, perf, sar, dstat, vmstat, free, df, du,
fdisk, lsblk, mount, fstab, raid, lvm.

Use when the user reports something is "slow", "broken", "down", or asks
to investigate why a Linux system is misbehaving. Audience: admin who
needs the actual diagnostic, not a textbook.

---

## Operating principles

- **Measure before fixing.** No "restart and hope". Confirm the symptom
  with numbers (CPU %, RAM, disk %, latency) before touching anything.
- **Top-down: cpu → memory → disk → network → app.** Most outages
  bottom out in one of those four resources.
- **Never restart a service without checking its logs first.** A
  restart can mask the failing root cause.
- **Snapshot evidence.** Before fixing, capture current state so we can
  compare after: `top -bn1 > /tmp/top.before; ss -s; df -h`.

---

## First 60 seconds (USE method)

```bash
uptime                              # load average
free -h                             # RAM, swap
df -h                               # disk
ss -s                               # socket summary
top -bn1 | head -20                 # CPU & top processes
dmesg --since '15 min ago' --level=err,warn,crit
journalctl -p 3 -xb --since '15 min ago' --no-pager  # boot-time errors
```

If you don't see anything obvious, drill in.

---

## CPU saturation

```bash
top                                 # interactive; press '1' for per-core
htop                                # if installed
pidstat 1 5                         # per-process CPU over 5s
mpstat -P ALL 1 3                   # per-core idle/iowait/sys

# kernel-time vs user-time split
sar -u 1 5                          # ALL row → %user %sys %iowait %idle

# what's the noisy process doing?
strace -c -p <pid>                  # syscall histogram (10s, ctrl-c)
perf top -p <pid>                   # live function-level (needs perf)
```

## Memory pressure / OOM

```bash
free -h
cat /proc/meminfo | head -20
ps -eo pid,ppid,user,rss,%mem,cmd --sort=-rss | head -15
dmesg | grep -iE 'oom|killed process'
journalctl -k --since '1 hour ago' | grep -iE 'oom|memory'
echo "swap usage per pid:"; for f in /proc/*/status; do
  awk '/VmSwap/{s=$2} /^Name/{n=$2} /^Pid/{p=$2} END{if(s>0) printf "%-25s pid=%-6s swap=%dkB\n", n, p, s}' "$f" 2>/dev/null
done | sort -k3 -n -r | head -10
```

If swap is hot: the OS is paging. Either add RAM, find the leaker, or
add swap (`fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`).

## Disk full / I/O bottleneck

```bash
df -h                               # by FS
df -hi                              # inode usage (often the silent killer)
du -sh /var/* 2>/dev/null | sort -h | tail
du -sh /var/log/* 2>/dev/null | sort -h | tail
journalctl --vacuum-size=200M       # if /var/log/journal is huge

# I/O activity
iostat -x 1 5                       # sysstat
iotop -ao                           # cumulative reads/writes per process
```

Find the largest files in a tree:
```bash
find / -xdev -type f -size +100M -printf "%s\t%p\n" 2>/dev/null | sort -rn | head
```

## Network / connectivity

```bash
ip addr; ip route                   # interfaces + default route
ss -tulnp                           # listening sockets + owners
ss -taonp state established          # active connections
ping -c4 1.1.1.1                    # raw IP reachability
ping -c4 google.com                 # DNS works?
mtr -c10 -r google.com              # latency + packet loss per hop
dig google.com +short               # DNS resolution
curl -v -m 5 https://example.com    # full TLS+HTTP trace
ss -tin                             # cwnd, rtt, retrans
```

DNS slow? Check `/etc/resolv.conf`, try `dig @1.1.1.1`.

Port apparently blocked?
```bash
nc -zv host 443
ufw status
iptables -L -n -v
```

## Open files / FD leaks

```bash
ulimit -n                           # current shell limit
cat /proc/<pid>/limits | grep 'open files'
ls -1 /proc/<pid>/fd | wc -l        # how many FDs the process has
lsof -p <pid> | head -50
lsof | wc -l                        # system-wide
```

## Zombies / defunct

```bash
ps -eo pid,ppid,stat,cmd | awk '$3 ~ /Z/'
# kill the parent (the zombie's child entry is freed when parent reaps it)
```

## SSL / time issues

```bash
openssl s_client -servername host -connect host:443 </dev/null 2>/dev/null \
    | openssl x509 -noout -subject -issuer -dates
date; timedatectl status
chrony tracking || ntpq -p
```

---

## Capture-and-compare

For "intermittent" issues, leave a sampler running:

```bash
( while sleep 5; do
    date '+%T'; uptime; free -h | awk 'NR==2'; df -h / | tail -1
  done ) > /tmp/watch.log &
```

Stop with `kill %1` after the issue reproduces, then `less /tmp/watch.log`.

## Pitfalls

- `top` shows CPU% per-core; with multiple cores a process can be at
  400% — that's 4 cores. Press `1` to see per-core breakdown.
- `df` reports clean even when inodes are exhausted. Always check `df -hi`.
- High `%iowait` blames the disk; really the process is just waiting on
  I/O — fix the process, not the disk.
- `kill -9` on a stuck process leaves zombies and unflushed buffers.
  Try `SIGTERM` (15) first.
