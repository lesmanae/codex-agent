# Vineflower — quick reference

Vineflower is a maintained fork of Fernflower. It generally produces cleaner
output than jadx for:

- Lambdas / `var` locals / stream pipelines
- Generics with complex type parameters
- Switch-on-string and switch-on-enum
- `try`-with-resources blocks
- Kotlin coroutines lowered to Java

## CLI

Our wrapper runs it as `java -jar /opt/vineflower.jar INPUT OUTPUT_DIR`.
Direct invocation:

```bash
java -jar /opt/vineflower.jar library.jar ./vine-out/
java -jar /opt/vineflower.jar -den=true -rsy=true library.jar ./vine-out/
```

Useful flags (from Vineflower's help):

| Flag | Meaning |
| --- | --- |
| `-den=1` | Decode enum switches |
| `-rsy=1` | Remove synthetic methods |
| `-dgs=1` | Decompile generic signatures |
| `-rbr=1` | Remove bridge methods |
| `-ner=1` | Not-null assertions as inline checks |
| `-nls=1` | Newline after `{` |

Default options in `decompile.sh` keep Vineflower's built-in defaults, which
are sensible for most APKs.

## Handling APK input

Vineflower does not read `.apk` directly. For APK input, `decompile.sh`:

1. Runs `d2j-dex2jar` to convert each DEX to a JAR.
2. Feeds the resulting JAR to Vineflower.

If `d2j-dex2jar` is missing, Vineflower mode for APK input will fail — install
dex2jar (see `setup-guide.md`).

## When to pick Vineflower over jadx

- **JAR / AAR libraries** — Vineflower is the clear winner. Use it by default.
- **jadx produced broken code** → try Vineflower for the specific class. Run
  the whole APK through `decompile.sh --engine both` and diff-review.
- **Kotlin-heavy codebases with lambdas / streams** — often cleaner here.

## When jadx is the better pick

- **Need resources decoded** — Vineflower only outputs Java.
- **Very large APK** — jadx is ~2-3× faster with `--no-res`.
- **You're grepping for Retrofit annotations** — either works, but jadx's
  output paths are more predictable.
