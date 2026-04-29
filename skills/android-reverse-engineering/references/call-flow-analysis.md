# Call-Flow Analysis

Once you have a decompiled app, the question is: *how does data get from the
user tapping a button to an HTTP request hitting the server?* This page
describes the anchor points and an efficient order to read the code.

## Step 0. Identify the architecture

Before reading any business logic, figure out how the app is wired:

| Pattern | Tell | Typical data flow |
| --- | --- | --- |
| **MVVM** (modern, ~80% of new apps) | `ViewModel` classes, `LiveData` / `StateFlow` / `Flow` | Activity → ViewModel → UseCase → Repository → ApiService |
| **Clean Architecture** | packages named `domain`, `data`, `presentation`; interactors or use cases | Same as MVVM, but with explicit interactor layer |
| **MVP** | `Presenter` classes, explicit `View` interfaces | Activity (View) → Presenter → Repository → ApiService |
| **Flutter** | `io.flutter.embedding.*`, `FlutterEngine`, single main activity with a platform-channel | Dart code lives in `assets/flutter_assets/` as compiled Dart bytecode; Android side is mostly plumbing |
| **React Native** | `com.facebook.react.*`, `ReactActivity` | JS bundle in `assets/index.android.bundle`; Java side is native modules |
| **KMP / Compose Multiplatform** | `androidx.compose.*` + shared Kotlin module | Same as MVVM but shared logic is in a `shared`/`common` module |

## Step 1. Find entry points from the manifest

```bash
apktool d app.apk -o smali-out
grep -E 'android.intent.action.MAIN' -A5 smali-out/AndroidManifest.xml
```

The `<activity>` with `android.intent.action.MAIN` + `android.intent.category.LAUNCHER`
is the launcher. For most apps, that's a `MainActivity` or a splash screen.

Also note:

- `<application android:name="...">` — the `Application` subclass, where DI
  containers are initialized.
- Activities with deeplink intent filters — potential attacker-controlled
  entry points, worth prioritizing for security audit.

## Step 2. Read the `Application.onCreate()`

This is where:

- Dagger/Hilt components are built
- OkHttp clients / Retrofit instances are constructed — **base URL lives here**
- Analytics / crash SDKs are initialized
- Feature-flag providers start

For a Retrofit app, there's usually a module that looks like:

```kotlin
@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {
    @Provides
    fun okHttpClient(): OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(AuthInterceptor())
        .build()

    @Provides
    fun retrofit(ok: OkHttpClient): Retrofit = Retrofit.Builder()
        .baseUrl(BuildConfig.BASE_URL)
        .addConverterFactory(MoshiConverterFactory.create())
        .client(ok)
        .build()

    @Provides
    fun userApi(r: Retrofit): UserApi = r.create(UserApi::class.java)
}
```

**`BuildConfig.BASE_URL`** is often just a constant compiled into a
`BuildConfig.class` — check `sources/com/example/app/BuildConfig.java`.

## Step 3. Trace a user action

Pick an activity (e.g., `LoginActivity`). Walk:

1. `onCreate()` → find click listeners: `setOnClickListener {`, `@OnClick`,
   `findViewById(R.id.login).setOnClickListener(...)`, or Compose
   `onClick = { ... }`.
2. Click handler → call site. In MVVM this is usually
   `viewModel.login(username, password)`.
3. `ViewModel.login()` → usually calls a repository:
   `userRepository.login(...)`.
4. `UserRepository.login()` → calls the API service:
   `api.login(LoginRequest(...))`.
5. The API service is an interface with Retrofit annotations — that's where
   the request is defined.

## Step 4. Map DI bindings

If the app uses Hilt or Dagger, don't chase instantiations directly — they're
generated. Instead:

- Find `@Module` classes to understand which implementation is provided for
  each interface.
- Find `@Binds` methods for 1:1 interface→impl mappings.
- `@Inject` constructors tell you that the class is the one actually
  constructed by Dagger.

For Koin (lightweight DI often used in Kotlin-first codebases):

```kotlin
val networkModule = module {
    single { Retrofit.Builder().baseUrl(BASE_URL).build() }
    single { get<Retrofit>().create(UserApi::class.java) }
}
```

## Step 5. Work around obfuscation

When class names are `a.b.c`, `aa`, `bb`:

- **String constants are never obfuscated**. Search for the URL, the
  endpoint path, or a known header name to find the method that uses it.
- **Library APIs are never obfuscated**. `okhttp3.Request.Builder.url()`
  stays named — grep for it and walk outward.
- **Retrofit + Gson field names** are preserved (the app would crash
  otherwise). You can read model shape from them.
- **Enums** often keep their `name()` strings used in `valueOf()`. Those
  strings leak meaning even when the enum itself is renamed.
- **`toString()` methods** — data classes compile to a `toString()` that
  concatenates `"ClassName(field1=" + a + ", field2=" + b + ")"`. Read those
  strings to recover class and field names.

## Step 6. Build a call graph (optional)

`androguard` can produce a call graph:

```python
from androguard.misc import AnalyzeAPK
a, d, dx = AnalyzeAPK("app.apk")
cg = dx.get_call_graph()
# Export to GraphML / PNG for visualisation:
import networkx as nx
nx.write_graphml(cg, "cg.graphml")
```

Import `cg.graphml` into [Cytoscape](https://cytoscape.org/) or yEd, then
filter to show only paths that lead to `Lokhttp3/Call;->execute`. This lets
you see every call-chain that ends in an HTTP request.

## Common pitfalls

- **Reactive chains** (`Flow`, `Observable`, `Single`): the call isn't made
  until someone subscribes. Find `.collect { ... }`, `.subscribe { ... }`, or
  coroutine `launch { ... }`.
- **Lazy initialization**: Kotlin `by lazy { ... }` delays the actual
  construction. The `lazy` block is in a `Function0` synthetic class.
- **Delegated properties**: `by viewModels()`, `by viewBindings()` — these
  resolve via extension functions in the KTX libraries. Look there, not at
  the property declaration.
- **`@Composable` functions**: Compose rebuilds UI on state changes. Click
  handlers are trailing lambdas on `Button(onClick = { ... })`. No separate
  listener classes to chase.
