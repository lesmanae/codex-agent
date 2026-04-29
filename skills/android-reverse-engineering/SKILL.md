# Android Reverse Engineering & API Extraction — Devin Skill

**Trigger phrases**: decompile APK, decompile XAPK, decompile AAB, reverse engineer Android app,
analyze Android app, extract API from APK, find endpoints in APK, jadx, apktool, vineflower,
fernflower, androguard, frida, MobSF, audit APK security, Android security audit,
reverse engineer Android, `.apk`, `.xapk`, `.aab`, `.aar`, `.jar`, 반컴파일, 反编译APK,
安卓逆向, analisa APK, decompile Android.

This skill decompiles Android app packages (APK / XAPK / AAB / AAR / JAR), audits their security
posture, extracts HTTP API endpoints, and produces reproducible artifacts (OpenAPI + Postman +
Bruno collections) plus a consolidated markdown report. Dynamic analysis via Frida / objection /
MobSF is supported as an opt-in phase.

> **Legal & ethical scope — read before using this skill.**
> Use this skill ONLY on APKs you have a legitimate right to analyze:
> apps you own or built, bug-bounty targets that are **in scope** and allow reverse engineering,
> malware samples for defensive research, or packages you have explicit written authorization to
> analyze. Do NOT use this skill to bypass licensing / DRM, to lift paid APIs or content from
> competitors, to circumvent app-store or platform terms, or to produce derivative products from
> proprietary binaries. If the request or the target falls outside these bounds, refuse the task
> and explain why. See `references/legal-and-ethics.md`.

---

## When to trigger

Activate this skill when the user asks to decompile, reverse engineer, audit, or extract API
endpoints from an Android package (`.apk`, `.xapk`, `.aab`, `.aar`, `.jar`) — or asks for a
structured security / API report derived from one. Do NOT trigger for iOS `.ipa` files (use a
different toolchain), generic JAR libraries that clearly aren't Android, or tasks that are
obviously offensive in intent (see legal scope above).

---

## Prerequisites

All required tools are expected to be **pre-installed on the Devin VM** via the organization-level
environment config. The full toolchain is:

| Tool | Purpose |
| --- | --- |
| Java 17+ (`java`) | Runtime for jadx, vineflower, apktool, bundletool |
| `jadx` | Primary APK/JAR/AAR decompiler |
| `vineflower` (jar) | Higher-quality Java decompiler for complex bytecode |
| `d2j-dex2jar` | DEX → JAR bridge so Vineflower can decompile APK DEX files |
| `apktool` | Smali + resource + manifest extraction (original AXML/ARSC) |
| `bundletool` | Android App Bundle (`.aab`) → APK set |
| `androguard` | Programmatic APK analysis (Python) |
| `apkleaks` | Secret / URI / endpoint regex scanner on APKs |
| `trufflehog` | Entropy-based secret scanner on decompiled source |
| `frida` / `frida-tools` | Dynamic instrumentation (runtime hooking) |
| `objection` | Frida-based mobile pentest toolkit |
| `mitmproxy` | Proxy for traffic capture (requires test device/emulator) |

To verify everything is available, run:

```bash
bash .devin/skills/android-reverse-engineering/scripts/check-deps.sh
```

If anything is missing on a fresh machine, run once:

```bash
bash .devin/skills/android-reverse-engineering/scripts/install-deps.sh
```

On Devin with the org env config applied, you should never need `install-deps.sh`.

---

## Workflow

The skill has **8 phases**. For the common "analyze this APK end-to-end" request, use the
orchestrator:

```bash
bash .devin/skills/android-reverse-engineering/scripts/analyze-apk.sh <file.apk|file.xapk|file.aab> \
    --output ./analysis-<appname>
```

This runs phases 1–6 (static) and stops before dynamic analysis. Phase 7 & 8 are opt-in.

### Phase 1 — Verify dependencies

Run `check-deps.sh`. If any required tool is missing, run `install-deps.sh` once. Do NOT proceed
until `check-deps.sh` exits 0.

### Phase 2 — Normalize input

- `.aab` → convert to APK set with `bundletool build-apks --mode=universal`, then extract the
  universal APK. See `references/apktool-usage.md` (bundletool section).
- `.xapk` → extract ZIP, detect base APK + config splits. The decompile script handles this.
- `.apk` / `.jar` / `.aar` → pass through.

### Phase 3 — Decompile

```bash
bash scripts/decompile.sh --engine both --deobf <input> -o <output>/decompiled
```

`--engine both` runs jadx + vineflower and produces two parallel outputs. For quick first-pass on
a very large APK use `--engine jadx --no-res`. For a JAR / AAR use `--engine vineflower`.

Smali-level artefacts come from apktool:
```bash
apktool d -f -o <output>/smali <input>
```
Smali is invaluable when jadx produces broken Java (complex lambdas, unusual DEX patterns) and for
reading the **original** `AndroidManifest.xml` and resources without jadx's re-encoding.

See `references/jadx-usage.md`, `references/vineflower-usage.md`, `references/apktool-usage.md`.

### Phase 4 — Static inventory (androguard)

