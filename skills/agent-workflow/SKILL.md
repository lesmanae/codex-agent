# Agent Workflow — Skill

**Trigger phrases**: rencanakan, plan, todo, checklist, tracking, multi-step,
many steps, refactor, implement feature, fitur baru, kerjain semua, kerjakan,
do everything, pull request, PR, merge request, MR, branch, deploy, lint
typecheck, run tests, ci, ci/cd, hand-off, prepare release.

This is the cross-cutting operating skill for **how** you tackle any
non-trivial task. It does NOT cover any specific tech stack — domain skills
(devops-vps, python-dev, git-workflow, …) handle that. Apply this skill
**concurrently** with the relevant domain skill on every multi-step task.

---

## When to apply

Apply on:

- Any request with 3+ distinct steps.
- Any code change in a tracked repository.
- Any infrastructure change to a running service.
- Any "do all of these" / list of items / repetitive batch.
- Any task where partial completion would be misleading.

Skip on genuinely trivial one-shot requests (single fact lookup, single
shell command, one-line clarification).

---

## The loop

1. **Restate the goal in one line.** What does success look like?
2. **Build a plan.** Numbered markdown list. Each step is concrete and
   verifiable.
3. **Mark the first step `[~]` and execute it.** Use real tools, in
   parallel when independent.
4. **Verify the step succeeded.** Run the matching check (curl, ss,
   `git status`, lint, tests) before moving on.
5. **Mark `[x]` and move to the next step.** Update the plan in the same
   reply if you discovered new sub-tasks.
6. **Loop until done.** Then run a final verification pass over the whole
   task (lint + tests + smoke check, or whatever applies).
7. **Hand off.** Summarize what changed, link the PR / preview URL /
   restarted service, and tell the operator what they need to do next
   (merge, deploy, restart, nothing).

---

## Plan formatting

Inline in the chat reply, render as:

```markdown
**Plan**

1. [~] Read repo conventions (README, CONTRIBUTING, package files)
2. [ ] Implement <change> in <files>
3. [ ] Run lint + typecheck + tests
4. [ ] Open PR
5. [ ] Verify CI passes
```

Status legend:

- `[~]` currently in progress (only **one** at a time)
- `[x]` complete
- `[ ]` pending
- `[blocked]` waiting on external input (operator clarification, missing
  secret, broken env)

Re-emit the plan as it changes — do not silently mutate it.

---

## Persistence rules

- Push through task-level errors (bugs, failing tests, lint warnings).
  Do not give up because something failed once.
- For repetitive work ("rename across 50 files", "process all entries"),
  keep an explicit checklist and **finish all of it**. Do not stop early
  because it's tedious.
- After 3–4 attempts on the same step with the same error, stop and
  message the operator. Especially if the error is environmental (network
  down, permission denied on system resources, missing tool).

## Never silently drop a task

If you decide a step the operator asked for is infeasible, **keep it in
the plan** as `[blocked]` and immediately tell the operator:

- what you tried
- what's blocking
- what alternatives are possible

Do NOT delete the step from the plan — the operator may have context that
unblocks it.

---

## Tool-first execution

- Always prefer running a tool over telling the operator to run it
  themselves. "Cek status nginx" → `systemctl status nginx`, then report.
- Issue independent tool calls in **parallel** in a single response.
  Examples worth parallelizing: reading 3 files, checking 3 URLs, running
  `df -h` + `free -h` + `uptime` together.
- Serialize only when a later call needs an earlier call's output.
- Do not paste a command for the operator to run unless they explicitly
  asked for the command itself.

## Verify, then act

Before any destructive operation, verify the target once more:

| Operation | Verify with |
|---|---|
| `rm -rf <path>` | `ls -la <path>` first |
| `docker compose down` | `docker compose ps` first |
| `git reset --hard` | `git status` and `git stash list` first |
| `nginx -s reload` | `nginx -t` first |
| `systemctl restart` | `systemctl cat <unit>` to confirm correct unit |
| `DROP TABLE` / `TRUNCATE` | `SELECT count(*)` and confirm DB name first |

For any mutating change to a system config file, take a quick backup:

```bash
cp /etc/nginx/sites-available/foo{,.bak.$(date +%s)}
```

---

## PR-driven code changes

For any change in a tracked repository (more than a one-line typo fix):

1. **Check the repo for setup files.** README.md, CONTRIBUTING.md,
   AGENTS.md, package manifest. Detect lint / typecheck / test commands.
2. **Detect pre-commit hooks** (`.pre-commit-config.yaml`, `.husky/`).
   If present, install them: `pre-commit install`.
3. **Branch off main** with a meaningful name:
   ```bash
   git checkout -b feat/<short-name>
   # or:
   git checkout -b fix/<bug-id>
   ```
4. **Implement focused edits.** No tangential refactors. No `git add .`
   — stage specific files.
5. **Run lint + typecheck + tests** before committing. Address every
   issue you introduced. Do NOT skip hooks.
6. **Commit with a clear message** following the repo's existing tone
   (conventional commits if the log uses them).
7. **Push** and **open a PR** with:
   - one-line summary
   - what + why (not how — the diff shows how)
   - verification steps the reviewer can run
   - link to issue / discussion if any
8. **Wait for CI**. Fix failures. After 3 attempts without progress,
   stop and ask the operator.
9. **Tell the operator the PR URL** and the rebuild / deploy command.

For pure ops on the live VPS (e.g. `nginx -s reload`, `docker compose
restart`), edit in place but **log the change** in your reply so the
operator can audit.

---

## Hand-off message

When you finish, the final message should contain (in this order):

1. **Outcome line.** One sentence: what works now that didn't before.
2. **Where to look.** PR URL, log path, preview URL, file references.
3. **What's next for the operator.** "Merge this", "rebuild with `docker
   compose build && docker compose up -d`", "no action needed".
4. **Anything you couldn't do.** Be plain — "X failed because Y, see
   logs at Z."

Do not bury bad news in a wall of celebratory text. Lead with errors if
errors occurred.

---

## Quality gates before declaring done

Run these in order; do not declare a task complete until all relevant gates
pass.

| Gate | When | Command examples |
|---|---|---|
| Lint | every code change | `ruff check .`, `npm run lint`, `golangci-lint run` |
| Format | every code change | `ruff format`, `npm run format`, `gofmt` |
| Type check | typed projects | `mypy`, `pyright`, `tsc --noEmit` |
| Tests | every code change with tests | `pytest -q`, `npm test`, `go test ./...` |
| Build | every code change | `npm run build`, `docker build`, `cargo build` |
| Smoke | live ops change | `curl`, `ss -tlnp`, `systemctl is-active`, `docker compose ps` |
| Logs clean | live ops change | `journalctl -u <unit> --since '1 minute ago'`, `docker logs --tail 50 <c>` |
| CI green | PR submitted | check CI status before pinging operator |

If any gate fails and you can't fix it, surface it as a `[blocked]`
plan item — do not pretend it passed.
