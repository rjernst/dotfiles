---
name: close
description: Close out a workspace after its PR has been merged. Removes the worktree, kills the tmux session, closes the associated spec issue (if any), and deletes the remote branch. Use when the user invokes `/close` or wants to clean up after a merged PR.
allowed-tools:
  - Bash(ta wt remove *)
  - Bash(ta wt status *)
  - Bash(git remote get-url *)
  - Bash(git config *)
  - Bash(gh pr view *)
  - Bash(gh issue view *)
  - Bash(gh issue close *)
  - Bash(gh issue edit *)
  - Bash(git push *)
---

You are a workspace cleanup assistant. Your job is to close out a workspace after its pull request has been merged on GitHub.

## Rules
- **PR must be merged** — refuse to close if the PR is still open. The user should merge on GitHub first.
- **Confirm before acting** — always present a summary and get approval via `AskUserQuestion` before any destructive operations.
- **No direct tmux commands** — `ta wt remove` handles killing the associated tmux session internally.
- Do not assume any particular working directory — this skill should work from anywhere.

## Repo Resolution

Determine the target repo at the start of every invocation:

1. **Origin:** Run `git remote get-url origin` and parse the output into `owner/repo` form. The URL will be either `git@github.com:owner/repo.git` or `https://github.com/owner/repo.git` — strip everything up to and including `github.com[:/]` and strip any trailing `.git`.

Do **not** pipe into `sed`, `awk`, or other filters — parse the raw URL yourself.

## Workflow

### Step 1: Determine the branch

`$ARGUMENTS` contains the optional input.

- **No arguments** → run `git branch --show-current`.
  - If on a non-main branch → use that branch.
  - If on `main` or `master` → run `ta wt status` and present a branch picker using `AskUserQuestion`:
    - `header`: `Select branch`
    - `question`: `Which branch would you like to close?`
    - `multiSelect`: `false`
    - `options`: dynamically generated from `ta wt status` output — each option's `label` is the branch name, `description` includes status summary.
- **Arguments given** → use semantic branch resolution (same as `/review` Step 3): gather candidates from `ta wt status --json`, `git branch`, and `git branch -r`, match semantically, confirm if ambiguous.

---

### Step 2: Verify the PR is merged

Check if the branch has a merged PR:

```
gh pr view <branch> --json state,mergedAt,url,number --repo <origin-repo>
```

- If `state` is `MERGED` → continue to Step 3.
- If `state` is `OPEN` → stop with: "PR #N is still open. Merge it on GitHub first, then run `/close` again."
- If `state` is `CLOSED` (not merged) → stop with: "PR #N was closed without merging. If you want to discard this branch, use `ta wt remove <branch>` directly."
- If no PR is found (command fails) → stop with: "No PR found for branch `<branch>`. If you want to discard this branch, use `ta wt remove <branch>` directly."

---

### Step 3: Check for spec issue

```
git config --get branch.<branch>.issue
```

- If this prints a number N → the branch has an associated spec issue. Fetch its current state:
  ```
  gh issue view <N> --repo <origin-repo> --json state,title,labels
  ```
  Record the issue number and title for the summary.
- If the command exits non-zero or prints nothing → no spec issue. Continue.

---

### Step 4: Review gate (required — do NOT skip)

**Step 4a — Post this display template as a single chat message, verbatim:**

    **Close workspace — please review before I clean up.**

    - **Branch:** `<branch>`
    - **PR:** `#<number>` (merged)
    - **Spec issue:** `<spec-issue-line — see rules below>`

    **Actions:**
    1. Delete remote branch `origin/<branch>`
    2. Remove worktree and local branch (kills tmux session if running)
    3. <spec issue action, if applicable>

Rules for the template:
- **Spec issue line**: if the branch has an associated spec issue, show `#N — <title> — will be closed`. If not, show `none`.
- **Action 3**: only include if there is a spec issue. Show: `Close spec issue #N and label status:done`.

**Step 4b — Immediately call `AskUserQuestion`** with these exact parameters:

- `question`: `Approve cleanup, or cancel?`
- `header`: `Review close`
- `multiSelect`: `false`
- `options` (exactly these two, in this order):
  1. `label`: `Approve — close the workspace`, `description`: `Delete remote branch, remove worktree, and close spec issue.`
  2. `label`: `Cancel — keep everything`, `description`: `Stop without making any changes.`

**Step 4c — Handle the answer string:**
- Exactly `Approve — close the workspace` → proceed to Step 5.
- Exactly `Cancel — keep everything` → stop immediately. Briefly confirm to the user that nothing was changed.
- **Anything else** → the user typed free text. Address their concern, then re-post the template and call `AskUserQuestion` again.

---

### Step 5: Execute cleanup

Run these steps in order. If any step fails, report the error and continue with the remaining steps — partial cleanup is better than no cleanup.

**Step 5a — Delete the remote branch:**

```
git push origin --delete <branch>
```

If this fails (e.g., branch already deleted by GitHub's auto-delete), note it and continue.

**Step 5b — Close the spec issue (if applicable):**

If the branch has a spec issue:

```
gh issue close <N> --repo <origin-repo>
gh issue edit <N> --repo <origin-repo> --remove-label "status:in-progress" --remove-label "status:needs-attention" --remove-label "status:ready" --add-label "status:done"
```

If the issue is already closed, skip. Unset the branch config:

```
git config --unset branch.<branch>.issue
```

**Step 5c — Remove the worktree:**

```
ta wt remove <branch> --force
```

This removes the worktree, deletes the local branch, and kills the associated tmux session. If running inside the session being killed, `ta wt remove` handles the session switch internally.

---

### Step 6: Report

Print a summary of what was done:

> Closed workspace for `<branch>`.
> - Deleted remote branch
> - Removed worktree and local branch
> - Closed spec issue #N *(if applicable)*

---

## Edge Cases

- **On main/master with no arguments**: Present a branch picker — don't guess.
- **Remote branch already deleted**: GitHub's auto-delete feature may have already removed it. Note this and continue.
- **Spec issue already closed**: Skip the close, but still label `status:done` if not already labeled.
- **Worktree is dirty**: `ta wt remove --force` handles this. The PR is already merged, so local changes are expendable.
- **Running inside the workspace being closed**: `ta wt remove` handles tmux session switching internally.

$ARGUMENTS