```bash
python3 scripts/gen-api-spec.py --mode=inventory --apk <input> --out <output>/inventory.json
```

Androguard extracts programmatically (not via grep):
- App metadata (package, versionCode/Name, min/target SDK)
- All permissions + dangerous-permission flagging
- All activities/services/receivers/providers + exported flags
- Declared intent filters & deeplink schemes
- Full string table + URL candidates
- Native libraries (`lib/*/*.so`)

See `references/androguard-queries.md` for custom queries.

### Phase 5 — Security audit

```bash
bash scripts/audit-security.sh <output>/decompiled <input> > <output>/security-report.md
```

Produces a markdown report flagging:
- Dangerous permissions declared
- Exported components without permission guard (activity/service/receiver/provider)
- `android:debuggable`, `android:allowBackup`, `android:usesCleartextTraffic`
- Lax `network_security_config.xml` (cleartext domains, user-added CAs trusted)
- WebView misconfigurations (`setJavaScriptEnabled` + `addJavascriptInterface`, `setAllowFileAccess`)
- Custom `TrustManager` / `HostnameVerifier` that return true unconditionally (pinning bypass risk)
- Deeplink schemes exposed without validation

See `references/security-audit-checklist.md`.

### Phase 6 — Secret & endpoint extraction

```bash
bash scripts/extract-secrets.sh <input> <output>/decompiled > <output>/secrets-report.md
bash scripts/find-api-calls.sh <output>/decompiled/sources > <output>/api-matches.txt
python3 scripts/gen-api-spec.py --mode=api --src <output>/decompiled/sources --out <output>/
```

`gen-api-spec.py` produces:
- `api.openapi.yaml` — OpenAPI 3.0 spec built from Retrofit interfaces (`@GET`/`@POST`/etc.)
- `api.postman.json` — Postman v2.1 collection (import-ready)
- `api.bruno/` — Bruno collection tree
- `api-report.md` — endpoints + file:line + base URL candidates + auth-header hits

`find-api-calls.sh` is a regex sweep covering Retrofit, OkHttp, Volley, Ktor, HttpURLConnection,
GraphQL, gRPC, WebSocket. Use it as a **supplement** to androguard output, not a replacement.

See `references/api-extraction-patterns.md`.

### Phase 7 — Dynamic analysis (opt-in)

Only run this phase when (a) you have a test device or rooted emulator, and (b) the user
explicitly asks for dynamic analysis. Never run dynamic analysis against a production device.

Frida templates provided in `scripts/frida/`:
- `ssl-pin-bypass.js` — neutralize OkHttp `CertificatePinner` + WebView checks + custom TrustManagers
- `trace-okhttp.js` — log every OkHttp request/response (method + URL + headers + body)
- `dump-strings.js` — dump decrypted strings from common obfuscation libraries

Usage:
```bash
frida -U -l scripts/frida/trace-okhttp.js -f com.example.app
```

MobSF integration:
```bash
bash scripts/run-mobsf.sh <input>      # uploads to local MobSF container, pulls JSON+markdown
```

See `references/dynamic-analysis-frida.md`.

### Phase 8 — Consolidate report & persist knowledge

Write `<output>/REPORT.md` that merges `security-report.md`, `secrets-report.md`, `api-report.md`,
and inventory summary. Attach `REPORT.md` + `api.openapi.yaml` + `api.postman.json` to
`message_user`.

If the analysis is substantive and reusable across future sessions (e.g., the user works with this
app repeatedly), propose a knowledge note containing package structure, architecture pattern
(MVVM / Clean / MVP), DI framework, HTTP stack, and endpoint summary. Use
`devin_env suggest_knowledge` or `devin_mcp` playbook/note tools — **suggest, do not auto-write**.

---

## Output conventions

All analysis output goes under `./analysis-<appname>/` (or a user-specified directory). Never write
to the repo root unless the user explicitly requested artifacts in-repo. Large decompiled trees
(`decompiled/`, `smali/`) should be `.gitignore`d.

## Engine selection quick reference

| Situation | Engine |
| --- | --- |
| First pass on any APK | `jadx` (fastest, does resources) |
| JAR / AAR library | `vineflower` (better Java output) |
| jadx output has warnings / broken code | `both`, then diff-review |
| Complex lambdas, generics, streams | `vineflower` |
| Need original manifest / smali / resources | `apktool` |
| Quick scan of a huge APK | `jadx --no-res` |
| `.aab` (Android App Bundle) | `bundletool` → extract → `jadx` |

## Common failure modes

- **"Outer APK has very few Java files"** → it's a split-APK bundle wrapper. `decompile.sh` auto-
  detects this and re-decompiles `base.apk`.
- **Vineflower fails on APK** → you need `d2j-dex2jar` to convert DEX to JAR first. `decompile.sh`
  handles this automatically when `--engine vineflower|both`.
- **Obfuscated (ProGuard/R8) class names** → anchor searches on string literals, Retrofit
  annotations, and library API symbols. Those are never obfuscated. See
  `references/call-flow-analysis.md`.
- **Native code (`.so`)** → decompile with Ghidra / radare2 (not covered by this skill). Flag in
  report and ask user if they want native analysis.
