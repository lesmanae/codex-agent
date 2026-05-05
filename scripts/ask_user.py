#!/usr/bin/env python3
"""codex-ask-user — back-channel CLI for interactive multiple-choice prompts.

The agent (Codex CLI) calls this from its sandboxed shell to *pause* the
turn, ask the user a multiple-choice question, and resume with whatever
the user picks (or types).

Env vars (auto-injected by the backend when spawning codex):
    CODEX_BACKEND_URL  — base URL the script POSTs to (e.g. http://127.0.0.1:8001)
    CODEX_AGENT_TOKEN  — per-startup secret, sent as ``X-Agent-Token``
    CODEX_SESSION_ID   — integer session id the question is bound to

Usage (from the agent's shell):
    codex-ask-user --question "Pakai stack apa?" \
                   --option "Next.js + Tailwind" \
                   --option "Vite + React" \
                   --option "Plain HTML/CSS/JS" \
                   [--allow-multiple] [--no-freetext]

Stdout on success: the answer text the user picked or typed.
Exit status:
    0 — got an answer (printed to stdout)
    2 — user pressed Stop / cancelled the question
    3 — bad invocation (missing env / args)
    4 — backend error
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        sys.stderr.write(f"codex-ask-user: missing env {name}\n")
        sys.exit(3)
    return v


def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        **headers,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8") or "{}")


def main() -> int:
    p = argparse.ArgumentParser(description="Ask the user a multiple-choice question and wait for the answer.")
    p.add_argument("--question", required=True, help="The question text shown to the user.")
    p.add_argument(
        "--option",
        action="append",
        default=[],
        help="A choice the user can tap. Pass multiple times.",
    )
    p.add_argument(
        "--allow-multiple",
        action="store_true",
        help="Let the user pick more than one option.",
    )
    p.add_argument(
        "--no-freetext",
        action="store_true",
        help="Disallow free-text fallback (force one of the options).",
    )
    args = p.parse_args()

    base = _env("CODEX_BACKEND_URL").rstrip("/")
    token = _env("CODEX_AGENT_TOKEN")
    sid = int(_env("CODEX_SESSION_ID"))
    headers = {"X-Agent-Token": token}

    try:
        started = _post(
            f"{base}/api/ask-user/start",
            {
                "session_id": sid,
                "question": args.question,
                "options": args.option,
                "allow_multiple": bool(args.allow_multiple),
                "allow_freetext": not args.no_freetext,
            },
            headers=headers,
            timeout=10.0,
        )
    except urllib.error.URLError as e:
        sys.stderr.write(f"codex-ask-user: start failed: {e}\n")
        return 4

    qid = str(started.get("id") or "").strip()
    if not qid:
        sys.stderr.write(f"codex-ask-user: no question id returned: {started}\n")
        return 4

    # Long-poll. The backend's wait endpoint blocks ~290s before returning
    # ``pending=True``; we just keep retrying. Total cap: ~30 minutes.
    deadline = time.time() + 30 * 60
    while True:
        if time.time() > deadline:
            sys.stderr.write("codex-ask-user: deadline (30 min) exceeded\n")
            return 4
        try:
            r = _post(
                f"{base}/api/ask-user/wait",
                {"id": qid, "timeout": 290.0},
                headers=headers,
                timeout=310.0,
            )
        except urllib.error.URLError as e:
            sys.stderr.write(f"codex-ask-user: wait failed: {e}; retrying\n")
            time.sleep(2.0)
            continue
        if r.get("pending"):
            continue
        ans = r.get("answer")
        if ans == "__cancelled__":
            sys.stderr.write("codex-ask-user: question cancelled by user\n")
            return 2
        if ans is None:
            sys.stderr.write(f"codex-ask-user: unexpected response: {r}\n")
            return 4
        sys.stdout.write(str(ans))
        sys.stdout.flush()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
