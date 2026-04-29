# Git Workflow — Skill

**Trigger phrases**: clone repo, git clone, commit, branch, checkout, push,
pull, merge, rebase, cherry-pick, revert, reset, stash, tag, fetch, log, diff,
blame, conflict, resolve conflict, force push, force-with-lease, hooks,
pre-commit, submodule, worktree, gitignore, remote, origin, fork, gh pr,
github pr, gitlab mr, pull request, code review, squash, fixup, history,
shallow clone, lfs, large files, sign commit, gpg, ssh key git, deploy key.

Use this skill when the user asks anything about Git workflow on the VPS.
Audience: developer doing day-to-day Git ops. Be terse and show exact
commands.

---

## Operating principles

- **Never destroy history without confirmation.** No `reset --hard`,
  `clean -fd`, `branch -D`, `push --force` (without `--force-with-lease`),
  `gc --prune=now`, or `reflog expire` unless user explicitly asks.
- **Detect shallow clones early.** Before `log`, `blame`, `bisect`,
  `cherry-pick` across history, run
  `git rev-parse --is-shallow-repository`; if `true`, run `git fetch --unshallow`.
- **Authenticate via existing config.** Don't embed PATs in URLs. Use
  the configured credential helper / SSH key. If pushing fails, surface
  the auth error rather than working around it.
- **Conventional commits for scoped projects.** If `package.json`,
  `pyproject.toml`, or commit log shows `feat:`/`fix:`/`chore:` style,
  follow it. Otherwise mirror the user's existing tone.
- **Conflicts:** for trivial conflicts (lockfiles, import order, adjacent
  edits) resolve autonomously. For substantive conflicts (refactors,
  architectural changes), stop and ask the user.

---

## Toolbox

```bash
which git gh glab pre-commit lazygit tig
git --version
```

`gh` (GitHub CLI) is convenient but not always installed — fall back to
plain `git` + URL printing if not present.

---

## Recipes

### Clone + start work

```bash
git clone https://github.com/<owner>/<repo>.git /opt/<repo>
cd /opt/<repo>
git checkout -b feat/<short-name>
```

### Status snapshot

```bash
git status -sb
git log --oneline -10
git diff --stat
```

### Commit a focused change

```bash
git add <specific-files>          # NEVER `git add .` blindly
git diff --cached                 # final review
git commit -m "feat(scope): one-line summary

Optional body explaining the why."
```

### Push current branch

```bash
git push -u origin HEAD
```

### Sync a feature branch with main

```bash
git fetch origin
git rebase origin/main            # preferred for tidy history
# OR
git merge --no-ff origin/main     # if rebase is forbidden
```

### Resolve conflicts

```bash
git status                        # see conflicted paths
$EDITOR <file>                    # remove <<<<<<<, =======, >>>>>>> markers
git add <file>
git rebase --continue   # or: git merge --continue
```

If hopeless: `git rebase --abort` / `git merge --abort`.

### Cherry-pick a commit onto current branch

```bash
git cherry-pick <sha>
# conflict? fix, then:
git cherry-pick --continue
```

### Revert a bad commit (preserves history)

```bash
git revert <sha>
git push
```

### Stash work-in-progress

```bash
git stash push -u -m "wip"
git stash list
git stash pop                     # or `apply` to keep the stash
```

### Inspect blame on a line range

```bash
git blame -L 100,150 path/to/file
```

### Find when a string was introduced

```bash
git log -S "the_target_string" --oneline -p -- path/
```

### Open a PR (GitHub CLI)

```bash
gh pr create --fill --base main --head "$(git branch --show-current)"
gh pr view --web
```

### Squash a feature branch before merge

```bash
git rebase -i origin/main         # mark all but first as 'fixup'
git push --force-with-lease       # only on your own branch
```

---

## Pre-commit hooks

If repo has `.pre-commit-config.yaml`:

```bash
pre-commit install                # one-time per clone
pre-commit run --all-files        # lint everything
```

If a commit is rejected by hook, fix the reported issues and `git add`
the auto-fixed files before retrying — don't `--no-verify`.

---

## Recovery

- Lost a commit after `reset`: `git reflog` → `git checkout <reflog-sha>`.
- Pushed to wrong branch: `git push origin :wrong-branch` (delete) +
  re-push to the correct one.
- Accidentally committed secrets: rotate the secret first; then
  `git reset HEAD~`, edit, recommit. If already pushed, also rewrite
  history (`git filter-repo` or BFG) and force-push (only with explicit
  user confirmation).
