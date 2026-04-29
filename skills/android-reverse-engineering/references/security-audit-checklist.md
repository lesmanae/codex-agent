# Security Audit Checklist

The checks `audit-security.sh` runs, why each matters, and how to interpret
findings. Use this as reference when reading a `security-report.md`.

## A. Manifest flags

### A1. `android:debuggable="true"`

- **Risk**: anyone with adb access can attach `jdb`, dump process memory,
  inspect databases, or inject code. On a rooted device, that means any app
  can escalate. Production builds must have this `false`.
- **Severity**: FAIL when `true` in a release build.

### A2. `android:allowBackup="true"` (or unset on API ≤ 30)

- **Risk**: `adb backup` can exfiltrate app data to the host. Historically
  the default was `true`; since Android 12 the default is `false` for
  `targetSdkVersion 31+`, but many apps still opt in explicitly.
- **Severity**: WARN when `true`. Consider `FAIL` if the app stores
  credentials / PII locally.

### A3. `android:usesCleartextTraffic="true"`

- **Risk**: `http://` traffic is allowed app-wide, trivially MITM'able.
- **Severity**: FAIL for any app handling user accounts or payments.
- **Remediation**: set `false`, and if specific domains legitimately need
  cleartext, use `network_security_config.xml` to scope it.

### A4. `network_security_config.xml`

Look for:

- `<base-config cleartextTrafficPermitted="true">` — globally equivalent to
  A3.
- `<domain-config cleartextTrafficPermitted="true">` — per-domain. Check
  which domain.
- `<trust-anchors><certificates src="user" /></trust-anchors>` — user-added
  CAs are trusted; facilitates MITM on dev/test devices. OK for debug
  builds, FAIL for release.
- `<pin-set>` — certificate pinning declaration. If present, note the pins
  and compare against whatever Frida produces when you run
  `trace-okhttp.js`.

## B. Permissions

