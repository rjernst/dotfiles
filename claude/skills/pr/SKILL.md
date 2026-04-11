---
name: pr
description: Create a GitHub pull request from the current branch. Detects context, determines the base branch, crafts a title and description, presents a review gate, and creates the PR via `gh pr create`. Use when the user invokes `/pr`.
allowed-tools:
  - Bash(mktemp *)
  - Write(/tmp/**)
  - Bash(gh pr create *)
  - Bash(git push *)
  - Bash(git remote get-url *)
  - Bash(git fetch upstream *)
---

You are a pull request assistant. Your job is to create a GitHub pull request from the current branch.

## Rules
- The review gate in Step 5 is mandatory — never skip the `AskUserQuestion` step, even though `gh pr create` is auto-approved via this skill's `allowed-tools` frontmatter.
- Always push commits before creating the PR.
- Do NOT run code review — that's `/review`'s job. `/pr` assumes the code is ready.
- Keep the workspace open after PR creation — this is not a cleanup step.
- Do NOT create PRs from `main` or `master`.

## Repo Resolution

Determine the target repo at the start of every invocation:

1. **Origin:** Run `git remote get-url origin` and parse the output into `owner/repo` form. The URL will be either `git@github.com:owner/repo.git` or `https://github.com/owner/repo.git` — strip everything up to and including `github.com[:/]` and strip any trailing `.git`. Result is the user's fork (e.g., `rjernst/elasticsearch`).

2. **Upstream (for base branch detection):** Run `git remote get-url upstream` and parse the same way. If the command exits non-zero, no `upstream` remote exists — skip upstream fallback. Otherwise you get the parent repo (e.g., `elastic/elasticsearch`).

Do **not** pipe these into `sed`, `awk`, or other filters — the permission allow-list only covers `git remote get-url`, and any extra command in the pipeline will trigger a permission prompt. Parse the raw URL yourself.

If origin cannot be resolved to a GitHub `owner/repo`, inform the user and stop.

## Workflow

### Step 1: Detect context

Run these commands to determine where you are:
```
git branch --show-current
git remote
```

If the current branch is `main` or `master`, stop with an error:
> "You're on the main branch. Switch to a feature branch first."

---

### Step 2: Determine the base branch

If the user provided an explicit base branch via `$ARGUMENTS`, verify it exists and use it. Otherwise, detect automatically using the same procedure as `/merge` — see the [Base branch detection](../merge/SKILL.md#base-branch-detection) section.

---

### Step 3: Push check

1. Check if the current branch has an upstream tracking branch:
   `git rev-parse --abbrev-ref @{upstream} 2>/dev/null`
2. **If no upstream** → push with: `git push -u origin HEAD`
3. **If upstream exists**, check for unpushed commits:
   `git rev-list @{upstream}..HEAD --count`
4. If unpushed commits > 0 → push: `git push origin HEAD`
5. Report push status to the user.

---

### Step 4: Craft PR title and description

1. Get the commit history:
   ```
   git log <base-branch>..HEAD --format="%h %s"
   ```

2. Also review the diff to understand the full scope of changes:
   ```
   git diff <base-branch>...HEAD --stat
   ```

3. Craft a PR **title**:
   - Short imperative summary under 70 chars
   - Synthesize from commit messages — don't just use the first commit

4. Craft a PR **body**:
   ```
   ## Summary
   - <bullet points summarizing key changes>
   - <synthesized from commits, not raw commit list>

   ## Test plan
   - [ ] <relevant test steps>
   ```

Hold the title and body in memory — do not create the PR yet.

---

### Step 5: Review gate (required — do NOT skip)

This step is mandatory and its format is fixed. Use the exact template and `AskUserQuestion` call below every time — substitute only the `<...>` placeholders, and do not rephrase headers, reorder fields, change emphasis, or alter fence style. Consistency across invocations is the entire point of this step.

**Step 5a — Post this display template as a single chat message, verbatim:**

    **PR draft — please review before I create the pull request.**

    - **Base:** `<base-branch>`
    - **Title:** `<proposed title>`

    ````markdown
    <proposed body verbatim>
    ````

Rules for the template:
- **Outer fence must be four backticks**, not three. The PR body may contain triple-backtick code fences, and a three-backtick outer wrapper will close early and render broken. Do not substitute `~~~` or any other fence style — four backticks only.
- **No other header fields.** Do not add commit count, file-change summaries, or branch name lines. The base/title pair and the body are the full information surface.

**Step 5b — Immediately call `AskUserQuestion`** with these exact parameters:

- `question`: `Approve this PR, or cancel? To request changes, select 'Other' and describe what to change.`
- `header`: `Review PR`
- `multiSelect`: `false`
- `options` (exactly these two, in this order):
  1. `label`: `Approve — create the PR`, `description`: `Create the GitHub pull request with this title and body.`
  2. `label`: `Cancel — discard this draft`, `description`: `Stop without creating the pull request.`

Do **not** add a third "Revise" option. `AskUserQuestion` automatically appends an "Other" free-text slot, and the signposting in the `question` text routes revision requests through it. Adding an explicit "Revise" option would force a second round-trip to collect the revision text — defeating the purpose.

Do **not** add `(Recommended)` to Approve. This is a neutral human checkpoint; the agent should not lobby the user to rubber-stamp its draft.

**Step 5c — Handle the answer string:**
- Exactly `Approve — create the PR` → proceed to Step 6.
- Exactly `Cancel — discard this draft` → stop immediately. Do not call `mktemp`, do not write any files, do not run `gh pr create`. Briefly confirm to the user that the draft was discarded.
- **Anything else** (the user selected "Other" and typed text) → treat the returned string as revision instructions. Update the title and/or body in memory, then **re-post the full display template from Step 5a again** with the updated content — not a diff, not a "here's what I changed" summary, not a partial block. Then call `AskUserQuestion` again with the exact same parameters from Step 5b. Repeat until the user selects Approve or Cancel.

---

### Step 6: Create the PR

**Precondition:** Do not start this step until the user has explicitly approved the PR in Step 5. The `mktemp`, `Write(/tmp/**)`, and `gh pr create` commands are auto-approved by this skill's `allowed-tools` frontmatter, which is why the review gate is non-optional.

Follow this exact three-step procedure — do not improvise, and do not combine steps.

**Step 6a — Generate a unique temp path (Bash tool):**

```zsh
mktemp -u /tmp/pr-body.XXXXXXXX
```

This prints a unique path (e.g. `/tmp/pr-body.aB3xZ9qP`) **without** creating the file on disk. Capture the exact path from stdout and use it verbatim in Steps 6b and 6c. Do not edit, rename, or add an extension to it.

**Step 6b — Write the PR body (Write tool, not Bash):**

Use the Claude Code `Write` tool with:
- `file_path`: the exact path printed by Step 6a
- `content`: the approved PR body verbatim (no shell escaping, no backslash escapes)

Because `mktemp -u` does not create the file, `Write` can create it fresh without needing a prior `Read`. The Write tool passes `content` through the tool-call JSON channel, so backticks, `$`, `%`, code fences, and embedded `EOF` markers all pass through untouched.

**Step 6c — Create the PR (Bash tool):**

```zsh
gh pr create \
  --base "<base-branch>" \
  --title "<approved title>" \
  --body-file "<path from Step 6a>"
```

Report the PR URL to the user. The workspace stays open for follow-up work.

### Do NOT

Every item below is a real failure mode that has been observed. None of them are acceptable substitutes for the three-step procedure above:

- **Do not use heredocs** (`cat <<EOF > "$f"` / `cat <<'EOF'`). Unquoted `EOF` causes shell expansion of `$` and backticks inside the body; the delimiter can collide with content; and trailing-newline handling is fragile.
- **Do not use `echo` or `printf`** to emit the body. Newlines, `%`, and backslashes misbehave across shells and `echo` variants.
- **Do not pass `--body "<inline string>"`**. Shell quoting of multi-line markdown is a guaranteed breakage on PR content.
- **Do not use a fixed temp path** like `/tmp/pr-body.md`. Multiple concurrent `/pr` invocations will clobber each other.
- **Do not put `X`s anywhere except the end of the `mktemp` basename** (e.g. never `mktemp /tmp/foo-XXXXXX.md`). On macOS (BSD `mktemp`), trailing characters after the `X`s break the placeholder — `mktemp` may fail or literally create a file named `foo-XXXXXX.md`, defeating uniqueness. `gh pr create --body-file` does not care about file extensions, so drop the `.md`.
- **Do not omit `-u` from `mktemp`**. Without `-u`, `mktemp` creates an empty file, which then forces the `Write` tool to require a prior `Read` before overwriting. `-u` generates the name without creating the file, which is what Step 6b needs.
- **Do not write the temp file before the review gate passes.** The whole point of the gate is that Cancel is side-effect-free.
- **Do not try to combine Steps 6a and 6b in a single Bash command** (e.g. `mktemp -u ... | xargs ...`). Keep them as two explicit tool calls so the path is captured cleanly.
- **Do not pipe `git remote get-url` into `sed` or `awk`** for repo resolution. Parse the raw URL yourself (see Repo Resolution above).

---

## Edge Cases

- **On main/master**: Error immediately — do not proceed.
- **No commits ahead of base**: Inform the user there are no changes to create a PR for.
- **Push fails**: Report the error and stop.
- **PR already exists**: `gh pr create` will error — report the existing PR URL if possible (`gh pr view --json url -q .url`).
- **Ambiguous base branch**: If multiple upstream branches are equally close ancestors, list them and ask the user to pick.

$ARGUMENTS
