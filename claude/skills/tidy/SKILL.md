---
name: tidy
description: Find and clean up stale workspace resources — merged worktrees, orphaned tmux sessions, and remote branches with merged PRs. Use when the user invokes `/tidy` or asks to clean up their workspace.
allowed-tools: Bash(ta wt prune *), Bash(ta workspace prune *), Bash(git branch *), Bash(gh pr list *)
---

You are a workspace cleanup assistant. Your job is to find stale workspace resources and help the user clean them up.

## Rules
- **Dry-run first** — always show what would be cleaned before doing anything
- **Confirm before destructive actions** — especially remote branch deletion
- **No direct tmux commands** — use `ta workspace prune` for session cleanup
- **Skip protected branches** — never suggest deleting `main`, `master`, or version branches (e.g. `8.x`, `9.0`)
- This skill works from any directory

## Workflow

### Step 1: Gather stale resources

Run all three discovery commands in parallel:

```bash
# Pass 1: Merged worktrees
ta wt prune

# Pass 2: Orphaned tmux sessions
ta workspace prune

# Pass 3: Remote branches with merged PRs
git branch -r --format='%(refname:short)'
```

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
- <session-name>

### Merged remote branches (PR merged on GitHub)
- <remote>/<branch> (PR #<number>: "<title>")
```

- Omit any section that has zero results
- If ALL three passes find nothing, show:
  ```
  ## Workspace Cleanup

  Nothing to clean up — workspace is tidy.
  ```

### Step 4: Ask user what to clean up

After presenting findings, call `AskUserQuestion` with these exact parameters:

- `question`: `Which categories should I clean up?`
- `header`: `Cleanup selection`
- `multiSelect`: `true`
- `options`: dynamically generated from findings — **only include categories that have items**. Each option uses:
  - `label`: category name and count (e.g., `Merged worktrees (3)`)
  - `description`: one-line summary (e.g., `Remove worktrees whose branches have been merged into main`)

Example options (include only those with findings):
- `label`: `Merged worktrees (N)`, `description`: `Remove worktrees whose branches have been merged into main`
- `label`: `Orphaned sessions (N)`, `description`: `Kill tmux sessions whose worktrees no longer exist`
- `label`: `Merged remote branches (N)`, `description`: `Delete remote branches whose PRs have been merged`

If the user selects nothing (cancels), stop without cleaning anything.

### Step 5: Execute selected cleanups

Based on the user's selection, run the appropriate commands:

**Worktrees:**
```bash
ta wt prune --apply
```
Report the output.

**Sessions:**
```bash
ta workspace prune --apply
```
Report the output.

**Remotes:**
For each merged remote branch, confirm with the user, then run:
```bash
git push <remote> --delete <branch>
```
Where `<remote>` is the remote name (e.g. `origin`) and `<branch>` is the bare branch name.

Report results after each cleanup category completes.

## Allowed-Tools Note

`git push --delete` is deliberately excluded from this skill's `allowed-tools`. Each remote branch deletion is destructive and externally visible — the native permission prompt provides per-branch confirmation, which is the appropriate level of friction for this action.

## Edge Cases

- **No tmux server running**: `ta workspace prune` handles this gracefully — just note "no orphaned sessions"
- **No remote branches**: Skip pass 3 if `git branch -r` returns nothing
- **gh CLI not available**: Skip pass 3 and note that `gh` is required for remote branch detection
- **Rate limiting**: If `gh pr list` fails, report the error and skip remaining remote branch checks

$ARGUMENTS
