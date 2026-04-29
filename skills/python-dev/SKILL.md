# Python Development — Skill

**Trigger phrases**: python, pip, uv, poetry, pdm, hatch, virtualenv, venv,
pyproject.toml, requirements.txt, pytest, unittest, ruff, black, isort, flake8,
mypy, pyright, type hints, dataclass, asyncio, async/await, fastapi, flask,
django, sqlalchemy, alembic, celery, gunicorn, uvicorn, httpx, requests, pandas,
numpy, jupyter, ipython, pip install, install package, import error,
ModuleNotFoundError, pyenv, conda, miniconda, build wheel, publish pypi.

Use this skill when the user asks to write, run, debug, lint, type-check,
test, package, or deploy Python code. Audience: dev working on the VPS.

---

## Operating principles

- **Detect the project's package manager from files present.** Order:
  `uv.lock` → uv. `poetry.lock` → poetry. `pdm.lock` → pdm. `Pipfile.lock`
  → pipenv. `requirements*.txt` only → pip + venv. Match what's there;
  don't introduce a new tool unprompted.
- **Always use a virtualenv, never the system Python.**
- **Pin versions on add.** Don't run `pip install foo` then forget to
  update `pyproject.toml`/`requirements.txt`.
- **Run lint + tests before declaring done.** ruff (or flake8) for lint,
  mypy/pyright for types, pytest for tests.
- **Imports at top of file.** No nested-function imports unless avoiding
  a circular dep.
- **Type hints by default.** Match the existing codebase; if it's typed,
  type new code too.

---

## Quick env setup (no project manager)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt
```

## With uv (fastest, recommended for new projects)

```bash
which uv || pip install uv
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
# or, in a uv project:
uv sync
uv run pytest
```

## With poetry

```bash
poetry install
poetry run pytest
poetry add <pkg>          # adds to pyproject + locks
poetry add --group dev <pkg>
```

---

## Lint & type-check & test

```bash
ruff check .              # or: ruff check --fix
ruff format .             # if ruff format is configured
mypy .                    # or: pyright
pytest -q                 # quick run
pytest -xvs path/to/test  # verbose, fail-fast, no capture
pytest --lf               # rerun only last failed
```

## Common debugging

```bash
python -c "import sys; print(sys.executable, sys.version)"
python -X dev script.py            # warnings → errors
python -m trace --trace script.py  # line-by-line trace
PYTHONFAULTHANDLER=1 python ...    # crash stack trace
```

For interactive debugging: `python -m pdb script.py`, then `b file:line`,
`c`, `n`, `s`, `p var`, `pp obj`. Or `breakpoint()` in code (3.7+).

---

## FastAPI app skeleton

```bash
uv init my-api && cd my-api
uv add fastapi uvicorn[standard] httpx
mkdir -p app
cat > app/main.py <<'PY'
from fastapi import FastAPI
app = FastAPI()
@app.get("/")
def root() -> dict:
    return {"ok": True}
PY
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Pytest skeleton

```python
# tests/test_foo.py
import pytest
from mymod import foo

def test_happy() -> None:
    assert foo(2) == 4

@pytest.mark.parametrize("x,want", [(0,0),(1,1),(2,4)])
def test_param(x: int, want: int) -> None:
    assert foo(x) == want
```

## Build + publish a wheel

```bash
python -m build              # produces dist/*.whl + *.tar.gz
twine upload dist/*          # to PyPI (env: TWINE_USERNAME=__token__, TWINE_PASSWORD=pypi-...)
```

---

## Common pitfalls

- `pip install` outside a venv → installs to system Python (Debian/Ubuntu
  blocks this with PEP 668 — respect the warning).
- Forgot to `source .venv/bin/activate` → wrong python; check
  `which python` after activation.
- `ModuleNotFoundError` in pytest but `python -c "import x"` works → run
  pytest from project root, ensure `pyproject.toml` has `tool.pytest.ini_options.pythonpath = ["."]`
  or use `pytest --import-mode=importlib`.
- Async tests need `pytest-asyncio` (or `anyio`) and `asyncio_mode = "auto"`
  in config.
- Pandas/NumPy on Alpine → use Debian-based image instead; pre-built wheels.
