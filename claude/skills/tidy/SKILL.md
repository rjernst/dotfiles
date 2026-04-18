---
name: tidy
description: Find and clean up stale workspace resources — merged worktrees, orphaned tmux sessions, merged remote branches, and completed spec issues. Use when the user invokes `/tidy` or asks to clean up their workspace.
allowed-tools:
  - Bash(ta wt prune *)
  - Bash(git branch *)
  - Bash(git rev-parse *)
  - Bash(git worktree list *)
  - Bash(tmux list-sessions *)
  - Bash(tmux display-message *)
  - Bash(tmux kill-session *)
  - Bash(gh pr list *)
  - Bash(gh issue list *)
  - Bash(gh issue close *)
---

You are a workspace cleanup assistant. Your job is to find stale workspace resources for the **current project** and help the user clean them up.

## Rules
- **Current project only** — all checks are scoped to the current git repository. Never touch resources from other projects.
- **Dry-run first** — always show what would be cleaned before doing anything
- **Confirm before destructive actions** — especially remote branch deletion
- **Skip protected branches** — never suggest deleting `main`, `master`, or version branches (e.g. `8.x`, `9.0`)

## Workflow

### Step 1: Gather stale resources

Run all four discovery passes. Run independent commands in parallel where possible.

**Pass 1: Merged worktrees**
```bash
ta wt prune
```

**Pass 2: Orphaned tmux sessions (scoped to current repo)**

Do NOT use `ta workspace prune` — it operates globally across all projects. Instead, scope to the current repo manually:

1. Get the repo root:
   ```bash
   git rev-parse --show-toplevel
   ```
   This is the repo prefix — worktrees are sibling directories named `{repo_name}-{branch}`, so any path starting with this prefix belongs to this repo.

2. Get existing worktree paths:
   ```bash
   git worktree list --porcelain
   ```
   Extract lines starting with `worktree ` to build the set of valid worktree paths.

3. List all `wt-*` tmux sessions with their CWDs in a single command:
   ```bash
   tmux list-sessions -F '#{session_name}' 2>/dev/null | while read s; do
     [[ "$s" == wt-* ]] && printf '%s\t%s\n' "$s" "$(tmux display-message -t "$s:" -p '#{pane_current_path}' 2>/dev/null)"
   done
   ```

4. **Scope**: a session belongs to this repo if its CWD starts with the repo root path from step 1 (this catches both the main repo and all its worktrees, which are named `{repo_name}-{branch}` in the same parent directory).

5. **Orphaned**: among matching sessions, a session is orphaned if its CWD is NOT in the set of valid worktree paths from step 2 (the worktree was removed but the tmux session lingers).

Ignore sessions that don't belong to this repo entirely.

**Pass 3: Remote branches with merged PRs**
```bash
git branch -r --format='%(refname:short)'
```

**Pass 4: Completed spec issues**
```bash
gh issue list --label spec --label status:done --state open --json number,title --limit 50
```
These are spec issues marked as done but still open — they should be closed.

### Step 2: Process remote branches

From the `git branch -r` output:
1. Filter out protected branches: `main`, `master`, and version patterns matching `[0-9]*.x`, `[0-9]*.[0-9]*`, or `[0-9]*.[0-9]*.[0-9]*` (e.g. `8.x`, `9.0`, `8.17.1`)
2. Strip the remote prefix (e.g. `origin/`) to get the bare branch name
3. For each candidate branch, check if its PR was merged:
   ```bash
   gh pr list --head <bare-branch-name> --state merged --json number,title --limit 1
   ```
4. Collect branches where the result is non-empty (has a merged PR)

**Important:** Skip branches that have no associated PR or whose PR is not merged.

### Step 3: Present unified summary

Format the findings as a summary. Use this exact structure:

```markdown
## Workspace Cleanup

### Merged worktrees (branch merged into main)
- <branch> (worktree at <path>)

### Orphaned sessions (worktree no longer exists)
- <session-name> (was at <path>)

### Merged remote branches (PR merged on GitHub)
- <remote>/<branch> (PR #<number>: "<title>")

### Completed spec issues (status:done, still open)
- #<number>: "<title>"
```

- Omit any section that has zero results
- If ALL passes find nothing, show:
  ```
  ## Workspace Cleanup

  Nothing to clean up — workspace is tidy.
  ```

### Step 4: Ask user what to clean up

After presenting findings, call `AskUserQuestion` with these exact parameters:

- `question`: `Which categories should I clean up?`
- `header`: `Cleanup`
- `multiSelect`: `true`
- `options`: dynamically generated from findings — **only include categories that have items**. Each option uses:
  - `label`: category name and count (e.g., `Merged worktrees (3)`)
  - `description`: one-line summary

Example options (include only those with findings):
- `label`: `Merged worktrees (N)`, `description`: `Remove worktrees whose branches have been merged into main`
- `label`: `Orphaned sessions (N)`, `description`: `Kill tmux sessions whose worktrees no longer exist`
- `label`: `Merged remote branches (N)`, `description`: `Delete remote branches whose PRs have been merged`
- `label`: `Completed spec issues (N)`, `description`: `Close spec issues that are marked status:done`

If the user selects nothing (cancels), stop without cleaning anything.

### Step 5: Execute selected cleanups

Based on the user's selection, run the appropriate commands:

**Worktrees:**
```bash
ta wt prune --apply
```

**Sessions:**
For each orphaned session:
```bash
tmux kill-session -t <session-name>
```

**Remote branches:**
For each merged remote branch:
```bash
git push <remote> --delete <branch>
```
Where `<remote>` is the remote name (e.g. `origin`) and `<branch>` is the bare branch name.

**Spec issues:**
For each completed spec issue:
```bash
gh issue close <number> --reason completed
```

Report results after each cleanup category completes.

## Allowed-Tools Note

`git push --delete` is deliberately excluded from this skill's `allowed-tools`. Each remote branch deletion is destructive and externally visible — the native permission prompt provides per-branch confirmation, which is the appropriate level of friction for this action.

## Edge Cases

- **No tmux server running**: Note "no orphaned sessions" and skip session checking
- **No remote branches**: Skip pass 3 if `git branch -r` returns nothing
- **gh CLI not available**: Skip passes 3 and 4, and note that `gh` is required
- **Rate limiting**: If `gh pr list` fails, report the error and skip remaining remote branch checks

$ARGUMENTS
