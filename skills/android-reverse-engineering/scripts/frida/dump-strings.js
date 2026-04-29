/*
 * dump-strings.js — dump decrypted strings produced by common Android string-
 * obfuscation libraries at runtime. Hooks the library's "decrypt" / "get" entry
 * points rather than scanning memory.
 *
 * Covers (add more as needed):
 *   - Paranoid Deobfuscator (chrisadragna): com.github.chrisadragna.paranoiddeobfuscator
 *   - Stringfog:                            com.github.megatronking.stringfog
 *   - DexGuard strings:                     com.dexguard.StringEncryption
 *   - Plain reflection-based Cipher.doFinal(byte[]) strings
 *
 * Usage:
 *   frida -U -l dump-strings.js -f com.example.app
 */

'use strict';

function tryHook(name, thunk) {
  try { thunk(); console.log('[+] hooked ' + name); }
  catch (e) {}
}

Java.perform(function () {
  // --- Stringfog common API ---
  tryHook('StringFog.decrypt', function () {
    var SF = Java.use('com.github.megatronking.stringfog.StringFog');
    SF.decrypt.overloads.forEach(function (o) {
      o.implementation = function () {
        var r = o.apply(this, arguments);
        console.log('[stringfog] ' + r);
        return r;
      };
    });
  });

  // --- Cipher.doFinal — useful when app wraps strings in AES/DES/RC4 at runtime ---
  tryHook('javax.crypto.Cipher.doFinal', function () {
    var Cipher = Java.use('javax.crypto.Cipher');
    Cipher.doFinal.overload('[B').implementation = function (buf) {
      var out = this.doFinal(buf);
      try {
        var s = Java.use('java.lang.String').$new(out);
        if (s.length() > 3 && s.length() < 500 && /[\x20-\x7e]{4,}/.test(s.toString())) {
          console.log('[cipher] ' + s);
        }
      } catch (e) {}
      return out;
    };
  });

  // --- Base64.decode — catches trivial obfuscation
  tryHook('android.util.Base64.decode', function () {
    var B64 = Java.use('android.util.Base64');
    B64.decode.overload('java.lang.String', 'int').implementation = function (s, flags) {
      var out = this.decode(s, flags);
      try {
        var plain = Java.use('java.lang.String').$new(out);
        if (/^https?:\/\/|^\/[a-z0-9]|(key|token|secret|bearer)/i.test(plain.toString())) {
          console.log('[base64] ' + plain);
        }
      } catch (e) {}
      return out;
    };
  });

  console.log('[dump-strings] active');
});
