# Contributing to Codex Agent

## Dev setup

```bash
git clone https://github.com/lesmanae/codex-agent.git
cd codex-agent
cp .env.example .env
$EDITOR .env

# With Docker (matches production)
docker compose up -d --build
docker compose logs -f api

# Or bare-metal, for fast iteration on api.py only:
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

Note: bare-metal mode skips `nsenter`, so Codex runs in your local
namespace. Fine for testing the API surface; you'll need full Docker
to test the actual agent loop.

## Code style

- Python 3.12+, fully typed.
- `ruff` + `black` (run via `pre-commit` once we add it).
- Avoid `Any` / `getattr` / `setattr` — if you need them, you don't yet
  understand the type. Read the code, find the actual type, then access
  the attribute the normal way.
- Logging: use `logger = logging.getLogger(__name__)` at module top.
- Lines: 100 char soft, 120 hard.

## Branching + PRs

- Branch off `main`, name it `feat/<topic>` / `fix/<topic>` /
  `chore/<topic>` (or `devin/<unix_ts>-<slug>` if you're an LLM agent).
- Commits: [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat(scope):`, `fix:`, `refactor:`, `docs:`, …).
- Open a PR to `main`, squash-merge.
- **Never push to `main` directly.**
- **Don't amend commits** — add a new commit instead.

## PEP 563 + Pydantic trap (READ THIS BEFORE EDITING `api.py`)

`app/api.py` starts with:

```python
from __future__ import annotations
```

This makes ALL type annotations evaluated lazily — they're stored as
strings. FastAPI introspects them via `typing.get_type_hints(func, …)`,
which can only resolve names in the **module global namespace**.

A Pydantic `BaseModel` defined inside a function body is **not** in the
module globals, so FastAPI silently falls back to "this is a query
string parameter" and you'll see HTTP **422** with `loc: ('query',
'body')` even though your JSON body is perfectly valid.

❌ **Don't:**

```python
def _register_routes(app):
    class Foo(BaseModel):              # locally scoped — FastAPI can't resolve this
        bar: str

    @app.post("/api/foo")
    async def foo(body: Foo):
        return body.bar
```

✅ **Do** either of:

1. Use `body: dict` and parse manually:

   ```python
   @app.post("/api/foo")
   async def foo(body: dict):
       bar = body.get("bar")
       if not isinstance(bar, str):
           raise HTTPException(400, "bar required (string)")
       return {"echo": bar}
   ```

2. Define the BaseModel at module level, outside any function:

   ```python
   class FooIn(BaseModel):
       bar: str

   def _register_routes(app):
       @app.post("/api/foo")
       async def foo(body: FooIn):
           return body.bar
   ```

We've been bitten by this twice already (`/api/ask-user/start` and
`/api/ask-user/answer`). Please don't bring it back.

## Other gotchas

- **Don't strip `git` from the Dockerfile.** Workspace endpoints rely on
  it. `_run_git()` catches `FileNotFoundError` defensively but the
  workspace UI degrades to "not a repo" if the binary is missing.
- **Codex `image_gen` quirk** — Codex CLI emits no JSONL event for
  `image_gen` (the built-in image tool). The runner watches the
  `generated_images/` directory before/after each turn and synthesizes
  a `tool_call` event. Don't remove the watcher in `codex_runner.py`.
- **Skill loading.** The system prompt only injects an INDEX of the 240
  skills (~62 KB); bodies are fetched on demand via
  `cat /app/skills/<name>/SKILL.md`. Don't try to inline the full bodies
  — the context window can't hold them all.

## Release checklist

1. Bump `app = FastAPI(version="X.Y.Z")` in `api.py`.
2. Update `CHANGELOG.md` (TODO — not yet committed).
3. Tag: `git tag vX.Y.Z && git push --tags`.
4. On the VPS: `cd /opt/codex-agent && git pull && docker compose up -d --build`.
5. Verify: `curl https://your-host/api/health`.
6. Update mobile app if any breaking event-schema change (most aren't).

## Filing bugs / feature requests

Open a GitHub Issue. Include:

- Backend version (`/api/health`).
- Logs (`docker compose logs api --tail=200`).
- Repro steps + expected vs actual.

## License

By contributing you agree that your contributions are licensed under
the [MIT License](LICENSE).
