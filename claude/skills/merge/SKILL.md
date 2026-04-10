---
name: merge
description: Merge the current worktree's branch back into its base branch via `ta wt merge`. Crafts a commit message from the branch history, presents it for review, and runs the merge. Use when the user invokes `/merge` from inside a worktree.
allowed-tools: Bash(mktemp:*), Write(/tmp/**), Bash(ta wt merge:*), Bash(git fetch upstream:*)
---

You are a merge assistant. Your job is to merge the current worktree's branch back into its base branch using `ta wt merge`.

## Rules
- The review gate in Step 5 is mandatory — never skip the `AskUserQuestion` step, even though `ta wt merge` is auto-approved via this skill's `allowed-tools` frontmatter.
- Do NOT attempt to resolve conflicts — report them and stop.
- Report results clearly after the merge.

## Workflow

### Step 1: Verify you are inside a worktree

Run these commands to determine where you are:
```
git worktree list --porcelain
pwd
git branch --show-current
git remote
```

Parse `git worktree list --porcelain`. Each worktree block starts with `worktree <path>`. The first entry is the main checkout; subsequent entries are worktrees.

If the current `pwd` does **not** match any worktree path other than the first (main checkout) entry, you are NOT in a worktree. Inform the user:

> "You are not inside a worktree. Switch to the worktree you want to merge (e.g. `ta workspace attach <branch>` or `cd` into it) and run `/merge` again."

Then stop. Do not attempt to list, pick, or merge a different branch — the skill only merges the current worktree's branch into its base.

### Step 2: Determine the base branch

1. Get the current branch: `git branch --show-current`
2. Follow the [Base branch detection](#base-branch-detection) procedure.

### Step 3: Draft the commit message

Follow the [Commit message crafting](#commit-message-crafting) procedure to produce a commit message for the branch (`git log <base-branch>..<current-branch>`). Hold the drafted message in memory — **do not** write it to a temp file yet. The temp file is created in Step 6, only after the user has approved.

### Step 4: Spec issue check

Follow the [Spec issue check](#spec-issue-check) procedure for the current branch to determine what to show on the "Spec issue:" line in the review template below.

### Step 5: Review gate (required — do NOT skip)

This step is mandatory and its format is fixed. Use the exact template and `AskUserQuestion` call below every time — substitute only the `<...>` placeholders, and do not rephrase headers, reorder fields, change emphasis, or alter fence style. Consistency across invocations is the entire point of this step.

**Step 5a — Post this display template as a single chat message, verbatim:**

    **Merge review — please approve before I run `ta wt merge`.**

    - **Source:** `<current-branch>`
    - **Target:** `<base-branch>`
    - **Spec issue:** `<spec-issue-line — see rules below>`

    ````
    <proposed commit message verbatim>
    ````

Rules for the template:
- **Outer fence must be four backticks**, not three. Commit messages rarely contain triple-backtick fences, but four backticks are the safe default and keep the skill visually consistent with `/create-spec`. Do not substitute `~~~` or any other fence style — four backticks only.
- **Spec issue line**: if the branch has an associated spec issue (see the Spec issue check procedure), show exactly `#N — will be closed by the merge`. If not, show exactly `none`. No other values.
- **No other header fields.** Do not add commit count, file-change summaries, or author lines. The source/target/spec-issue triple and the commit message body are the full information surface — anything else is duplication of what the diff and commit history already show.

**Step 5b — Immediately call `AskUserQuestion`** with these exact parameters:

- `question`: `Approve this merge, or cancel? To request changes to the commit message, select 'Other' and describe what to change.`
- `header`: `Review merge`
- `multiSelect`: `false`
- `options` (exactly these two, in this order):
  1. `label`: `Approve — run the merge`, `description`: `Run ta wt merge with this commit message.`
  2. `label`: `Cancel — don't merge`, `description`: `Stop without running ta wt merge.`

Do **not** add a third "Revise" option. `AskUserQuestion` automatically appends an "Other" free-text slot, and the signposting in the `question` text routes revision requests through it. Adding an explicit "Revise" option would force a second round-trip to collect the revision text — defeating the purpose.

Do **not** add `(Recommended)` to Approve. This is a neutral human checkpoint; the agent should not lobby the user to rubber-stamp its draft.

**Step 5c — Handle the answer string:**
- Exactly `Approve — run the merge` → proceed to Step 6.
- Exactly `Cancel — don't merge` → stop immediately. Do not call `mktemp`, do not write any files, do not run `ta wt merge`. Briefly confirm to the user that the merge was discarded.
- **Anything else** (the user selected "Other" and typed text) → treat the returned string as revision instructions. Update the drafted commit message in memory, then **re-post the full display template from Step 5a again** with the updated message — not a diff, not a "here's what I changed" summary, not a partial block. Then call `AskUserQuestion` again with the exact same parameters from Step 5b. Repeat until the user selects Approve or Cancel.

### Step 6: Run the merge

**Precondition:** Do not start this step until the user has explicitly approved the commit message in Step 5. The `mktemp`, `Write(/tmp/**)`, and `ta wt merge` commands are auto-approved by this skill's `allowed-tools` frontmatter, which is why the review gate is non-optional.

Follow this exact three-step procedure — do not improvise, and do not combine steps.

**Step 6a — Generate a unique temp path (Bash tool):**

```zsh
mktemp -u /tmp/merge-msg.XXXXXXXX
```

This prints a unique path (e.g. `/tmp/merge-msg.aB3xZ9qP`) **without** creating the file on disk. Capture the exact path from stdout and use it verbatim in Steps 6b and 6c. Do not edit, rename, or add an extension to it.

**Step 6b — Write the approved commit message (Write tool, not Bash):**

Use the Claude Code `Write` tool with:
- `file_path`: the exact path printed by Step 6a
- `content`: the approved commit message verbatim (no shell escaping, no backslash escapes)

Because `mktemp -u` does not create the file, `Write` can create it fresh without needing a prior `Read`. The Write tool passes `content` through the tool-call JSON channel, so backticks, `$`, `%`, and code fences all pass through untouched.

**Step 6c — Run the merge (Bash tool):**

```zsh
ta wt merge --target "<base-branch>" --message-file "<path from Step 6a>" "<current-branch>"
```

Report the output. If it fails (non-zero exit), show the error and stop. `ta wt merge` handles spec issue verification and closing internally — do not run any post-merge commands from the skill, because the command's last step kills the tmux session this agent is running in.

### Do NOT

Every item below is a real failure mode. None of them are acceptable substitutes for the procedure above:

- **Do not use heredocs** (`cat <<EOF > "$f"` / `cat <<'EOF'`) to write the commit message. Use the `Write` tool.
- **Do not pass `--message` (inline string)** to `ta wt merge`. Shell quoting of multi-line messages with backticks and `$` is a guaranteed breakage.
- **Do not use a fixed temp path** like `/tmp/merge-msg.txt`. Concurrent `/merge` invocations would clobber each other.
- **Do not put `X`s anywhere except the end of the `mktemp` basename**. On macOS (BSD `mktemp`), trailing characters after the `X`s break the placeholder.
- **Do not omit `-u` from `mktemp`**. Without `-u`, `mktemp` creates an empty file, which then forces the `Write` tool to require a prior `Read` before overwriting.
- **Do not write the temp file before the review gate passes.** The whole point of the gate is that Cancel is side-effect-free.

---

## Edge Cases

- **Not in a worktree**: Tell the user to switch to the worktree they want to merge and stop (see Step 1).
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

Use this procedure to draft the commit message shown in the review gate:

1. Get the commit history:
   ```
   git log <base>..<branch> --format="%h %s"
   ```

2. Craft a concise summary commit message from those commits:
   - **First line**: Short imperative summary under 72 chars (e.g. "Add retry logic for failed API requests")
   - **Body** (optional, after a blank line): Brief conceptual explanation of *what* and *why* the merge changes something. Do NOT use bullet lists, and do NOT enumerate exact changes — the diff speaks for itself.
   - Synthesize the commits into a meaningful description — do NOT just list the raw commit messages.

Hold the drafted message in memory. It is written to a temp file in Step 6 only after the review gate passes.

## Spec issue check

Use this procedure to decide what to show on the "Spec issue:" line in the review template. This is purely cosmetic — `ta wt merge` handles the actual verification and close internally, and will refuse to merge if the spec issue is not ready.

```
git config --get branch.<current-branch>.issue
```

- If the command exits non-zero or prints nothing: the branch has no associated spec issue. Show `none` on the Spec issue line.
- If it prints a number N: show `#N — will be closed by the merge` on the Spec issue line.

Do not attempt to verify labels, fetch issue metadata, or close the issue yourself. `ta wt merge` owns that logic, runs it atomically in the same process as the merge, and exits non-zero (with a clear error) if anything is misaligned. If the merge command fails, show its error output and stop — the user can fix the underlying problem and re-run `/merge`, or run `/tidy` to recover from a partial state.

$ARGUMENTS