`audit-security.sh` flags the dangerous permissions from [Android's dangerous
permission list](https://developer.android.com/guide/topics/permissions/overview).
Pay extra attention to:

- `SYSTEM_ALERT_WINDOW` — tapjacking and overlay attacks.
- `BIND_ACCESSIBILITY_SERVICE` — full screen reading + input injection.
  Legitimate uses exist (accessibility apps, password managers) but it's
  also the signature permission of stalkerware and banking trojans.
- `REQUEST_INSTALL_PACKAGES` — can install other APKs; common in
  "app installer" apps and Play Store alternatives, rare elsewhere.
- `QUERY_ALL_PACKAGES` — enumerate every app on the device (API 30+
  requires justification for Play Store submission).
- `BIND_DEVICE_ADMIN` — can enforce policies, wipe device. Only legitimate
  in MDM / enterprise contexts.

## C. Exported components

An exported component without a permission guard is an external attack
surface — any other app (or shell via `am start`) can invoke it.

```xml
<activity android:name=".SomeActivity" android:exported="true">
    <intent-filter>
        <action android:name="android.intent.action.VIEW" />
        <category android:name="android.intent.category.DEFAULT" />
        <data android:scheme="myapp" />
    </intent-filter>
</activity>
```

- **Risk**: intent-spoofing, deeplink-injection, unauthenticated operations.
- **Severity**: WARN at minimum. Escalate to FAIL when:
  - The activity has a deeplink filter AND parses URI data without
    validation (check for `getIntent().getData()` usage).
  - The service / receiver performs a privileged action (e.g., dismissing
    a session, deleting data) without verifying caller identity via
    `Binder.getCallingUid()`.

## D. Deeplink schemes

List every `<data android:scheme="...">` and cross-check:

1. Is the scheme **registered with intent-filter-verification** (API 23+
   `android:autoVerify="true"` and an `assetlinks.json` on the domain)?
   Without this, any app can register the same scheme and intercept the
   deeplink.
2. Does the activity handling the deeplink **validate the data**? Look for
   `getIntent().getData()` followed by direct use without `Uri` sanitization.
   Common bugs: reading a URI param and using it as a file path
   (`path traversal`), as a WebView URL (`javascript:` scheme injection),
   or as a redirect target (open redirect).

## E. WebView misconfigurations

High-severity patterns (from the source-level scan):

- `setJavaScriptEnabled(true)` + `addJavascriptInterface(...)` → JS can call
  into Java. Pre-API 17 this was exploitable via reflection; API 17+
  restricts to `@JavascriptInterface`-annotated methods but a vulnerable
  interface method is still an RCE primitive.
- `setAllowFileAccess(true)` + loading user-controlled URLs → `file://`
  scheme reads local files.
- `setAllowContentAccess(true)` — access to other apps' content providers.
- `setAllowFileAccessFromFileURLs(true)` / `setAllowUniversalAccessFromFileURLs(true)`
  — same-origin bypass for `file://` documents.
- `setDomStorageEnabled(true)` + `setDatabaseEnabled(true)` without
  origin-scoped clearing — data retained across sessions.

Pair this audit with a read of every call site that constructs a `WebView`
to see what URL eventually reaches `loadUrl(...)`.

## F. TLS / TrustManager

Look for:

- `new X509TrustManager() { ... checkServerTrusted(...) {} ... }` — an empty
  `checkServerTrusted` means no certificate validation at all. If this code
  is live (not just defined-but-unused), the app is wide open to MITM.
- `HostnameVerifier { _, _ -> true }` — accepts any hostname.
- `OkHttpClient.Builder().hostnameVerifier(NullHostnameVerifier()).build()`.
- `SSLContext.init(null, trustAllCerts, new SecureRandom())`.

These are sometimes gated behind a `BuildConfig.DEBUG` check; verify whether
the flag is true in the release build you're auditing.

## G. Reflection / dynamic loading

- `Runtime.getRuntime().exec("...")` — shell-out. Arguments from
  user/remote input is command injection.
- `DexClassLoader` / `PathClassLoader` with a path that can be influenced
  from outside → loadable third-party code.
- `System.loadLibrary("name")` — loads native `.so` from `lib/abi/`. Check
  the native libraries for their own surface (JNI_OnLoad, exported
  functions).

## H. Crypto

Weak primitives found by `audit-security.sh`:

- DES / 3DES, RC4, MD5, SHA-1 — deprecated, don't use for anything
  sensitive.
- `Cipher.getInstance("AES")` without mode/padding → defaults to ECB, which
  leaks structure. Use `AES/GCM/NoPadding` or `AES/CBC/PKCS7Padding`.
- `IvParameterSpec(new byte[16])` → static all-zero IV; fatal for CBC.
- Keys derived from `String.getBytes()` of a short password → no KDF. Use
  `PBKDF2WithHmacSHA256` with a real salt and 100k+ iterations.

## I. Root / emulator detection

Not a vulnerability per se, but it tells you what the app is trying to defeat.
If you're doing authorized dynamic analysis, plan around:

- Checking for `/system/bin/su` / `/system/xbin/su`
- `Build.TAGS` containing `test-keys`
- `ro.debuggable` / `ro.secure` system properties
- Magisk / LSPosed / Riru detection

`objection`'s root-detection bypass handles most of these.

## J. Clipboard & screen-capture

- `ClipboardManager.setPrimaryClip(...)` with sensitive data (tokens, OTP).
  Leaks via global clipboard to other apps on ≤ API 31 without
  `ClipDescription.EXTRA_IS_SENSITIVE`.
- Absence of `FLAG_SECURE` on activities showing sensitive data → screen
  recording / screenshots capture it.

## Severity grading the skill uses

| Tag | Meaning | Example |
| --- | --- | --- |
| `OK` | Check passed | debuggable=false |
| `WARN` | Concerning; may be intentional | dangerous permission declared |
| `FAIL` | High confidence security issue | debuggable=true, cleartext=true |

`audit-security.sh` is conservative with `FAIL` — it only issues it for
unambiguous flags. Use `WARN`s as a starting point for manual review, not
as verdicts.
