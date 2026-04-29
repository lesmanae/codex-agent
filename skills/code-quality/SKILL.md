# Code Quality — Skill

**Trigger phrases**: refactor, code review, code quality, clean code,
follow conventions, lint, typecheck, type check, write code, implement,
edit file, modify function, add comment, code style, naming,
imports, error handling, exception handling, test, unit test, write tests.

Universal coding standards that apply to every code change, regardless of
language. Pair this with the language-specific skill (`python-dev`,
`node-dev`, etc.) for syntax-level guidance.

---

## Operating principles

### 1. Minimal, focused edits

- **Scope every change tightly.** If the operator asks for a bug fix, fix
  the bug — do not also reformat the file, rename variables, or pull in a
  new dependency.
- **No drive-by refactors.** Refactoring is its own task and gets its own
  PR.
- **Touch the fewest lines that solve the problem.** Reviewers read diffs;
  smaller diffs get reviewed faster and break less.
- **General-purpose solutions, not special cases.** Avoid hard-coding,
  one-shot conditionals, or workarounds that pass a single test but fail
  the next case. If the only way to make a test pass is a hack, flag the
  test as wrong instead.

### 2. Match the existing code

Before writing anything new in a file:

1. Read the file (or at least the surrounding 50 lines).
2. Check what package manager / framework / lint config is in use.
3. Mirror the file's style: tabs vs spaces, quote style, naming case,
   import grouping, type-hint usage, comment density.
4. Reuse helpers and utilities that already exist — don't write a new
   `slugify` if one is right there.

If the operator asks for something that contradicts the existing style,
follow the operator. Otherwise, the existing style wins.

### 3. Verify dependencies before using them

Never assume a library is available:

- Python: check `pyproject.toml` / `requirements*.txt` / `pdm.lock` /
  `poetry.lock` / `uv.lock`.
- Node: check `package.json` `dependencies` / `devDependencies`.
- Rust: check `Cargo.toml`.
- Go: check `go.mod`.

If a library is not present, either (a) add it via the project's package
manager (`uv add`, `npm install`, `cargo add`, `go get`) and update the
manifest, or (b) implement the functionality with stdlib / what's already
there. Do not write `import x` for an `x` that isn't installed.

### 4. Imports at the top

Place all imports at the top of the file. Do **not** import inside
functions or classes unless avoiding a real circular import (and then
add a comment explaining why).

### 5. Use real types, not escape hatches

- Match the file's typing convention. If it's typed, type new code too.
- No `Any`, no `getattr` / `setattr` for ordinary attribute access. If
  you feel you need them, you don't yet understand the type — go read the
  code, learn the type, access it correctly.
- No `# type: ignore` without a comment explaining why.
- Prefer narrow exceptions over `except Exception:`. Catch what you can
  handle, let the rest propagate.

### 6. Generated code must run

Every snippet you produce must be immediately executable in the target
context: imports present, dependencies declared, entry points wired,
syntax valid, indentation correct.

If you can't run it yourself, at minimum read it back end-to-end before
declaring it done.

---

## Comments

**Default is no comment.** Bias aggressively toward terseness. Most
production code should have very few comments — rely on good naming.
Match the surrounding density of the file.

When you do comment, the comment must:

- Describe the code in **general terms**, not the diff or the bug.
- Make sense to a reader who has no idea what changed recently.
- Be short (one line if possible, two-three at most).

**Never write "diff comments"** — comments whose only purpose is to
explain what you just changed:

```python
# BAD
# now we also check for None
if x is not None and x > 0:
    ...

# BAD
# fixed: was returning the wrong index
return matches[i]

# BAD
# previously this used a dict; switched to list for ordering
items = []
```

That context belongs in the PR description, not in the source. If the
comment only makes sense to someone reading the diff, delete it.

```python
# OK — describes the code, not the change
# Use a list (not a dict) so insertion order is preserved.
items: list[Item] = []
```

---

## Error handling

- Fail fast on bad inputs at the boundary (parse / validate once, work
  with structured types after).
- Never silently swallow exceptions. `except: pass` is almost always
  wrong; if you really mean it, leave a one-line comment justifying it.
- Log enough context to debug from logs alone (request ID, file path,
  parameter values — *not* secrets).
- Re-raise with `raise ... from exc` to preserve the cause when wrapping.
- Validate at the public boundary (HTTP handler, CLI parser); trust
  internal callers.

---

## Tests

- Don't modify failing tests to make them pass unless the operator
  explicitly asked you to. The test is ground truth; if it seems wrong,
  flag it.
- New behavior gets a test. Bug fix gets a regression test that fails
  before the fix and passes after.
- Match the existing test style (pytest fixtures vs unittest, jest
  describe/it vs test(), etc.). Don't introduce a new framework.
- Keep tests deterministic. Stub time, randomness, network. Use fixed
  seeds.
- One assertion per concept; multiple `assert`s in one test is fine if
  they all check the same behavior.

---

## Naming

- **Verbs for functions, nouns for data.** `compute_total()` /
  `total_price`.
- **No abbreviations** unless they're domain-standard. `req`/`res` is
  fine in a web handler. `pmt_amt_calc` is not.
- **Boolean prefixes** for booleans: `is_`, `has_`, `should_`, `can_`.
- **Avoid `data`, `info`, `temp`, `helper`** — name what it actually is.
- Match the file's case convention: snake_case for Python, camelCase
  for JS/TS, PascalCase for types/classes.

---

## Security in code

- **Never hard-code credentials.** Read from env / secret store. The
  only acceptable inline credentials are local-dev defaults like
  `password=password` in a `docker-compose.yml` for a local DB.
- **Never log secrets.** Mask tokens in logs; log structured fields
  with allowlists, not whole request bodies.
- **Validate / sanitize external input** at the boundary. Don't trust
  user-supplied paths (path traversal), shell args (command injection),
  or SQL fragments (use parameters / ORM).
- **Use cryptographically random** generators for secrets, tokens,
  IDs (`secrets.token_urlsafe`, `crypto.randomBytes`). Not `random`.
- **Constant-time compare** for HMAC / token equality
  (`hmac.compare_digest`).

---

## Final-pass checklist before commit

Run mentally before `git commit`:

- [ ] Diff contains only files relevant to the task.
- [ ] No leftover `print` / `console.log` / `dbg!` / debug loggers.
- [ ] No commented-out code blocks.
- [ ] No TODO / FIXME unless tied to a real follow-up the operator
      knows about.
- [ ] Imports tidy, no unused imports.
- [ ] No new dependency that isn't declared in the manifest.
- [ ] Lint, format, typecheck, tests pass locally.
- [ ] Comments (if any) describe the code, not the diff.
- [ ] No secrets in the diff.

If any item fails, fix it before committing.
