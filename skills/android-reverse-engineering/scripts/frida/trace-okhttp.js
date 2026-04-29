/*
 * trace-okhttp.js — log every OkHttp request + response (method, URL, headers, body).
 * Works with OkHttp v3.x and v4.x. Body logging is truncated at 2 KB per message.
 *
 * Usage:
 *   frida -U -l trace-okhttp.js -f com.example.app
 */

'use strict';

var MAX_BODY = 2048;

function toUtf8(buf) {
  try { return Java.use('java.lang.String').$new(buf, 'UTF-8'); }
  catch (e) { return '[binary ' + buf.length + ' bytes]'; }
}

Java.perform(function () {
  var OkRequest  = Java.use('okhttp3.Request');
  var RequestBody = Java.use('okhttp3.RequestBody');
  var OkResponse = Java.use('okhttp3.Response');
  var Buffer     = Java.use('okio.Buffer');

  // --- Requests: intercept newCall
  var OkClient = Java.use('okhttp3.OkHttpClient');
  OkClient.newCall.implementation = function (req) {
    try {
      var method = req.method();
      var url    = req.url().toString();
      var headers = req.headers().toString();
      var bodyStr = '';
      var body = req.body();
      if (body) {
        var buf = Buffer.$new();
        body.writeTo(buf);
        var bytes = buf.readByteArray();
        if (bytes.length > MAX_BODY) bytes = Java.array('byte', Array.prototype.slice.call(bytes, 0, MAX_BODY));
        bodyStr = toUtf8(bytes).toString();
      }
      console.log('\n[REQ] ' + method + ' ' + url);
      console.log(headers.trim());
      if (bodyStr) console.log('--- body ---\n' + bodyStr);
    } catch (e) { console.log('[trace-okhttp][req] ' + e); }
    return this.newCall(req);
  };

  // --- Responses: hook Response.body().string() (best-effort)
  try {
    var RB = Java.use('okhttp3.ResponseBody');
    var origString = RB.string;
    RB.string.implementation = function () {
      var s = origString.call(this);
      console.log('[RES] body: ' + (s.length > MAX_BODY ? s.substring(0, MAX_BODY) + '... [truncated]' : s));
      return s;
    };
  } catch (e) {}

  console.log('[trace-okhttp] active');
});
