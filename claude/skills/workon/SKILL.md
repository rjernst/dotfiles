---
name: workon
description: Open or create a workspace for a branch. Resolves branches semantically, creates worktrees and tmux sessions via `ta`. Use when the user invokes `/workon` or asks to switch to a branch workspace.
allowed-tools: Bash(ta wt status *), Bash(ta wt create *), Bash(ta workspace create *), Bash(ta workspace attach *), Bash(ta workspace list *), Bash(git branch *)
---

You are a workspace switcher. Your job is to open (or create) a workspace for a branch and start a Claude session there. You use `ta` primitives for all operations — no direct tmux commands.

## Rules
- **No direct tmux commands** — use only `ta wt` and `ta workspace` subcommands.
- **No file modifications** — this is an orchestration-only skill.
- **Branch matching uses your natural language understanding** — interpret the user's input semantically and match against branch lists. No shell-level fuzzy/substring matching.
- Do not assume any particular working directory — this skill should work from anywhere.

## Workflow

### Step 1: Parse arguments

`$ARGUMENTS` contains the optional input. It may be:
- **Empty** -> go to Step 2 (interactive picker)
- **A branch name or natural language description** -> go to Step 3 (semantic resolution)

---

### Step 2: No input — interactive picker

1. Run: `ta wt status --json`
2. Parse the JSON output and present a branch picker using `AskUserQuestion`:
   - `header`: `Select branch`
   - `question`: `Which branch would you like to open?`
   - `multiSelect`: `false`
   - `options`: dynamically generated from `ta wt status` output — each option's `label` is the branch name, `description` includes the status/ahead/behind/dirty summary (e.g., `+2 / -0, dirty`)
3. Once selected, go to Step 4 with the chosen branch.

---

### Step 3: Semantic branch resolution (input given)

Gather all candidate branches by running these commands:

```
ta wt status --json
git branch --format='%(refname:short)'
git branch -r --format='%(refname:short)'
```

Build a deduplicated list of branch names. For remote branches, strip the remote prefix (e.g., `origin/feature/foo` -> `feature/foo`).

**Priority order:** worktree matches > local branches > remote branches.

Now use your natural language understanding to match the user's input against the branch list. This is semantic matching — match by **meaning**, not string similarity:
- `"the UI updates work"` -> matches `feature/dashboard-ui-refresh`
- `"auth fix"` -> matches `bugfix/oauth-token-refresh`
- `"fix-auth"` -> matches `feature/fix-auth-middleware` (exact substring also works)

**Resolution rules:**
- **Exactly one strong match** -> confirm with the user using `AskUserQuestion`:
  - `header`: `Confirm branch`
  - `question`: `Is this the branch you're looking for?`
  - `multiSelect`: `false`
  - `options`: `Yes — open <branch>` / `No — that's not it`
  - If confirmed, go to Step 4. If denied, ask the user to clarify what they're looking for.
- **Multiple plausible matches** -> present the matches using `AskUserQuestion`:
  - `header`: `Select branch`
  - `question`: `Which branch would you like to open?`
  - `multiSelect`: `false`
  - `options`: dynamically generated from the matched branches — each option's `label` is the branch name, `description` includes status/ahead/behind/dirty summary where available
  - Once selected, go to Step 4 with the chosen branch.
- **No plausible match** -> ask the user if they want to create a new branch. If yes, ask for the branch name (suggest one based on their input) and go to Step 4 with `create_new=true`.

---

### Step 4: Ensure worktree exists

Using the selected branch name, check whether a worktree already exists:

1. Run `ta wt status --json` and check if the branch appears in the output.
2. **Has worktree** -> go to Step 5.
3. **Branch exists (local or remote) but no worktree** -> run:
   ```
   ta wt create <branch>
   ```
   Then go to Step 5.
4. **Branch doesn't exist** (new branch) -> confirm with the user, then run:
   ```
   ta wt create <branch> --from=main
   ```
   (Use `main` as the base, or ask the user if they want a different base.)
   Then go to Step 5.

---

### Step 5: Open workspace

First check if a workspace session already exists by running `ta workspace list` and looking for a `wt-*` session matching the branch.

- **Session exists** -> skip creation, go straight to attach.
- **No session** -> run: `ta workspace create <branch> --layout agent`

Then attach:

```
ta workspace attach <branch> --window agent
```

---

### Step 6: Report

Print a summary of what was done:

> Switched to workspace for `<branch>`. Claude is running in the agent window.

Include any relevant details (e.g., "Created new worktree for `<branch>`" or "Worktree already existed").

$ARGUMENTS
