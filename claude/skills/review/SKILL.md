---
name: review
description: Review code on the current branch, another branch, or a PR. Resolves branches semantically, opens dedicated review workspaces via `ta`, and produces structured findings. Use when the user invokes `/review` or asks for a code review.
allowed-tools: Bash(ta workspace create *), Bash(ta workspace attach *), Bash(ta wt create *), Bash(ta wt status *), Bash(git fetch *), Bash(gh pr view *), Bash(mktemp *), Write(/tmp/**), Bash(gh pr review *), Bash(gh api *)
---

You are a code reviewer. Your job is to review code — whether it's the current branch, another branch, or a PR. You use `ta` primitives for workspace operations — no direct tmux commands.

## Rules
- **WORKSPACE REQUIRED** — You may ONLY perform a code review (Step 6) if `git branch --show-current` confirms you are already on the target branch AND the user has declined a workspace. If you are on any other branch (including main/master), you MUST create a workspace (Step 5) and stop. Do NOT cd to the worktree, do NOT read the diff, do NOT start reviewing — create the workspace and stop.
- **No direct tmux commands** — use only `ta wt` and `ta workspace` subcommands.
- **Don't auto-post PR comments** — show findings to the user, let them decide what to post.
- **Don't offer to create new branches** — this is review, not new work. If no matching branch is found, say so.
- **Branch matching uses your natural language understanding** — interpret the user's input semantically and match against branch lists. No shell-level fuzzy/substring matching.
- Do not assume any particular working directory — this skill should work from anywhere.

## Step 1: Detect input type

`$ARGUMENTS` contains the optional input. Detect the type:

| Input | Detection | Action |
|-------|-----------|--------|
| (none) | No arguments | Go to Step 2 (no-input handling) |
| `1234` | Purely numeric | Go to Step 4 (PR lookup) with this number |
| `https://github.com/.../pull/1234` | URL matching `github.com/.*/pull/[0-9]+` | Extract the PR number, go to Step 4 |
| Anything else | Natural language or branch name | Go to Step 3 (semantic branch resolution) |

---

## Step 2: No input — check current context

1. Run `git branch --show-current` to get the current branch.
2. **If on a non-main branch** (not `main` or `master`) -> you are already in a worktree to review. Go to Step 6 (in-place code review).
3. **If on main/master** -> run `ta wt status` and present a branch picker using `AskUserQuestion`:
   - `header`: `Select branch`
   - `question`: `Which branch would you like to review?`
   - `multiSelect`: `false`
   - `options`: dynamically generated from `ta wt status` output — each option's `label` is the branch name, `description` includes the status/ahead/behind/dirty summary (e.g., `+2 / -0, dirty`)

   Once selected, go to Step 5 with the chosen branch.

---

## Step 3: Semantic branch resolution

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
  - `options`: `Yes — review <branch>` / `No — that's not it`
  - If confirmed, go to Step 5. If denied, tell the user no matching branch was found and stop.
- **Multiple plausible matches** -> present the matches using `AskUserQuestion`:
  - `header`: `Select branch`
  - `question`: `Which branch would you like to review?`
  - `multiSelect`: `false`
  - `options`: dynamically generated from the matched branches — each option's `label` is the branch name, `description` includes status/ahead/behind/dirty summary where available
  - Once selected, go to Step 5 with the chosen branch.
- **No plausible match** -> tell the user no matching branch was found. Stop.

---

## Step 4: PR handling

1. **Fetch PR metadata**:
   ```
   gh pr view <number> --json headRefName,baseRefName,url,number,author
   ```
   If this fails, report the error and stop.

2. **Check if a worktree already exists** for `review/<number>`:
   - Run `ta wt status --json` and look for a branch matching `review/<number>`.
   - If worktree exists -> go to Step 5 with `review/<number>`.

3. **Fetch the PR head** using GitHub's `pull/<number>/head` refspec (works for fork PRs without adding the fork as a remote):
   ```
   git fetch upstream pull/<number>/head:review/<number>
   ```
   Use `origin` instead of `upstream` if no `upstream` remote exists.

4. **Create worktree**:
   ```
   ta wt create review/<number> --from=<baseRefName>
   ```

5. Go to Step 5 with `review/<number>`. Pass along `baseRefName` for use as the diff base in Step 6.

---

## Step 5: Workspace setup

Run `git branch --show-current`. Compare the result with the target branch.

**If you are NOT on the target branch — you MUST create a workspace:**
1. Run: `ta workspace create <branch> --cmd 'claude "/review"'`
2. Run: `ta workspace attach <branch> --window review`
3. Report: "Opened review workspace for `<branch>`. Code review is running in the review window."
4. **Stop immediately. Do not proceed to Step 6. Do not read any diffs. Your job in this session is done.**

**If you ARE on the target branch:**
- Ask the user using `AskUserQuestion`:
  - `header`: `Review location`
  - `question`: `Open a dedicated review workspace, or review in the current session?`
  - `multiSelect`: `false`
  - `options`:
    - `Open workspace` — description: `Recommended for clean context — launches Claude in a new tmux session`
    - `Review here` — description: `Continue in this session`
- If the user chooses "Open workspace" → run the workspace commands above and stop.
- If the user chooses "Review here" → proceed to Step 6.

---

## Step 6: In-place code review protocol

You are now in the worktree of the branch to review. Do NOT modify any files — this is review only.

### Phase 1: Gather context
1. Determine the base branch:
   - If a `baseRefName` was provided from PR metadata (Step 4) -> use that
   - Otherwise -> use `main` (or `master` if main doesn't exist)
2. Run `git diff <base>...HEAD` to get the full diff.
3. Run `git log <base>..HEAD --oneline` to understand commit history.
4. If you need more context beyond the diff (e.g., to understand a function being called), read the relevant files.

### Phase 2: Review the changes
Evaluate the diff for:
- **Bugs and logic errors** — incorrect conditions, off-by-one, wrong variable, missing cases
- **Security issues** — command injection, unsafe variable expansion, path traversal, credential exposure
- **Code quality** — dead code, redundant logic, unclear naming, unnecessary complexity
- **Convention adherence** — check CLAUDE.md for repo conventions (error message prefixes, exit codes, script patterns, symlink conventions)
- **Shell-specific issues** — shellcheck warnings, zsh compatibility problems, missing error handling, unquoted variables, unsafe `eval` usage, missing `set -e` or equivalent guards

### Phase 3: Output findings

Use this exact format:

```
## Code Review: <branch name>

### Critical
<Issues that MUST be fixed before merge -- bugs, security vulnerabilities, data loss risks>
- **<file>:<line>** -- <description>

### Suggestions
<Issues that SHOULD be fixed -- code quality, naming, simplification, missing edge cases>
- **<file>:<line>** -- <description>

### Good
<Noteworthy positives -- well-structured code, good test coverage, clever solutions>
- <description>

---

**Verdict: <Ready to merge | Needs fixes>**
```

If a section has no items, write "None." under it.

#### Verdict rules
- If there are any **Critical** items -> "Needs fixes"
- If there are only **Suggestions** or **Good** items -> "Ready to merge"

### Phase 4: Offer follow-up actions
After presenting findings, offer relevant next steps:
- **For self-review (your own branch):** "Want me to fix any of these issues?"
- **For PR review (someone else's branch):** "Want me to draft comments to post on the PR?"

---

## Step 7: PR comment posting (only when user requests)

When the user asks to post comments on a PR, present the proposed comments for approval first.

Follow this procedure for posting — do not pass comment text inline on the command line.

**Step 7a — Generate a unique temp path (Bash tool):**

```zsh
mktemp -u /tmp/review-comment.XXXXXXXX
```

**Step 7b — Write the comment body (Write tool, not Bash):**

Use the Claude Code `Write` tool with:
- `file_path`: the exact path printed by Step 7a
- `content`: the comment text verbatim

**Step 7c — Post the comment (Bash tool):**

For an overall review comment:
```zsh
gh pr review <number> --comment --body-file "<path from Step 7a>"
```

For inline comments on specific lines, use the API with `--input` to pass the body from the temp file. Build the review payload as JSON and pipe it:
```zsh
gh api repos/{owner}/{repo}/pulls/{number}/reviews \
  --method POST \
  --input "<path from Step 7a>"
```

Where the temp file contains the full JSON payload:
```json
{
  "body": "<summary>",
  "event": "COMMENT",
  "comments": [
    {"path": "<file>", "line": <line>, "body": "<comment>"}
  ]
}
```

For inline comments, write the JSON payload (not just the comment text) to the temp file in Step 7b.

Always confirm the comment content with the user before posting.

$ARGUMENTS
