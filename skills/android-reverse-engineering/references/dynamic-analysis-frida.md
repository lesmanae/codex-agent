# Dynamic Analysis with Frida / objection / mitmproxy

Dynamic analysis observes the app at runtime, which is the only way to:

- Bypass SSL pinning to capture traffic with `mitmproxy`.
- Dump strings that are decrypted only at runtime (StringFog, DexGuard,
  Paranoid, custom obfuscators).
- See the actual request/response payload shape (no amount of static
  analysis recovers a dynamic body field).
- Exercise code paths that depend on device state (root detection, emulator
  detection, attestation).

## Prerequisites (you must have ALL of these)

1. **A test device or rooted emulator**. On Devin's VM we don't have one
   attached by default; this phase runs from the user's machine or a
   separately-managed Android device. The Frida templates live here so the
   user can copy them over.
2. **frida-server on the device**. Download a matching version from
   <https://github.com/frida/frida/releases>, push to `/data/local/tmp`,
   `chmod +x`, run as root.
3. **An app you have the right to analyze.** See `legal-and-ethics.md`.

## Step-by-step: capture traffic from a pinned app

### 1. Set up mitmproxy

On your host (not Devin's VM):

```bash
mitmproxy --listen-host 0.0.0.0 --listen-port 8080
# Tap "mitmproxy CA cert" from mitmproxy UI, or:
# http://mitm.it on a device/emulator configured to use the proxy.
```

Install mitmproxy's CA on the device as a **system** CA (not user CA —
user-added CAs are not trusted by default on API 24+):

```bash
# On a rooted emulator:
adb root
adb remount
openssl x509 -inform PEM -subject_hash_old -in ~/.mitmproxy/mitmproxy-ca-cert.cer | head -1
# say it printed "c8750f0d"
adb push ~/.mitmproxy/mitmproxy-ca-cert.cer /system/etc/security/cacerts/c8750f0d.0
adb shell chmod 644 /system/etc/security/cacerts/c8750f0d.0
adb reboot
```

### 2. Configure the device to use the proxy

```bash
adb shell settings put global http_proxy 192.168.1.42:8080
```

### 3. Start the app with the SSL-pin bypass

```bash
frida -U -l scripts/frida/ssl-pin-bypass.js -f com.example.app
```

Requests now flow through mitmproxy and appear in its UI.

### 4. (Optional) Also log requests directly

```bash
frida -U -l scripts/frida/trace-okhttp.js -f com.example.app
```

This prints method, URL, headers, and body for every `OkHttpClient.newCall(...)`
invocation — useful when the proxy doesn't see traffic (non-OkHttp clients,
custom DNS-over-HTTPS implementations, etc.).

## Objection — runtime pentesting

Objection wraps Frida with a higher-level command line:

```bash
objection --gadget com.example.app explore
# inside the REPL:
android sslpinning disable
android hooking list activities
android hooking watch class com.example.app.UserRepository
android heap print_instances com.example.app.AuthManager
memory list modules
```

Useful commands:

- `android sslpinning disable` — built-in pin bypass for common libraries.
- `android root disable` — bypass root detection checks.
- `android hooking watch method '<class>' --dump-args --dump-return` —
  method-level tracing without writing a custom script.
- `android heap search instances '<class>'` — find live object instances
  (useful for grabbing `AuthManager` state after login).

## Template scripts (`scripts/frida/`)

### `ssl-pin-bypass.js`

Hooks:

- `okhttp3.CertificatePinner.check()`
- `com.android.org.conscrypt.TrustManagerImpl.verifyChain`
- Every loaded class ending in `TrustManager` / `TrustManagerImpl` →
  `checkServerTrusted` no-op
- `javax.net.ssl.HostnameVerifier.verify()` → `true`
- `HttpsURLConnection` default verifiers → no-op

The WebView SSL-error bypass is commented out by default; uncomment if the
app loads HTTPS URLs in a `WebView` and you specifically need that.

### `trace-okhttp.js`

Logs every OkHttp request (method, URL, headers, body) and response body,
truncated at 2 KB per message.

### `dump-strings.js`

Hooks common string-obfuscation decryption paths:

- `StringFog.decrypt`
- `javax.crypto.Cipher.doFinal([B)[B` → logs output if it's printable ASCII
- `android.util.Base64.decode` → logs output that looks like a URL or token

Add more library-specific hooks as you encounter them — the pattern is the
same: find the decryption entry point, hook it, log the return value.

## Frida troubleshooting

- **"Failed to attach: unable to connect to remote frida-server"** — version
  mismatch between `frida` (on host) and `frida-server` (on device). Match
  them exactly.
- **"Java.perform: Class not found: okhttp3.CertificatePinner"** — the app
  doesn't use OkHttp, or it uses an obfuscated / relocated copy. Check with
  `Java.enumerateLoadedClassesSync()` and look for `*CertificatePinner*`.
- **App crashes on launch when Frida is attached** — the app has tamper
  detection; try `frida -U --no-pause -l ssl-pin-bypass.js -f com.example.app`
  with early injection, or hook the tamper-detection routines first using
  `objection android root disable`.
- **Traffic still not visible** — the app uses cleartext DNS tunneling,
  Protocol Buffers over gRPC+TLS, or a non-OkHttp stack. Fall back to
  `tcpdump` on the device and decrypt with session keys exported via Frida.

## Scope reminder

Dynamic analysis is significantly more invasive than static. Running it
against an app you don't own or don't have permission to test is almost
certainly illegal / a terms-of-service breach. See `legal-and-ethics.md`.
