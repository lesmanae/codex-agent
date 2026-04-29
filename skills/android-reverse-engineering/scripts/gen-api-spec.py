#!/usr/bin/env python3
"""
gen-api-spec.py — extract HTTP API surface from a decompiled Android APK and
emit reproducible artifacts:
  - OpenAPI 3.0 spec (YAML)
  - Postman v2.1 collection (JSON)
  - Bruno collection (tree of .bru files)
  - api-report.md (human-readable summary)

Two modes:
  --mode inventory --apk <apk>           androguard-based metadata dump
  --mode api       --src  <sources-dir>  scan decompiled Java/Kotlin for Retrofit
                                         endpoints + hardcoded URLs + auth headers

For "api" mode we prefer an AST-lite approach: regex across Java/Kotlin files is
accurate enough for Retrofit and much faster than loading a full Java parser.

Usage examples:
  gen-api-spec.py --mode inventory --apk app.apk --out ./out/
  gen-api-spec.py --mode api --src ./decompiled/sources --out ./out/ \
                  --app-name MyApp
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


def _reexec_with_androguard_python() -> None:
    """If androguard can't be imported here, re-exec under a python that has it.
    We look in common pipx/venv locations. This keeps the script single-file
    while still supporting the isolated pipx install used on Devin VMs.
    """
    try:
        import androguard  # noqa: F401
        return
    except ImportError:
        pass

    candidates = [
        Path.home() / ".local/pipx/venvs/androguard/bin/python",
        Path("/opt/devin-andre-venv/bin/python"),
        Path("/usr/local/share/androguard-venv/bin/python"),
    ]
    env_py = os.environ.get("ANDROGUARD_PYTHON")
    if env_py:
        candidates.insert(0, Path(env_py))

    for cand in candidates:
        if cand.exists() and cand.resolve() != Path(sys.executable).resolve():
            os.execv(str(cand), [str(cand), os.path.abspath(__file__), *sys.argv[1:]])
    # fall through — caller will emit the friendly error

# ---------------------------------------------------------------------------
# Inventory mode — androguard
# ---------------------------------------------------------------------------

def inventory_apk(apk_path: Path, out_dir: Path) -> None:
    _reexec_with_androguard_python()
    try:
        from androguard.misc import AnalyzeAPK  # type: ignore
    except ImportError:
        print("androguard is required for --mode inventory; install with 'pipx install androguard'", file=sys.stderr)
        sys.exit(2)

    a, dvms, _ = AnalyzeAPK(str(apk_path))
    data = {
        "package":         a.get_package(),
        "app_name":        a.get_app_name(),
        "version_code":    a.get_androidversion_code(),
        "version_name":    a.get_androidversion_name(),
        "min_sdk":         a.get_min_sdk_version(),
        "target_sdk":      a.get_target_sdk_version(),
        "main_activity":   a.get_main_activity(),
        "permissions":     sorted(a.get_permissions()),
        "activities":      sorted(a.get_activities()),
        "services":        sorted(a.get_services()),
        "receivers":       sorted(a.get_receivers()),
        "providers":       sorted(a.get_providers()),
        "libraries":       sorted(a.get_libraries()),
        "signature_names": [str(n) for n in a.get_signature_names()],
        "features":        sorted(a.get_features()),
    }
    # Collect URL strings from DEX string tables.
    urls: set[str] = set()
    try:
        dvm_list = dvms if isinstance(dvms, list) else [dvms]
        for d in dvm_list:
            for s in d.get_strings():
                s = str(s)
                if re.match(r"^https?://", s) and len(s) < 512:
                    urls.add(s)
    except Exception as e:
        data["url_strings_error"] = str(e)
    data["url_strings"] = sorted(urls)[:500]

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "inventory.json"
    out_path.write_text(json.dumps(data, indent=2))
    print(f"[gen-api-spec] inventory written to {out_path}")


# ---------------------------------------------------------------------------
# API mode — Retrofit endpoint extraction
# ---------------------------------------------------------------------------

HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}

# Retrofit method annotation: @GET("path"), @POST("path"), ...
# Also: @HTTP(method="GET", path="x", hasBody=false)
RE_METHOD_ANN = re.compile(
    r'@(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s*\(\s*"([^"]*)"\s*\)'
)
RE_HTTP_ANN = re.compile(
    r'@HTTP\s*\(\s*method\s*=\s*"(\w+)"\s*,\s*path\s*=\s*"([^"]*)"'
)

# Parameter annotations on the same method signature.
RE_HEADERS_ANN = re.compile(r'@Headers\s*\(\s*\{?\s*((?:"[^"]+"\s*,?\s*)+)\}?\s*\)')
RE_PARAM_ANN = re.compile(
    r'@(Query|QueryMap|QueryName|Path|Body|Field|FieldMap|Part|PartMap|Header|HeaderMap|Url)\s*'
    r'(?:\(\s*"([^"]*)"\s*\))?'
    r'\s+(?:final\s+)?([\w<>\[\],\?\.\s]+?)\s+(\w+)\s*[,)]'
)

# Retrofit interface method signature — we scan each line with a @METHOD annotation
# and peek at the following lines to grab the signature until ';' or '{'.
RE_METHOD_SIG_LINE = re.compile(r'^\s*(?:public\s+|abstract\s+|default\s+)?\S.*\b(\w+)\s*\(')

# Base URL construction.
RE_BASE_URL = re.compile(
    r'(?:\.baseUrl\s*\(\s*|BASE_URL\s*=\s*|base_url\s*=\s*)"([^"]+)"'
)

# Hardcoded URLs in string literals.
RE_HARDCODED_URL = re.compile(r'"(https?://[^"\s]+)"')


@dataclass
class Endpoint:
    method: str
    path: str
    file: str
    line: int
    func_name: str = ""
    headers: list[str] = field(default_factory=list)
    params: list[dict] = field(default_factory=list)  # {kind, name, type, var}


def scan_sources(src_root: Path) -> tuple[list[Endpoint], set[str], dict[str, list[tuple[str, int]]]]:
    endpoints: list[Endpoint] = []
    base_urls: set[str] = set()
    hardcoded: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for path in src_root.rglob("*"):
        if not path.is_file() or path.suffix not in (".java", ".kt"):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue

        for i, line in enumerate(lines):
            m = RE_METHOD_ANN.search(line)
            http_m = RE_HTTP_ANN.search(line) if not m else None

            if m:
                method, epath = m.group(1), m.group(2)
            elif http_m:
                method, epath = http_m.group(1), http_m.group(2)
            else:
                # base URL + hardcoded URL sweep
                for bm in RE_BASE_URL.finditer(line):
                    base_urls.add(bm.group(1))
                for hm in RE_HARDCODED_URL.finditer(line):
                    url = hm.group(1)
                    # Skip schema-ish / resource URIs / extremely long junk
                    if len(url) < 512 and "://" in url:
                        hardcoded[url].append((str(path), i + 1))
                continue

            # Gather signature + params: scan forward until we hit ')' at end or ';' or '{'
            window_start = max(0, i - 6)  # @Headers may be 1-2 lines above
            window_end   = min(len(lines), i + 8)
            window = "\n".join(lines[window_start:window_end])

            func_name = ""
            # Look forward from current line for method name "returnType name("
            for j in range(i, window_end):
                m2 = re.search(r'\b(\w+)\s*\(', lines[j])
                if m2 and not lines[j].lstrip().startswith("@") and "(" in lines[j]:
                    func_name = m2.group(1)
                    if func_name not in HTTP_METHODS and func_name not in ("HTTP", "Headers"):
                        break

            headers: list[str] = []
            hm2 = RE_HEADERS_ANN.search(window)
            if hm2:
                headers = re.findall(r'"([^"]+)"', hm2.group(1))

            params = []
            for pm in RE_PARAM_ANN.finditer(window):
                kind, name, type_, var = pm.group(1), pm.group(2), pm.group(3).strip(), pm.group(4)
                params.append({"kind": kind, "name": name or var, "type": type_, "var": var})

            endpoints.append(Endpoint(
                method=method, path=epath, file=str(path), line=i + 1,
                func_name=func_name, headers=headers, params=params,
            ))

    return endpoints, base_urls, hardcoded


# ---------------------------------------------------------------------------
# Emit artifacts
# ---------------------------------------------------------------------------

def pick_base_url(base_urls: Iterable[str], hardcoded: dict[str, list]) -> str:
    candidates = list(base_urls)
    if not candidates:
        # Fall back to most common host root among hardcoded URLs
        from collections import Counter
        roots = Counter()
        for u in hardcoded:
            mm = re.match(r"(https?://[^/]+)", u)
            if mm:
                roots[mm.group(1)] += 1
        if roots:
            return roots.most_common(1)[0][0] + "/"
        return "https://api.example.com/"
    # Prefer HTTPS
    https = [c for c in candidates if c.startswith("https://")]
    return (https or candidates)[0]


def retrofit_path_to_openapi(path: str) -> str:
    # Retrofit @Path("x") with "/users/{x}" already matches OpenAPI {x}.
    # Ensure leading slash.
    if not path.startswith(("/", "http")):
        path = "/" + path
    return path


def emit_openapi(endpoints: list[Endpoint], base_url: str, app_name: str) -> dict:
    paths: dict[str, dict] = defaultdict(dict)
    for ep in endpoints:
        p = retrofit_path_to_openapi(ep.path)
        # Strip query strings from path for OpenAPI (queries become parameters)
        path_only = p.split("?", 1)[0]
        op_params = []
        for param in ep.params:
            kind = param["kind"]
            in_ = None
            if kind == "Query" or kind == "QueryName" or kind == "QueryMap":
                in_ = "query"
            elif kind == "Path":
                in_ = "path"
            elif kind == "Header" or kind == "HeaderMap":
                in_ = "header"
            if in_:
                op_params.append({
                    "name": param["name"],
                    "in": in_,
                    "required": in_ == "path",
                    "schema": {"type": "string"},
                })
        body = None
        for param in ep.params:
            if param["kind"] in ("Body", "Field", "FieldMap", "Part", "PartMap"):
                body = {
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
                break
        op: dict = {
            "operationId": ep.func_name or f"{ep.method.lower()}_{path_only.strip('/').replace('/', '_').replace('{','').replace('}','')}",
            "summary": f"{ep.method} {path_only}",
            "description": f"Source: {ep.file}:{ep.line}",
            "responses": {"200": {"description": "OK"}},
        }
        if op_params:
            op["parameters"] = op_params
        if body:
            op["requestBody"] = body
        if ep.headers:
            op.setdefault("parameters", []).extend([
                {"name": h.split(":", 1)[0].strip(), "in": "header",
                 "required": False, "schema": {"type": "string"},
                 "example": h.split(":", 1)[1].strip() if ":" in h else ""}
                for h in ep.headers
            ])

        paths[path_only][ep.method.lower()] = op

    return {
        "openapi": "3.0.3",
        "info": {
            "title": f"{app_name} — extracted API",
            "version": "0.1.0",
            "description": "Auto-generated from decompiled Retrofit interfaces. "
                           "Endpoints and shapes are best-effort; verify before use.",
        },
        "servers": [{"url": base_url.rstrip("/")}],
        "paths": dict(paths),
    }


def emit_postman(endpoints: list[Endpoint], base_url: str, app_name: str) -> dict:
    items = []
    for ep in endpoints:
        path = retrofit_path_to_openapi(ep.path).split("?", 1)[0]
        url_parts = [base_url.rstrip("/") + path]
        query = [{"key": p["name"], "value": ""} for p in ep.params if p["kind"] in ("Query", "QueryMap", "QueryName")]
        headers = []
        for h in ep.headers:
            if ":" in h:
                k, v = h.split(":", 1)
                headers.append({"key": k.strip(), "value": v.strip()})
        body = None
        if any(p["kind"] in ("Body", "Field", "FieldMap") for p in ep.params):
            body = {"mode": "raw", "raw": "{}", "options": {"raw": {"language": "json"}}}

        items.append({
            "name": ep.func_name or f"{ep.method} {path}",
            "request": {
                "method": ep.method,
                "header": headers,
                "url": {
                    "raw": "".join(url_parts) + (("?" + "&".join(f"{q['key']}=" for q in query)) if query else ""),
                    "host": [base_url.rstrip("/")],
                    "path": [seg for seg in path.split("/") if seg],
                    "query": query,
                },
                **({"body": body} if body else {}),
                "description": f"Source: {ep.file}:{ep.line}",
            },
            "response": [],
        })
    return {
        "info": {
            "_postman_id": "generated",
            "name": f"{app_name} — extracted API",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
    }


def emit_bruno(endpoints: list[Endpoint], base_url: str, out_dir: Path, app_name: str) -> None:
    bruno_dir = out_dir / "api.bruno"
    bruno_dir.mkdir(parents=True, exist_ok=True)
    (bruno_dir / "bruno.json").write_text(json.dumps({
        "version": "1",
        "name": f"{app_name} — extracted API",
        "type": "collection",
    }, indent=2))
    for idx, ep in enumerate(endpoints, start=1):
        path = retrofit_path_to_openapi(ep.path)
        name = ep.func_name or f"{ep.method}_{idx}"
        safe = re.sub(r"[^\w\-]+", "_", name)[:60]
        body = "{}" if any(p["kind"] in ("Body", "Field", "FieldMap") for p in ep.params) else ""
        lines = [
            f"meta {{",
            f"  name: {name}",
            f"  type: http",
            f"  seq: {idx}",
            f"}}",
            "",
            f"{ep.method.lower()} {{",
            f"  url: {base_url.rstrip('/')}{path}",
            f"  body: {'json' if body else 'none'}",
            f"}}",
            "",
        ]
        if ep.headers:
            lines.append("headers {")
            for h in ep.headers:
                if ":" in h:
                    k, v = h.split(":", 1)
                    lines.append(f"  {k.strip()}: {v.strip()}")
            lines.append("}")
            lines.append("")
        if body:
            lines.append("body:json {")
            lines.append(f"  {body}")
            lines.append("}")
            lines.append("")
        (bruno_dir / f"{safe}.bru").write_text("\n".join(lines))


def emit_markdown_report(endpoints, base_urls, hardcoded, out_path: Path, app_name: str) -> None:
    lines = [f"# API Report — {app_name}", "", f"Base URL candidates: {sorted(base_urls)}", ""]
    by_method: dict[str, list[Endpoint]] = defaultdict(list)
    for ep in endpoints:
        by_method[ep.method].append(ep)
    lines.append(f"**Total Retrofit endpoints: {len(endpoints)}**")
    for method in sorted(by_method):
        lines.append(f"\n## {method}\n")
        lines.append("| Path | Function | File:Line | Params | Headers |")
        lines.append("|---|---|---|---|---|")
        for ep in sorted(by_method[method], key=lambda e: e.path):
            params_s = ", ".join(f"{p['kind']}({p['name']})" for p in ep.params) or "-"
            headers_s = "; ".join(ep.headers) or "-"
            lines.append(f"| `{ep.path}` | `{ep.func_name}` | `{ep.file}:{ep.line}` | {params_s} | {headers_s} |")

    lines.append("\n## Hardcoded URLs\n")
    for url, hits in sorted(hardcoded.items()):
        lines.append(f"- `{url}` ({len(hits)} occurrences; first: `{hits[0][0]}:{hits[0][1]}`)")
    out_path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# YAML dump without a yaml dependency
# ---------------------------------------------------------------------------

def to_yaml(obj, indent: int = 0) -> str:
    sp = "  " * indent
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        out = []
        for k, v in obj.items():
            k_s = str(k)
            if isinstance(v, (dict, list)) and v:
                out.append(f"{sp}{k_s}:")
                out.append(to_yaml(v, indent + 1))
            else:
                out.append(f"{sp}{k_s}: {to_yaml(v, indent + 1).lstrip()}")
        return "\n".join(out)
    if isinstance(obj, list):
        if not obj:
            return "[]"
        out = []
        for item in obj:
            if isinstance(item, (dict, list)):
                out.append(f"{sp}-")
                out.append(to_yaml(item, indent + 1))
            else:
                out.append(f"{sp}- {to_yaml(item, indent + 1).lstrip()}")
        return "\n".join(out)
    if isinstance(obj, str):
        if re.search(r'[:\n#\-\?\&\*\|\>\!\%\@\`]|^\s|\s$', obj) or obj == "":
            return '"' + obj.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return obj
    if obj is True:  return "true"
    if obj is False: return "false"
    if obj is None:  return "null"
    return str(obj)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("inventory", "api"), required=True)
    ap.add_argument("--apk", type=Path)
    ap.add_argument("--src", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--app-name", default="App")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    if args.mode == "inventory":
        if not args.apk or not args.apk.exists():
            print("Error: --apk is required for --mode inventory", file=sys.stderr)
            return 2
        inventory_apk(args.apk, args.out)
        return 0

    # api mode
    if not args.src or not args.src.is_dir():
        print("Error: --src must be an existing directory for --mode api", file=sys.stderr)
        return 2

    endpoints, base_urls, hardcoded = scan_sources(args.src)
    base_url = pick_base_url(base_urls, hardcoded)

    openapi = emit_openapi(endpoints, base_url, args.app_name)
    (args.out / "api.openapi.yaml").write_text(to_yaml(openapi))
    (args.out / "api.openapi.json").write_text(json.dumps(openapi, indent=2))

    postman = emit_postman(endpoints, base_url, args.app_name)
    (args.out / "api.postman.json").write_text(json.dumps(postman, indent=2))

    emit_bruno(endpoints, base_url, args.out, args.app_name)
    emit_markdown_report(endpoints, base_urls, hardcoded, args.out / "api-report.md", args.app_name)

    print(f"[gen-api-spec] wrote {len(endpoints)} endpoints / {len(hardcoded)} hardcoded URLs")
    print(f"  - {args.out}/api.openapi.yaml")
    print(f"  - {args.out}/api.postman.json")
    print(f"  - {args.out}/api.bruno/")
    print(f"  - {args.out}/api-report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
