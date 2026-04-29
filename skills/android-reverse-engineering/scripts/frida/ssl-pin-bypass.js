/*
 * ssl-pin-bypass.js — neutralize common Android SSL-pinning mechanisms.
 *
 * USE ONLY on apps you have a legitimate right to analyze. Running this against
 * a production app you don't own is almost certainly a terms-of-service breach
 * and may be illegal in your jurisdiction.
 *
 * Covers:
 *   - OkHttp CertificatePinner.check()
 *   - TrustManagerImpl.verifyChain (conscrypt)
 *   - javax.net.ssl.X509TrustManager implementations
 *   - HostnameVerifier
 *   - WebView SSL error ignoring (optional — disabled by default)
 *
 * Usage:
 *   frida -U -l ssl-pin-bypass.js -f com.example.app
 */

'use strict';

function tryHook(name, thunk) {
  try { thunk(); console.log('[+] hooked ' + name); }
  catch (e) { /* class not present in this app */ }
}

Java.perform(function () {
  // --- OkHttp v3 / v4 ---
  tryHook('okhttp3.CertificatePinner.check()', function () {
    var CP = Java.use('okhttp3.CertificatePinner');
    CP.check.overload('java.lang.String', 'java.util.List').implementation = function () { return; };
    CP['check$okhttp'] && (CP['check$okhttp'].implementation = function () { return; });
  });

  // --- Conscrypt / platform TrustManagerImpl ---
  tryHook('com.android.org.conscrypt.TrustManagerImpl.verifyChain', function () {
    var TM = Java.use('com.android.org.conscrypt.TrustManagerImpl');
    TM.verifyChain.implementation = function (untrustedChain) { return untrustedChain; };
  });

  // --- Generic X509TrustManager ---
  tryHook('javax.net.ssl.X509TrustManager', function () {
    var TMs = Java.enumerateLoadedClassesSync().filter(function (n) {
      return n.endsWith('TrustManager') || n.endsWith('TrustManagerImpl');
    });
    TMs.forEach(function (cn) {
      try {
        var K = Java.use(cn);
        if (K.checkServerTrusted) {
          K.checkServerTrusted.overloads.forEach(function (o) {
            o.implementation = function () { /* trust everything */ };
          });
        }
      } catch (e) {}
    });
  });

  // --- HostnameVerifier ---
  tryHook('javax.net.ssl.HostnameVerifier', function () {
    var HV = Java.use('javax.net.ssl.HostnameVerifier');
    HV.verify.implementation = function () { return true; };
  });

  // --- HttpsURLConnection default SSLSocketFactory ---
  tryHook('javax.net.ssl.HttpsURLConnection', function () {
    var HUC = Java.use('javax.net.ssl.HttpsURLConnection');
    HUC.setDefaultHostnameVerifier.implementation = function () {};
    HUC.setHostnameVerifier.implementation = function () {};
  });

  // --- WebView SSL errors (disabled by default) ---
  // Uncomment only when you actually need it — apps may rely on certificate
  // validation for more than TLS.
  /*
  tryHook('android.webkit.WebViewClient.onReceivedSslError', function () {
    var WVC = Java.use('android.webkit.WebViewClient');
    WVC.onReceivedSslError.implementation = function (view, handler) {
      handler.proceed();
    };
  });
  */

  console.log('[ssl-pin-bypass] active');
});
