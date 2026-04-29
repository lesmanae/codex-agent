#!/usr/bin/env bash
# find-api-calls.sh — grep decompiled source for HTTP API surface.
# Covers: Retrofit, OkHttp, Volley, Ktor, HttpURLConnection, WebSocket,
#         GraphQL, gRPC, hardcoded URLs, auth patterns.
#
# Usage: find-api-calls.sh [--retrofit|--okhttp|--volley|--ktor|--urls|--auth|
#                           --graphql|--grpc|--websocket|--all] <source-dir>
set -euo pipefail

usage() {
  cat <<EOF
Usage: find-api-calls.sh [OPTIONS] <source-dir>

Flags (pick one; default: --all):
  --retrofit   --okhttp   --volley   --ktor
  --urls       --auth     --graphql  --grpc     --websocket
  --all        (default)

Output format: file:line:match
EOF
  exit 0
}

MODE="all"
SRC=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --retrofit|--okhttp|--volley|--ktor|--urls|--auth|--graphql|--grpc|--websocket|--all)
      MODE="${1#--}"; shift ;;
    -h|--help) usage ;;
    -*) echo "Unknown option: $1" >&2; exit 2 ;;
    *)  SRC="$1"; shift ;;
  esac
done

[[ -z "$SRC" ]] && { echo "Error: no source dir" >&2; usage; }
[[ -d "$SRC" ]] || { echo "Error: not a directory: $SRC" >&2; exit 1; }

section() { printf '\n==== %s ====\n\n' "$1"; }
gg()      { grep -rnE --include='*.java' --include='*.kt' --include='*.smali' \
               --include='*.xml' --include='*.properties' --include='*.json' \
               "$1" "$SRC" 2>/dev/null || true; }
ggi()     { grep -rnEi --include='*.java' --include='*.kt' --include='*.smali' \
               --include='*.xml' --include='*.properties' --include='*.json' \
               "$1" "$SRC" 2>/dev/null || true; }

want() { [[ "$MODE" == "all" || "$MODE" == "$1" ]]; }

if want retrofit; then
  section "Retrofit — endpoint annotations"
  gg '@(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|HTTP)\s*\('
  section "Retrofit — parameter annotations"
  gg '@(Headers|Header|HeaderMap|Query|QueryMap|QueryName|Path|Body|Field|FieldMap|Part|PartMap|Url|Streaming|Multipart|FormUrlEncoded|Tag)\s*[\(({]'
  section "Retrofit — base URL builder"
  gg '(baseUrl|base_url|BASE_URL)\s*[\(=]'
fi

if want okhttp; then
  section "OkHttp — request building"
  gg '(Request\.Builder|HttpUrl|\.newCall|\.enqueue|\.addInterceptor|\.addNetworkInterceptor|MediaType\.(parse|get))'
  section "OkHttp — URL construction"
  gg '(\.url\s*\(|\.addQueryParameter|\.addPathSegment|\.scheme\s*\(|\.host\s*\()'
  section "OkHttp — pinning / trust"
  gg '(CertificatePinner|HostnameVerifier|SSLContext\.getInstance|TrustManager|X509TrustManager)'
fi

if want volley; then
  section "Volley — requests"
  gg '(StringRequest|JsonObjectRequest|JsonArrayRequest|ImageRequest|RequestQueue|Volley\.newRequestQueue|HurlStack)'
fi

if want ktor; then
  section "Ktor client"
  gg '(HttpClient\s*\(|ktor\.client|io\.ktor|Url\s*\(|\.url\s*\{)'
  gg '(io\.ktor\.client\.request\.(get|post|put|delete|patch|head|options))'
fi

if want urls; then
  section "Hardcoded URLs (http[s]://)"
  gg '"https?://[^"[:space:]]+'
  section "HttpURLConnection"
  gg '(openConnection|setRequestMethod|HttpURLConnection|HttpsURLConnection|URI\.create)'
  section "WebView URLs"
  gg '(loadUrl|loadData|loadDataWithBaseURL|evaluateJavascript|addJavascriptInterface|WebViewClient|WebChromeClient)'
fi

if want auth; then
  section "Auth / API keys / tokens"
  ggi '(api[_-]?key|auth[_-]?token|bearer\s|authorization|x-api-key|client[_-]?secret|access[_-]?token|refresh[_-]?token|id[_-]?token)'
  section "Host / base URL constants"
  ggi '(BASE_URL|API_URL|SERVER_URL|ENDPOINT|API_BASE|HOST_NAME|GATEWAY|CDN_URL)'
fi

if want graphql; then
  section "GraphQL (Apollo / plain)"
  gg '(com\.apollographql\.apollo|ApolloClient|\.query\s*\(|\.mutation\s*\(|\.subscription\s*\()'
  gg '"\s*(query|mutation|subscription)\b[^"]{0,200}\{'
fi

if want grpc; then
  section "gRPC"
  gg '(io\.grpc|ManagedChannelBuilder|\.forAddress\s*\(|\.newBlockingStub|\.newStub)'
fi

if want websocket; then
  section "WebSocket"
  gg '(WebSocket|newWebSocket|okhttp3\.WebSocket|javax\.websocket|tyrus|\.wss?://)'
fi

echo
echo "=== find-api-calls complete ==="
