# Androguard — programmatic queries

Androguard is a Python library for static Android analysis. We use it in
`gen-api-spec.py --mode inventory` to extract structured metadata directly
from the APK without relying on grep.

## Running

`gen-api-spec.py` auto-reexecs under the pipx venv python that has androguard.
To use the library interactively:

```bash
~/.local/pipx/venvs/androguard/bin/python -i -c '
from androguard.misc import AnalyzeAPK
a, d, dx = AnalyzeAPK("app.apk")
'
```

- `a`  — `APK` instance (manifest + resources + certificates)
- `d`  — list of `DEX` format objects (one per DEX in the APK)
- `dx` — `Analysis` object (cross-references, call graphs)

## Recipes

### 1. All activities + their exported status

```python
for act in a.get_activities():
    exported = a.get_element("activity", "exported", name=act)
    perm     = a.get_element("activity", "permission", name=act)
    print(f"{act}  exported={exported}  permission={perm}")
```

### 2. Deeplink schemes

```python
import xml.etree.ElementTree as ET
NS = "{http://schemas.android.com/apk/res/android}"
tree = ET.fromstring(a.get_android_manifest_axml().get_xml())
for intent in tree.iter("intent-filter"):
    for data in intent.iter("data"):
        scheme = data.get(f"{NS}scheme")
        host   = data.get(f"{NS}host")
        if scheme:
            print(f"deeplink: {scheme}://{host or '*'}")
```

### 3. Strings that look like URLs

```python
import re
urls = set()
for dex in d if isinstance(d, list) else [d]:
    for s in dex.get_strings():
        s = str(s)
        if re.match(r"https?://", s) and len(s) < 512:
            urls.add(s)
print(sorted(urls))
```

### 4. Methods that call `Cipher.doFinal`

```python
from androguard.decompiler.decompiler import DecompilerJADX  # optional
target = "Ljavax/crypto/Cipher;->doFinal([B)[B"
for m in dx.find_methods(classname="", methodname="", descriptor=""):
    for _, call, _ in m.get_xref_to():
        if call.get_method().get_name() == "doFinal":
            print(m.get_method().get_class_name(), "->", m.get_method().get_name())
```

### 5. Call graph to a specific sink

Use `dx.get_call_graph()` and walk with `networkx`:

```python
import networkx as nx
cg = dx.get_call_graph()
sink = next(n for n in cg.nodes if "doFinal" in str(n))
sources = [n for n in nx.ancestors(cg, sink) if "onCreate" in str(n)]
```

### 6. Check for Dagger/Hilt bindings

Dagger modules are marked with `@Module` and `@Provides`:

```python
for cls in dx.get_classes():
    if any("Module" in a.get_name() for a in cls.get_annotations() or []):
        print(cls.get_vm_class().get_name())
```

## Why prefer androguard over grep

- **Permissions + components**: exact, parsed from the binary manifest — grep
  misses attribute parsing edge cases (e.g. `android:exported` implied by
  `<intent-filter>` presence on API ≤ 30).
- **Call graphs**: grep can't tell you who calls `Cipher.doFinal`.
- **Resource strings**: grep on decompiled Java misses strings stored in
  `resources.arsc` and accessed via `getString(R.string.X)`.
- **Obfuscated names**: androguard gives you cross-references by descriptor,
  not by mangled class name.
