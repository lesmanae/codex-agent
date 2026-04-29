# API Extraction Patterns

How to turn decompiled Android code into a useful description of the app's
HTTP API. This guide covers the anchor patterns and how `gen-api-spec.py`
turns them into an OpenAPI 3 spec.

## 1. Retrofit (most common on modern Android)

Retrofit is an interface with method annotations:

```java
public interface UserApi {
    @GET("users/{id}")
    Call<User> getUser(@Path("id") long id,
                       @Header("Authorization") String bearer);

    @POST("users")
    @Headers({"Content-Type: application/json"})
    Call<User> createUser(@Body CreateUserRequest body);
}
```

Anchors that are **never obfuscated** (because Retrofit resolves them via
reflection at runtime, so ProGuard preserves them when a proper `-keep`
rule is in place, which nearly every app ships):

- `@GET`, `@POST`, `@PUT`, `@DELETE`, `@PATCH`, `@HEAD`, `@OPTIONS`, `@HTTP`
- `@Path`, `@Query`, `@QueryMap`, `@QueryName`, `@Body`, `@Field`, `@FieldMap`,
  `@Part`, `@PartMap`, `@Headers`, `@Header`, `@HeaderMap`, `@Url`
- `@FormUrlEncoded`, `@Multipart`, `@Streaming`

`gen-api-spec.py` scans Java/Kotlin files for these and produces one OpenAPI
operation per method annotation. Path parameters (`{id}`) map cleanly to
OpenAPI `path` parameters; `@Query` → `query` parameters; `@Header` → `header`
parameters; `@Body` / `@Field` / `@Part` → request body.

## 2. Base URL resolution

Usually constructed with `Retrofit.Builder().baseUrl(...)`:

```kotlin
val retrofit = Retrofit.Builder()
    .baseUrl("https://api.example.com/v1/")
    .addConverterFactory(GsonConverterFactory.create())
    .build()
```

`gen-api-spec.py` looks for:

- `.baseUrl("...")` calls
- `BASE_URL = "..."` / `base_url = "..."` constants
- Failing those, the most frequent `https://host/` root among hardcoded URLs

If the app has multiple `Retrofit` instances (e.g. prod + debug), the script
picks the first HTTPS one. Override manually for production use.

## 3. OkHttp (raw, without Retrofit)

```kotlin
val client = OkHttpClient()
val req = Request.Builder()
    .url("https://api.example.com/v1/users/$id")
    .addHeader("Authorization", "Bearer $token")
    .build()
val resp = client.newCall(req).execute()
```

Anchors (partial list — `find-api-calls.sh --okhttp`):

- `Request.Builder` / `HttpUrl` / `MediaType.parse` / `MediaType.get`
- `.url(`, `.addQueryParameter`, `.addPathSegment`, `.scheme(`, `.host(`
- `.newCall(` + `.enqueue(` or `.execute()`
- `.addInterceptor(` / `.addNetworkInterceptor(` (auth / logging hooks)

These don't give you a clean mapping to OpenAPI — you usually need to pair
grep with manual reading to reconstruct the request shape.

## 4. Ktor

Kotlin Multiplatform HTTP client:

```kotlin
val client = HttpClient(CIO)
val resp: Users = client.get("https://api.example.com/v1/users") {
    header("Authorization", "Bearer $token")
    parameter("limit", 10)
}
```

Anchors:

- `HttpClient(` / `io.ktor.client.*`
- `client.get(`, `client.post(`, `client.put(`, ...
- `url(`, `parameter(`, `header(`, `setBody(`

## 5. Volley (legacy)

```java
StringRequest req = new StringRequest(
    Request.Method.GET,
    "https://api.example.com/v1/ping",
    resp -> { ... },
    err -> { ... });
requestQueue.add(req);
```

Anchors: `StringRequest`, `JsonObjectRequest`, `JsonArrayRequest`,
`ImageRequest`, `RequestQueue`, `Volley.newRequestQueue`, `HurlStack`.

## 6. HttpURLConnection (plain JDK)

```java
URL url = new URL("https://api.example.com/v1/ping");
HttpURLConnection conn = (HttpURLConnection) url.openConnection();
conn.setRequestMethod("GET");
```

Anchors: `openConnection`, `setRequestMethod`, `HttpURLConnection`,
`HttpsURLConnection`, `URI.create`.

## 7. GraphQL

Apollo Android:

```kotlin
val apolloClient = ApolloClient.Builder()
    .serverUrl("https://api.example.com/graphql")
    .build()

val user = apolloClient.query(GetUserQuery(id = "1")).execute().data?.user
```

Anchors: `ApolloClient`, `.query(`, `.mutation(`, `.subscription(`,
`com.apollographql.apollo`. GraphQL operations are often in `.graphql` files
inside the APK's resources — look under `assets/` and `res/raw/`.

## 8. gRPC

```kotlin
val channel = ManagedChannelBuilder
    .forAddress("api.example.com", 443)
    .useTransportSecurity()
    .build()
val stub = UserServiceGrpcKt.UserServiceCoroutineStub(channel)
```

Anchors: `io.grpc`, `ManagedChannelBuilder`, `.forAddress(`,
`.newBlockingStub`, `.newStub`, generated `*Grpc` / `*GrpcKt` classes.

`.proto` files are usually not shipped with the APK — reconstruct from the
generated Java/Kotlin stubs.

## 9. WebSocket

```kotlin
val ws = client.newWebSocket(
    Request.Builder().url("wss://api.example.com/ws").build(),
    listener)
```

Anchors: `newWebSocket`, `WebSocket`, `wss?://`.

## 10. Hardcoded URLs

Scan all string literals: `"https?://...`. Useful even when the HTTP stack is
one we didn't cover explicitly — the URL will still show up as a string
constant. False positives: resource schemas
(`http://schemas.android.com/apk/res/android`), logback codes
(`http://logback.qos.ch/...`), XML sax features. Filter these out.

## 11. Auth / secret hints

Grep (case-insensitive) for:

- `api[_-]?key`, `auth[_-]?token`, `bearer`, `authorization`, `x-api-key`,
  `client[_-]?secret`, `access[_-]?token`, `refresh[_-]?token`, `id[_-]?token`
- Base URL constants: `BASE_URL`, `API_URL`, `SERVER_URL`, `ENDPOINT`, `API_BASE`,
  `HOST_NAME`, `GATEWAY`, `CDN_URL`
- Provider-specific: `AIza...` (Google API key), `sk_live_...` (Stripe),
  `AKIA...` / `ASIA...` (AWS), `xoxb-...` (Slack), `ghp_...` (GitHub PAT)

`apkleaks` and `trufflehog` do most of this for you in the `extract-secrets.sh`
pipeline; regex is the fallback for patterns they miss.

## Obfuscation (ProGuard / R8 / DexGuard) workarounds

- Retrofit annotations and Gson/Moshi field names are **usually preserved**
  via `-keep` rules (the app wouldn't work otherwise).
- Obfuscated class names don't help you — anchor on the annotations, not the
  class.
- Strings obfuscated via StringFog / Paranoid / DexGuard appear as opaque
  `byte[]` decryption calls in decompiled code. Use the Frida
  `dump-strings.js` template to intercept them at runtime, or statically
  simulate the decryptor with `androguard` (harder).
