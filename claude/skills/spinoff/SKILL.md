You are a workspace spinoff assistant. Your job is to branch off current work into a dedicated worktree and workspace, moving dirty files and optionally transferring conversation context.

## Rules
- **No direct tmux commands** — use only `ta` subcommands (`ta wt`, `ta workspace`)
- **New worktree always branches from main** — regardless of which branch you're currently on
- **Ask before transferring context** — don't assume the user wants it
- **Stash includes untracked files** — use `git stash -u`
- The skill should work from any directory (main worktree or another worktree)

## Workflow

### Step 1: Determine branch name

Check `$ARGUMENTS` for a branch name. If not provided, suggest a name based on the current conversation topic (e.g., `fix/auth-token-handling`) and ask the user to confirm or provide one.

### Step 2: Check for existing branch

Run `git branch --list <branch-name>` and `git branch -r --list "origin/<branch-name>"` to check if the branch already exists. If it does, inform the user that the branch exists and that `ta wt create` will create a worktree for it (if it passes the merge-base check). Ask the user if they want to proceed. If they decline, stop.

### Step 3: Ask about context transfer

Ask the user: **"Should I carry over the conversation context to the new workspace?"**

**If yes:**
1. Summarize the current conversation into a concise context document. Include:
   - What problem was being discussed
   - Key decisions or conclusions reached
   - Any relevant code/files mentioned
   - What the user wants to do next
2. Sanitize the branch name for use in the filename (replace non-alphanumeric characters except `-` and `_` with `-`)
3. Write to `/tmp/spinoff-context-<sanitized-branch>.md`

Note: Claude Code sessions are repo-scoped, so the new workspace can also access this session's full history via `/resume`. The context file provides a quick summary so the new session doesn't have to read through the entire prior conversation.

**If no:** Skip context file creation.

### Step 4: Stash dirty files

1. Run `git status --porcelain` to check for changes (staged, unstaged, or untracked).
2. **If dirty:**
   - Note the number of dirty files for the final report.
   - If the current branch is NOT `main`, warn the user: "You have dirty files on `<branch>`, not `main`. The stash was made against a different commit than `main` — the stash pop in the new worktree might conflict."
   - Run `git stash -u` to save all changes including untracked files.
3. **If clean:** Skip stash. Note "no dirty files" for the report.

### Step 5: Create worktree

Run:
```
ta wt create <branch-name> --from=main
```

This creates a new local branch from `main` with a worktree. It does not push.

### Step 6: Pop stash in new worktree

**Only if files were stashed in Step 4:**

1. Get the worktree path by running `ta wt list --json` and parsing the `path` field for the matching branch.
2. Run `git -C <wt_path> stash pop` to apply the stashed changes in the new worktree. This works because worktrees share the same stash storage. The most recent stash (`stash@{0}`) is the one created in Step 4 — run the pop immediately after worktree creation to avoid picking up an unrelated stash.
3. **If stash pop fails** (exit code non-zero): Report the conflict to the user and tell them to resolve manually in the new worktree. Do NOT attempt auto-resolution. Continue to Step 7 (workspace setup) so the user has a workspace to resolve in.

### Step 7: Open workspace

Sanitize the branch name for use in the context filename (replace non-alphanumeric characters except `-` and `_` with `-`).

**If context file exists** (from Step 3):
```
ta workspace create <branch-name> --cmd 'claude "Read /tmp/spinoff-context-<sanitized-branch>.md for background from our previous conversation, then continue helping with the work described there."'
ta workspace attach <branch-name> --window claude
```

**If no context file:**
```
ta workspace create <branch-name>
ta workspace attach <branch-name>
```

### Step 8: Report

Output a completion message:
```
Spun off to workspace for `<branch-name>`.
- Dirty files: moved (N files) / none
- Context: transferred to /tmp/spinoff-context-<sanitized-branch>.md / skipped
- Tip: the new session can also access this conversation's full history via /resume

Claude is running in the new workspace.
```

## Edge Cases

- **Already on a non-main branch**: Dirty file migration still works (stash in current worktree, pop in new one), but warn the user about potential conflicts (handled in Step 4).
- **Branch name already exists**: Check upfront in Step 2 and inform the user. `ta wt create` handles this if the branch passes the merge-base check.
- **No dirty files, no context**: Simplest case — just create worktree and workspace, report.
- **Stash pop conflicts**: Report and tell the user to resolve manually. Still open the workspace so they can resolve there.

$ARGUMENTS
