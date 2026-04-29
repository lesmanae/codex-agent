# Setup Guide

## TL;DR

```bash
bash .devin/skills/android-reverse-engineering/scripts/install-deps.sh
bash .devin/skills/android-reverse-engineering/scripts/check-deps.sh
```

On Devin with the org-level environment config applied, **you do not need to
run `install-deps.sh`** — the VM snapshot already has everything. Use
`install-deps.sh` only when:

- You're running the skill outside Devin (on your own machine, CI, etc.).
- A new tool was added to the skill and the org env config hasn't been
  updated yet — then bump it in `install-deps.sh` and propagate via
  `devin_env update_config target=org`.

## What gets installed and where

| Tool | Installed via | Binary path |
| --- | --- | --- |
| OpenJDK 17 | `apt install openjdk-17-jdk` | `/usr/bin/java` |
| jadx | GitHub release zip → `/opt/jadx/` | `/usr/local/bin/jadx` |
| apktool | wrapper script + jar | `/usr/local/bin/apktool`, `/usr/local/bin/apktool.jar` |
| vineflower | GitHub release jar | `/opt/vineflower.jar` (run as `java -jar`) |
| dex2jar | GitHub release zip → `/opt/dex-tools-*/` | `/usr/local/bin/d2j-dex2jar` |
| bundletool | GitHub release jar + wrapper | `/usr/local/bin/bundletool`, `bundletool.jar` |
| androguard | `pipx install androguard` | `~/.local/bin/androguard` (library used via its venv python) |
| apkleaks | `pipx install apkleaks` | `~/.local/bin/apkleaks` |
| frida-tools | `pipx install frida-tools` | `~/.local/bin/frida*` |
| objection | `pipx install --python python3.12 objection` | `~/.local/bin/objection` |
| mitmproxy | `pipx install mitmproxy` | `~/.local/bin/mitmproxy` |
| trufflehog | official installer | `/usr/local/bin/trufflehog` |

## Python notes

- `objection` needs Python 3.11+ (uses `tomllib`). `install-deps.sh` auto-detects
  `python3.12` or `python3.11` and installs under that interpreter via
  `pipx install --python ...`.
- `gen-api-spec.py --mode inventory` needs the `androguard` library. Because
  `pipx` isolates each package in its own venv, the script auto re-executes
  under `~/.local/pipx/venvs/androguard/bin/python` when run with `python3`.
  Override with `ANDROGUARD_PYTHON=/path/to/python`.

## Supported host OS

- Ubuntu 22.04 / 24.04 (primary, what Devin VMs run)
- Debian 12
- Other Linux distros should work with minor tweaks. macOS works for the Java
  tools and most Python tools, but `trufflehog`'s install script assumes
  Linux; grab a macOS build from the Trufflehog releases page instead.
- Windows: unsupported here. The upstream Claude Code plugin has PowerShell
  scripts; port those yourself if needed.

## Version pinning

Versions are defined as shell variables at the top of `install-deps.sh`:

```bash
JADX_VER=1.5.0
APKTOOL_VER=2.10.0
VINE_VER=1.10.1
DEX2JAR_VER=2.4
BUNDLETOOL_VER=1.17.2
```

Override with environment variables (e.g. `JADX_VER=1.6.0 bash install-deps.sh`).

## Verifying the install

```bash
bash .devin/skills/android-reverse-engineering/scripts/check-deps.sh
```

Exits 0 if all required tools are present, 1 otherwise. Missing optional tools
are flagged but do not fail the check.

## Uninstall / reset

Everything goes into `/opt/*`, `/usr/local/bin/*`, and `~/.local/pipx/venvs/*`.
To reset:

```bash
sudo rm -rf /opt/jadx /opt/vineflower.jar /opt/dex-tools-*
sudo rm /usr/local/bin/{jadx,apktool,apktool.jar,d2j-*,bundletool,bundletool.jar,trufflehog}
pipx uninstall androguard apkleaks frida-tools objection mitmproxy
```
