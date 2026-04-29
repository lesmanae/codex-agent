# jadx — quick reference

`jadx` is the primary decompiler. It handles `.apk`, `.xapk` (via our wrapper),
`.jar`, `.aar`, and raw `.dex` files natively, and also decodes Android resources
(AXML, ARSC, PNG/WebP, XML, strings).

## Common invocations

```bash
# Default: decompile into ./<name>-decompiled/
jadx app.apk

# Custom output
jadx -d out/ app.apk

# Deobfuscate mangled names (recommended for ProGuard/R8 output)
jadx --deobf app.apk

# Skip resources (faster, code only)
jadx --no-res app.apk

# Show decompiler errors in output
jadx --show-bad-code app.apk

# Multi-threaded (default) — tune for your machine
jadx -j 8 app.apk
```

## Layout of the output

```
<output>/
├── sources/              # decompiled .java files
│   └── com/example/...
├── resources/
│   ├── AndroidManifest.xml      # jadx-re-encoded manifest (readable)
│   ├── res/                     # decoded resources
│   └── assets/                  # raw assets
└── <appname>.jadx.kts   # jadx-gui project file (ignored in scripts)
```

> Note: for the *original* binary manifest and resources, use `apktool` — it
> preserves the exact AXML. jadx's manifest is reconstructed.

## When jadx struggles

- **"Code has been simplified" warnings** → feed the class through Vineflower
  as a second pass. Compare the two and take whichever reads cleaner.
- **"Unknown opcode" / "finish with errors"** → enable `--show-bad-code` and
  cross-check with smali from `apktool`.
- **Too few files out** → split-APK wrapper; `decompile.sh` handles this
  automatically.
- **Obfuscated class names** → `--deobf` maps `a.b.c` → deterministic
  `C0001a.C0001b.C0001c` style. Anchor your reading on string literals and
  library-annotation boundaries, which are NOT obfuscated.

## Performance tips

- `--no-res` cuts 30-50% off large APKs if you only care about code.
- `-j 1` single-threaded if jadx OOMs on a big APK; default parallelism can
  spike RSS.
- For a 100+ MB APK, pass `--no-debug-info` and bump the JVM heap:
  `JAVA_OPTS="-Xmx6g" jadx app.apk`.
