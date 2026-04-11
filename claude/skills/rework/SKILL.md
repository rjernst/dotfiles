---
name: rework
description: Append new implementation tasks to an existing Ralph spec issue and transition it back to `status:ready` for re-execution. Use when the user invokes `/rework`, wants to add steps to a spec, or wants to feed review findings back into a spec issue.
allowed-tools:
  - Bash(mktemp *)
  - Write(/tmp/**)
  - Bash(gh issue view *)
  - Bash(gh issue edit *)
  - Bash(git config *)
  - Bash(git remote get-url *)
---

You are a spec rework assistant. Your job is to append new implementation tasks to an existing Ralph spec issue and transition it back to `status:ready` for re-execution.

## Rules
- Do NOT implement any code. This skill only modifies the spec issue.
- The review gate in Step 4 is mandatory — never skip the `AskUserQuestion` step, even though `gh issue edit` is auto-approved via this skill's `allowed-tools` frontmatter.
- New steps must follow the same format as existing steps in the issue body.
- Leave completed steps marked `[done]`. Only append new steps.
- New step numbers must continue from the last existing step number.

## Repo Resolution

Determine the target repo at the start of every invocation:

1. **Origin:** Run `git remote get-url origin` and parse the output into `owner/repo` form. The URL will be either `git@github.com:owner/repo.git` or `https://github.com/owner/repo.git` — strip everything up to and including `github.com[:/]` and strip any trailing `.git`. Result is the user's fork (e.g., `rjernst/elasticsearch`).

Do **not** pipe these into `sed`, `awk`, or other filters — the permission allow-list only covers `git remote get-url`, and any extra command in the pipeline will trigger a permission prompt. Parse the raw URL yourself.

If origin cannot be resolved to a GitHub `owner/repo`, inform the user and stop.

All `gh issue` commands **must** use `--repo <origin-repo>`.

## Input Detection

The skill receives the text after `/rework` on the command line as `$ARGUMENTS`. Detect the input type:

| Input | Detection | Action |
|-------|-----------|--------|
| (none) | Empty or blank `$ARGUMENTS`, no conversation context | Read issue from `git config branch.<current-branch>.issue`, ask the user what to rework |
| (none) | Empty or blank `$ARGUMENTS`, conversation context exists (e.g., review findings) | Extract referenced items from conversation, read issue from git config |
| `#1234` or `1234` | Numeric (with optional `#`) | Use that issue number directly |
| Natural language | e.g., "address issues 1, 3, 5" | Parse conversation context for review findings, match referenced items by number |

### Reading the issue from git config

```
git config branch.$(git branch --show-current).issue
```

If no value is set, ask the user for the issue number.

## Session Context Awareness

When invoked after a `/review` that produced numbered findings, the skill must:

1. Scan conversation history for the most recent code review output (look for the `## Code Review:` heading pattern).
2. Parse the numbered findings from **Critical** and **Suggestions** sections.
3. If the user referenced specific numbers (e.g., "address 1, 3, 5"), select only those findings.
4. If no specific numbers were referenced, include all Critical + Suggestions findings.

### Extracting findings

Review output follows this pattern:
```
## Code Review: <title>

### Critical
1. **<finding title>** — <description>
2. **<finding title>** — <description>

### Suggestions
3. **<finding title>** — <description>
4. **<finding title>** — <description>
```

Each numbered finding becomes a candidate for a new implementation step.

## Workflow

### Step 1: Determine the issue number

- From `$ARGUMENTS` if numeric input was provided
- Otherwise from `git config branch.<current-branch>.issue`
- If neither is available, ask the user

### Step 2: Fetch the current issue body

```
gh issue view <number> --repo <origin-repo> --json body,title,labels --jq '{body: .body, title: .title, labels: [.labels[].name]}'
```

Confirm the issue has the `spec` label. If not, warn: "Issue #N does not appear to be a spec issue (no `spec` label). Continue anyway?"

### Step 3: Determine new tasks

- If conversation context contains review findings (see [Session Context Awareness](#session-context-awareness)), convert each selected finding into a new implementation step.
- If no review context exists, ask the user to describe what needs to be reworked.
- For each new task, draft a step in the same format used by existing steps in the spec:

```markdown
### Step <N>: <Task name>

**Files:**
- `path/to/file` — Description

**Implement:**
1. <Concrete implementation step>

**Test:**
- <Test case description>

**Verify:** Run `<test command>`. Fix any failures.

**Review:** <What to review for>

**Address feedback:** Fix findings, re-run tests.
```

### Step 4: Review gate (required — do NOT skip)

This step is mandatory and its format is fixed. Use the exact template and `AskUserQuestion` call below every time — substitute only the `<...>` placeholders, and do not rephrase headers, reorder fields, change emphasis, or alter fence style. Consistency across invocations is the entire point of this step.

**Step 4a — Post this display template as a single chat message, verbatim:**

    **Spec rework — please review before I update the issue.**

    - **Issue:** `#<number> — <title>`
    - **New steps:** `<count>`
    - **Label:** `status:ready`

    ````markdown
    <new step content verbatim>
    ````

Rules for the template:
- **Outer fence must be four backticks**, not three. The step content may contain triple-backtick code fences, and a three-backtick outer wrapper will close early and render broken. Do not substitute `~~~` or any other fence style — four backticks only.
- **No other header fields.** Do not add repo, branch, or existing step summaries. The issue/steps/label triple and the fenced content are the full information surface.

**Step 4b — Immediately call `AskUserQuestion`** with these exact parameters:

- `question`: `Approve these new steps, or cancel? To request changes, select 'Other' and describe what to change.`
- `header`: `Review rework`
- `multiSelect`: `false`
- `options` (exactly these two, in this order):
  1. `label`: `Approve — append steps and mark ready`, `description`: `Append the new steps to the issue body and set status:ready.`
  2. `label`: `Cancel — discard changes`, `description`: `Stop without modifying the issue.`

Do **not** add a third "Revise" option. `AskUserQuestion` automatically appends an "Other" free-text slot, and the signposting in the `question` text routes revision requests through it. Adding an explicit "Revise" option would force a second round-trip to collect the revision text — defeating the purpose.

Do **not** add `(Recommended)` to Approve. This is a neutral human checkpoint; the agent should not lobby the user to rubber-stamp its draft.

**Step 4c — Handle the answer string:**
- Exactly `Approve — append steps and mark ready` → proceed to Step 5.
- Exactly `Cancel — discard changes` → stop immediately. Do not call `mktemp`, do not write any files, do not run `gh issue edit`. Briefly confirm to the user that the changes were discarded.
- **Anything else** (the user selected "Other" and typed text) → treat the returned string as revision instructions. Update the drafted steps, then **re-post the full display template from Step 4a again** with the updated content — not a diff, not a "here's what I changed" summary, not a partial block. Then call `AskUserQuestion` again with the exact same parameters from Step 4b. Repeat until the user selects Approve or Cancel.

### Step 5: Append steps and update labels

**Precondition:** Do not start this step until the user has explicitly approved the new steps in Step 4. The `mktemp`, `Write(/tmp/**)`, and `gh issue edit` commands are auto-approved by this skill's `allowed-tools` frontmatter, which is why the review gate is non-optional.

Follow this exact three-step procedure — do not improvise, and do not combine steps.

**Step 5a — Generate a unique temp path (Bash tool):**

```zsh
mktemp -u /tmp/rework-body.XXXXXXXX
```

This prints a unique path (e.g. `/tmp/rework-body.aB3xZ9qP`) **without** creating the file on disk. Capture the exact path from stdout and use it verbatim in Steps 5b and 5c. Do not edit, rename, or add an extension to it.

**Step 5b — Write the updated issue body (Write tool, not Bash):**

Use the Claude Code `Write` tool with:
- `file_path`: the exact path printed by Step 5a
- `content`: the full updated issue body (existing body + new steps, verbatim — no shell escaping, no backslash escapes)

Because `mktemp -u` does not create the file, `Write` can create it fresh without needing a prior `Read`. The Write tool passes `content` through the tool-call JSON channel, so backticks, `$`, `%`, code fences, and embedded `EOF` markers all pass through untouched.

**Step 5c — Update the issue (Bash tool):**

```zsh
gh issue edit <number> --repo <origin-repo> --body-file "<path from Step 5a>"
```

**Step 5d — Transition labels:**

```zsh
gh issue edit <number> --repo <origin-repo> --remove-label "status:done" --remove-label "status:needs-attention" --remove-label "status:in-progress" --remove-label "status:blocked" --add-label "status:ready"
```

Report: "Updated issue #N with X new tasks, marked ready."

### Do NOT

Every item below is a real failure mode that has been observed. None of them are acceptable substitutes for the three-step procedure above:

- **Do not use heredocs** (`cat <<EOF > "$f"` / `cat <<'EOF'`). Unquoted `EOF` causes shell expansion of `$` and backticks inside the body; the delimiter can collide with content; and trailing-newline handling is fragile.
- **Do not use `echo` or `printf`** to emit the body. Newlines, `%`, and backslashes misbehave across shells and `echo` variants.
- **Do not pass `--body "<inline string>"`**. Shell quoting of multi-line markdown is a guaranteed breakage on issue content.
- **Do not use a fixed temp path** like `/tmp/rework-body.md`. Multiple concurrent `/rework` invocations will clobber each other.
- **Do not put `X`s anywhere except the end of the `mktemp` basename** (e.g. never `mktemp /tmp/foo-XXXXXX.md`). On macOS (BSD `mktemp`), trailing characters after the `X`s break the placeholder — `mktemp` may fail or literally create a file named `foo-XXXXXX.md`, defeating uniqueness. `gh issue edit --body-file` does not care about file extensions, so drop the `.md`.
- **Do not omit `-u` from `mktemp`**. Without `-u`, `mktemp` creates an empty file, which then forces the `Write` tool to require a prior `Read` before overwriting. `-u` generates the name without creating the file, which is what Step 5b needs.
- **Do not write the temp file before the review gate passes.** The whole point of the gate is that Cancel is side-effect-free.
- **Do not try to combine Steps 5a and 5b in a single Bash command** (e.g. `mktemp -u ... | xargs ...`). Keep them as two explicit tool calls so the path is captured cleanly.
- **Do not pipe `git remote get-url` into `sed` or `awk`** for repo resolution. Parse the raw URL yourself (see Repo Resolution above).

## Edge Cases

- **No spec issue found**: If git config has no issue and no number was provided, ask the user for the issue number. If they can't provide one, stop.
- **Issue not a spec**: Warn but allow the user to proceed if they confirm.
- **No review context and no user input**: Ask the user to describe what needs reworking before proceeding.
- **Issue already has `status:ready`**: Still append the steps — the issue may have been re-readied for other reasons. Skip the label transition.

$ARGUMENTS
