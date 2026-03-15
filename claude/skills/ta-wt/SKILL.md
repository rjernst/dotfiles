# ta wt — Worktree Manager

Reference documentation for the `ta wt` subcommands. This is not an invocable skill.

## Subcommands

### `ta wt list [--json] [--full]`

List all worktrees with enriched status (branch, path, ahead/behind, dirty status). Includes all worktrees (including main).

- `--json` — Output as JSON array (fields: `branch`, `status` (dirty status, e.g. "clean"/"dirty(2S,1M)"/"current"), `ahead`, `behind`, `path`; with `--full` adds: `commit_message`, `commit_date`)
- `--full` — Include last commit message and date

### `ta wt create <branch> [path] [--remote <remote>]`

Create a worktree tracking a remote branch. Defaults to `upstream` remote if it exists, else `origin`. Auto-generates path as `../<repo>-<branch>` if not specified.

### `ta wt remove <branch> [--force]`

Remove a worktree and delete the local branch. Refuses if dirty unless `--force`.

### `ta wt prune [--apply]`

Find worktrees whose branches are merged into main. Dry-run by default; `--apply` to actually remove them. Skips dirty worktrees and those with active git operations.

### `ta wt status [--json]`

Quick one-line status of each worktree (skips main). Shows branch, classification, ahead count, and dirty status.

- `--json` — Output as JSON array with fields: `branch`, `status` (classification, e.g. "ready"/"wip"/"merged"), `ahead`, `behind`, `dirty` (detail string), `path`. Unlike `list --json`, uses classification-based status and always skips main.

### `ta wt merge <branch> [--target <branch>] [--message <msg> | --message-file <path>]`

Squash-merge a branch into the target (default: `main`). Validates both worktrees are clean, performs `git merge --squash`, commits, removes the source worktree, and kills the workspace tmux session.

- `--target <branch>` — Merge into this branch instead of `main`
- `--message <msg>` — Use this string as the commit message
- `--message-file <path>` — Read the commit message from this file
- Default commit message: `"Squashed '<branch>' into <target>"`
- `--message` and `--message-file` are mutually exclusive

## Exit Codes

- `0` — Success
- `1` — Runtime error (dirty state, merge conflicts, missing remote)
- `2` — Usage error (bad arguments, missing branch)

## Status Classifications (from `ta wt status`)

- `current` — The worktree you are currently in
- `merged` — Branch is fully merged into main
- `conflict` — Branch has merge conflicts with main
- `ready` — Clean, no conflicts, has commits ahead of main (all pushed to upstream)
- `almost` — Clean, has commits ahead, but unpushed commits to upstream
- `wip` — Dirty working tree
