---
name: git-tracking-recovery
category: references
---

# Git Recovery for Untracked Files and Accidental Overwrites

## The Problem

A test or source file gets accidentally overwritten (or truncated to 0 bytes). Because the file was **never tracked by Git**, `git checkout -- <file>` and `git show HEAD:<file>` both fail — there is no committed version to restore. This is the single most common way to lose work in this repo.

## Recovery Paths (in order of reliability)

### 1. Hermes checkpoints (preferred — automatic safety net)
Checkpoints are enabled globally (`hermes config set checkpoints.enabled true`). Before any `write_file` or `patch`, a snapshot is taken in the shadow store at `~/.hermes/checkpoints/store/`.

```bash
# List snapshots for this project
/rollback

# Preview what will change
/rollback diff N

# Restore a single file
/rollback N -- tests/test_app.py

# Full project restore
/rollback --all
```

### 2. Git staged (index) state
If the file was staged before it was overwritten, the index still holds the last added content:

```bash
# Show the staged version and write it back
git show :tests/test_app.py > tests/test_app.py

# Verify
wc -l tests/test_app.py
```

Only works if `git add` was run on the correct version first.

### 3. Git committed (HEAD) state
Only works if the file was committed at some point:

```bash
git show HEAD:tests/test_app.py > tests/test_app.py
```

**Fails with `error: ... not tracked`** if the file was never committed.

### 4. .bak files
The repo has backup files in-place during edits:
- `src/web/index.html.bak`
- `src/paper/engine.py.bak`

Check for these before assuming total loss.

### 5. Session history (Hermes only)
If the original file content appeared in the conversation transcript, recover it with `session_search` or `read_file` of the tool result (not from chat memory).

## Staging a Clean Baseline (Prevention)

**Before any destructive edit:**

```bash
# 1. Update .gitignore to exclude build/cache artifacts
#    (must list __pycache__/, .ruff_cache/, .pyc, .ruff_cache/, .env, .env.*, .dockerignore)

# 2. Stage everything except secrets and caches
git add -A src/ tests/ .gitignore

# 3. Verify the file is tracked and has content
git ls-files tests/test_app.py     # should print the path
wc -l tests/test_app.py            # should show line count

# 4. Run tests NOW — the baseline must be green
python -m pytest tests/ -q
```

### Key Rule

Never use `write_file` on an untracked file without first running `git add` on the current version. A 2,062-byte one-line overwrite is impossible to undo if nothing in Git's object store references the original.

## Staged vs Unstaged

- **Staged**: `git status --short` shows `A` (added) or `M` (modified) — these files are tracked and can be recovered via `git show :<file>` or `git show HEAD:<file>`.
- **Unstaged**: `git status --short` shows `??` (untracked) or `M` (modified but not staged) — these files are not in the index and cannot be recovered by `git checkout` unless they were previously staged.

## No Secrets in Staged Files

`git status --short` and `git diff --cached` show only file paths — no secret values are exposed in the diff output. All secrets are handled via `.env` (not in version control) or environment variables.

## Git Status Summary

- **Branch**: `master`
- **Status**: 18 staged, 2 modified, 22 untracked
- **Commit history**: 2026-09-11 commit hash `a1b2c3d4e5f6...` (example) — `git log --oneline` for details.

## Important: Never Stage an Empty File

If `tests/test_app.py` is 0 bytes after an overwrite:
- **Do not** run `git add tests/test_app.py` — that commits the empty file.
- **Do** recover the content first (via checkpoint, session history, or `.bak`).
- Then `git add -f tests/test_app.py` to stage the restored content.

## Staging Checklist

1. `git status --short` — verify `src/` and `tests/` are either `A` (added) or `M` (modified), not `??` (untracked).
2. `git diff --cached --name-only` — verify `tests/test_app.py` is listed.
3. `git ls-files tests/test_app.py` — should return the path if tracked.
4. `git diff --cached --name-only` — verify no unintended files are staged.
5. `git status` — confirm no uncommitted changes in `.env` or credential files.