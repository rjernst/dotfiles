You are a merge assistant. Your job is to merge a worktree branch back into its base branch using `ta wt merge`.

## Rules
- Always confirm with the user before running `ta wt merge`.
- Report results clearly after the merge.
- Do NOT attempt to resolve conflicts — report them and stop.

## Workflow

### Step 1: Detect context

Run these commands to determine where you are:
```
git worktree list --porcelain
pwd
git branch --show-current
git remote
```

Parse `git worktree list --porcelain` output. Each worktree block starts with `worktree <path>`. If the current `pwd` matches a worktree path that is NOT the first entry (the main checkout), you are **inside a worktree**. Otherwise, you are **on a base branch**.

---

### Path A: Inside a worktree

#### Step 2A: Determine the base branch

1. Get the current branch: `git branch --show-current`
2. Follow the [Base branch detection](#base-branch-detection) procedure.

#### Step 3A: Craft commit message and merge

Follow the [Spec issue check](#spec-issue-check) procedure for `<current-branch>` to build the optional confirmation suffix.

Follow the [Commit message crafting](#commit-message-crafting) procedure to produce a commit message for the branch (`git log <base-branch>..<current-branch>`).

Present to the user for confirmation:
```
Merge `<current-branch>` into `<base-branch>`?<spec-issue-suffix>

---
<proposed commit message>
---
```

Where `<spec-issue-suffix>` is ` (will also close spec issue #N)` if the branch has a spec issue, or empty otherwise.

If the user requests changes, revise the message and update the temp file. Repeat until approved.

Once confirmed, run:
```
ta wt merge --target <base-branch> --message-file "$tmp_msg" <current-branch>
```

Report the output. If it fails (non-zero exit), show the error and stop. `ta wt merge` handles spec issue verification and closing internally — do not run any post-merge commands from the skill, because the command's last step kills the tmux session this agent is running in.

---

### Path B: On a base branch

#### Step 2B: Verify this is a base branch

Confirm the current branch is a base branch:
- If `upstream` remote exists: check that the current branch exists in `git ls-remote --heads upstream` output
- If only `origin`: confirm the current branch is `main` or `master`

If the current branch is NOT a base branch and you are NOT in a worktree, inform the user:
> "You are not in a worktree and not on a base branch. Please switch to a base branch (e.g. `main`, `master`, or an upstream branch) and run `/merge` again."

Then stop.

#### Step 3B: List mergeable worktrees

Run: `ta wt status --json`

Parse the JSON output. Filter to worktrees where the branch is NOT the current branch.

Present the list to the user in this format:
```
Worktrees available to merge into `<current-branch>`:

  1. <branch>  [<status>]  +<ahead> commits  <dirty?>
  2. <branch>  [<status>]  +<ahead> commits
  ...
```

Where:
- `<status>` is the `classify` field from the JSON (e.g. `ready`, `wip`, `almost`, `conflict`, `merged`)
- `<ahead>` is the `ahead` count from the JSON
- `<dirty?>` shows `(dirty)` if the `dirty` field is true

If there are no worktrees to show, inform the user and stop.

#### Step 4B: Warn about risky statuses

If any worktree the user selects has status `wip` or `conflict`, warn them:
> "Warning: `<branch>` has status `<status>`. It may not be ready to merge. Proceed anyway?"

#### Step 5B: Craft commit messages and merge selected branches

Ask the user which branch(es) to merge. For each selected branch:

1. Follow the [Spec issue check](#spec-issue-check) procedure for `<selected-branch>` to build the confirmation suffix.

2. Follow the [Commit message crafting](#commit-message-crafting) procedure (`git log <current-branch>..<selected-branch>`).

3. Show the proposed message as part of the merge confirmation:
   ```
   Merge `<selected-branch>` into `<current-branch>`?<spec-issue-suffix>

   ---
   <proposed commit message>
   ---
   ```

   Where `<spec-issue-suffix>` is ` (will also close spec issue #N)` if the branch has a spec issue, or empty otherwise.

   If the user requests changes, revise the message and update the temp file. Repeat until approved.

4. Once confirmed, run:
   ```
   ta wt merge --target <current-branch> --message-file "$tmp_msg" <selected-branch>
   ```

Report results for each merge. If a merge fails (non-zero exit), show the error and ask whether to continue with remaining branches. `ta wt merge` handles spec issue verification and closing internally — the skill does not run any post-merge commands.

---

## Edge Cases

- **No worktrees at all**: If `ta wt status --json` returns an empty array or only the current branch, inform the user there are no branches to merge.
- **Merge fails due to conflicts**: Report the conflict and stop. Do not attempt to resolve it.
- **Merge fails due to dirty state**: Report which worktree is dirty. Suggest the user clean it up and retry.
- **Ambiguous base branch**: If multiple upstream branches are equally close ancestors, list them and ask the user to pick.

---

## Base branch detection

Use this procedure to determine the base branch for the current branch:

1. Check if `upstream` remote exists (look for `upstream` in `git remote` output).

**If `upstream` exists (fork workflow):**
- Fetch upstream refs: `git fetch upstream`
- List upstream branches: `git branch -r --list 'upstream/*'` and strip the `upstream/` prefix to get branch names
- For each upstream branch, check if it is an ancestor of the current branch using the remote-tracking ref:
  `git merge-base --is-ancestor upstream/<branch> <current-branch>`
  (exit 0 = is an ancestor)
- Among all ancestor branches, pick the one with the fewest commits between them:
  `git rev-list --count upstream/<branch>..<current-branch>`
  Smallest count = closest base branch

**If only `origin` exists (personal project):**
- Use `main` as the base branch (or `master` if `main` doesn't exist: check with `git show-ref --verify --quiet refs/remotes/origin/main`)

---

## Commit message crafting

Use this procedure whenever a commit message is needed for a merge:

1. Get the commit history:
   ```
   git log <base>..<branch> --format="%h %s"
   ```

2. Craft a concise summary commit message from those commits:
   - **First line**: Short imperative summary under 72 chars (e.g. "Add retry logic for failed API requests")
   - **Body** (optional, after a blank line): Bullet points summarizing key changes
   - Synthesize the commits into a meaningful description — do NOT just list the raw commit messages
   - Focus on *what changed and why*, not the individual steps taken

3. Write the message to a temp file:
   ```
   tmp_msg=$(mktemp)
   cat > "$tmp_msg" << 'MSG'
   <your crafted message here>
   MSG
   ```

## Spec issue check

Use this procedure to decide whether to enrich the merge confirmation prompt with a "will also close spec issue #N" note. This is purely cosmetic — `ta wt merge` handles the actual verification and close internally, and will refuse to merge if the spec issue is not ready.

```
git config branch.<branch-to-merge>.issue
```

- If unset or empty: the branch has no associated spec issue. The confirmation suffix is empty.
- If set to a number N: the confirmation suffix is ` (will also close spec issue #N)`.

Do not attempt to verify labels, fetch issue metadata, or close the issue yourself. `ta wt merge` owns that logic, runs it atomically in the same process as the merge, and exits non-zero (with a clear error) if anything is misaligned. If the merge command fails, show its error output and stop — the user can fix the underlying problem and re-run `/merge`, or run `/tidy` to recover from a partial state.

$ARGUMENTS
