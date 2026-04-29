# Apktool — quick reference

Apktool disassembles an APK to **smali** (Dalvik-bytecode-equivalent text),
decodes the binary `AndroidManifest.xml` and `resources.arsc` back to their
original XML, and can rebuild the whole thing back into a signable APK.

We use it alongside jadx because:

- jadx's manifest is re-encoded, not original. Apktool gives you the *exact*
  AXML-decoded manifest, which matters for security audits (e.g., verifying
  `android:exported`, `android:permission`, and intent-filter data).
- Smali is the ground truth when jadx produces broken or simplified Java.
- Resources (layouts, strings, drawables) come out in their original XML form.

## Common invocations

```bash
# Decode APK → smali + resources + manifest
apktool d -f -o ./smali-out app.apk

# Decode but skip resources (faster)
apktool d -r -o ./smali-out app.apk

# Decode without decoding sources (manifest + resources only)
apktool d -s -o ./smali-out app.apk

# Rebuild a patched APK
apktool b ./smali-out -o ./new-app.apk
# Then sign with apksigner or uber-apk-signer
```

## Layout of the output

```
./smali-out/
├── AndroidManifest.xml                    original AXML, decoded to text
├── apktool.yml                            apktool project metadata
├── res/                                   decoded resources
│   ├── values/                            strings, colors, dimensions, ...
│   ├── xml/                               network_security_config, file_provider, etc.
│   ├── layout/                            UI layouts
│   └── drawable/                          XML drawables
├── assets/                                raw assets (unchanged)
├── kotlin/                                Kotlin runtime stubs (not user code)
├── smali/                                 Dalvik bytecode, class-per-file
├── smali_classes2/                        for multi-dex APKs
├── META-INF/                              original signatures (if present)
└── unknown/                               anything apktool didn't recognize
```

## Smali cheat sheet

Smali maps 1:1 to Dalvik bytecode:

| Smali | Meaning |
| --- | --- |
| `invoke-static {...}, Lpkg/Cls;->name(...)V` | static call, void return |
| `invoke-virtual {v0, v1}, Lpkg/Cls;->m(I)V` | virtual call on `v0` with arg `v1` |
| `const-string v0, "https://api.example.com/"` | load string literal |
| `iget-object v0, p0, Lpkg/Cls;->field:Lpkg/T;` | read instance field |
| `sget-object v0, Lpkg/Cls;->FIELD:Lpkg/T;` | read static field |

Searching smali for hardcoded URLs and endpoints is often more reliable than
searching decompiled Java, because obfuscators leave strings intact.

## Reading `AndroidManifest.xml` from apktool

The skill's `audit-security.sh` prefers the apktool manifest when available.
If you're reading it manually, pay attention to:

- `android:exported="true"` without `android:permission` → attack surface
- `android:debuggable="true"` → leaks app data via adb
- `android:allowBackup` (default is `true` pre-Android 12) → leaks via adb
  backup
- `android:usesCleartextTraffic` → HTTP allowed
- `<intent-filter>` with `<action android:name="android.intent.action.VIEW" />`
  + `<data android:scheme="..." />` → deeplink; check what the target
  component does with the URI
- `<meta-data android:name="...">` → often config (e.g. Google Maps API keys,
  Firebase project IDs, Flutter entry points)
