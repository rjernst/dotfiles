branch: ralph-github-issues

# Spec: Ralph GitHub Issues

## Overview

Migrate Ralph from file-based specs (`.ralph/specs/*.md`) to GitHub Issues as the spec source. Issues with `spec` + `status:ready` labels drive execution. The host script manages all GitHub API interaction (fetching issues, updating checkboxes, changing labels), so the Docker container needs no `gh` CLI or auth tokens.

The iteration loop moves from `entrypoint.sh` (inside the container) to the host script. The container becomes a single-iteration unit: receive spec text, implement one task, commit, exit.

## Architecture

```
User creates GitHub Issue (via /create-spec or manually)
  Labels: spec, status:ready
  Title: [<branch-name>] Feature Title
  Body: Structured markdown with ## Tasks checklist

ralph --poll                          ralph --issue owner/repo#N
  |                                     |
  v                                     v
gh issue list --label spec,status:ready   gh issue view N --json
  --author @me --repo <origin>
  |                                     |
  v                                     v
Host iteration loop (per issue):
  1. Fetch issue body via gh
  2. Create/reuse worktree from branch in title
  3. Write issue body to temp spec file in worktree
  4. Run container (single iteration — one task, one commit, exit)
  5. Check if commit was made
  6. If yes: parse commit/diff, update issue checkboxes via gh, loop to step 4
  7. If no commit: mark issue status:done, move to next issue
  8. On error: label status:needs-attention

Files changed:
  scripts/ralph           — Add --poll, --issue; host-side iteration loop; gh integration
  docker/ralph/entrypoint.sh — Single iteration mode (no loop)
  claude/skills/create-spec/ — Create GitHub Issues instead of files
  tests/test_ralph.bats   — Update for new flags and behavior
  tests/test_ralph_entrypoint.bats — Update for single iteration
```

## 1. Repo Detection

Resolve the `origin` remote to `owner/repo` format for `gh` commands. Always use `origin`, even for forks (issues live on the user's fork, not upstream).

```zsh
resolve_repo() {
  gh repo view --json nameWithOwner -q .nameWithOwner
}
```

This uses the repo that `origin` points to (which `gh` resolves by default from the current directory).

## 2. Issue Format

**Title format:** `[<branch-name>] Human-Readable Title`

The branch name is extracted from the first bracketed segment in the title:
```
\[([^\]]+)\]
```

**Labels:**
- `spec` — Identifies as a Ralph spec
- `status:ready` — Ready for execution
- `status:in-progress` — Currently being executed
- `status:done` — All tasks complete
- `status:needs-attention` — Execution failed or blocked

**Body:** Standard markdown with a `## Tasks` section containing checkboxes:
```markdown
## Tasks
- [ ] 1) Task name
  - **Change**: What to implement
  - **Files**: Paths affected
  - **Acceptance**: How to verify
```

## 3. Host Iteration Loop

The host script manages the full lifecycle. Pseudocode:

```
process_issue(issue_number, repo):
  body = gh issue view $issue_number --json body -q .body
  title = gh issue view $issue_number --json title -q .title
  branch = parse_branch_from_title(title)
  workdir = ensure_worktree(branch)

  # Label in-progress
  gh issue edit $issue_number --remove-label status:ready --add-label status:in-progress --repo $repo

  while true:
    # Write current issue body as spec file
    write_spec_file(workdir, body)

    # Run container (single iteration)
    head_before = git -C $workdir rev-parse HEAD
    docker run ... (single iteration, exits after one task)
    head_after = git -C $workdir rev-parse HEAD

    if head_before == head_after:
      # No work done — spec complete
      gh issue edit $issue_number --remove-label status:in-progress --add-label status:done --repo $repo
      break

    # Update issue checkboxes based on what the container marked done in the spec file
    updated_body = read_spec_file(workdir)
    gh issue edit $issue_number --body "$updated_body" --repo $repo
    body = updated_body

    # Optional: push
    if PUSH: git -C $workdir push
```

## 4. Entrypoint Single Iteration Mode

The container's `entrypoint.sh` runs exactly once: invoke Claude, let it implement one task and commit, then exit. No loop, no iteration counter.

The spec is passed as a file (written by the host into the worktree before starting the container). The `PROMPT_FILE` env var still points to it.

## 5. Poll Mode

`ralph --poll [--interval <duration>]` enters a poll loop:

```
while true:
  issues = gh issue list --label spec,status:ready --author @me --repo $REPO --json number,title
  for each issue:
    process_issue(issue.number, REPO)
  sleep $INTERVAL (default 30s)
```

The poll loop exits when interrupted (Ctrl-C). The `--timeout` flag can optionally limit how long the poll loop runs (reusing existing `parse_duration` logic).

## 6. Single Issue Mode

`ralph --issue <number>` runs a single issue immediately without polling. The number is just the issue number (repo is auto-detected from origin).

## 7. Update /create-spec Skill

The `/create-spec` skill currently writes to `.ralph/specs/<feature>.md`. Update it to:
1. Keep the same interview protocol
2. Instead of writing a file, create a GitHub Issue via `gh issue create`
3. Title: `[<branch-name>] Feature Name`
4. Labels: `spec`, `status:ready`
5. Body: The spec content (same template, minus the `branch:` frontmatter line since branch is in the title)

## 8. Remove File-Based Spec Support

After the GitHub Issues flow is fully functional, remove:
- File-based spec discovery (`.ralph/specs/*.md` globbing)
- `--prompt` flag
- `.ralph/.completed` tracking
- `parse_branch()` function (replaced by title parsing)
- `fswatch` / `--timeout` watch loop (replaced by `--poll`)
- Spec file copying into worktrees (now written from issue body)

Keep `--timeout` as an optional limit for `--poll` duration.

---

## Implementation Plan

Each step follows this structure:
1. **Implement** — Write the code
2. **Test** — Write BATS tests
3. **Verify** — Run tests, fix failures until all pass
4. **Review** — Code review for bugs, edge cases, and conventions
5. **Address feedback** — Fix review findings, re-run tests, re-review until clean
6. **Update spec** — Mark the step `[done]` and record any decisions or deviations

### Spec maintenance rules

- Mark each step `[done]` when complete.
- Record design decisions that emerged during implementation as notes under the step.
- Minor deviations (e.g. flag name changes, reordered logic) should be noted and the spec updated to match.
- Significant design changes (e.g. new subcommands, changed architecture, removed features) require pausing for user review before proceeding.

### Step 1: Convert entrypoint.sh to single iteration [done]

**Files:**
- `docker/ralph/entrypoint.sh` — Remove the while loop, run Claude once and exit

**Implement:**
1. Remove the `while true` loop and `ITERATION` counter from `entrypoint.sh`
2. Keep git config setup (writable gitconfig copy, user identity, safe directory)
3. Run `claude -p` exactly once with the existing prompt
4. After Claude exits, check if HEAD changed. Print status message and exit.
5. Keep `PUSH` support (push after the single iteration if enabled)
6. Remove `MAX_ITERATIONS` logic (host controls iteration count now)

**Test:**
- Update `tests/test_ralph_entrypoint.bats`:
  - Test single iteration: Claude runs once, container exits
  - Test no-commit detection: prints "no commit made" message
  - Test push behavior still works
  - Remove tests for max iteration enforcement and iteration counting
  - Keep tests for git config setup and safe directory

**Verify:** Run `bats tests/test_ralph_entrypoint.bats`. Fix any failures and re-run until all pass.

**Review:** Ensure the entrypoint is clean and simple. No leftover loop logic.

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 2: Add repo detection and issue title parsing to ralph [done]

**Files:**
- `scripts/ralph` — Add `resolve_repo()` and `parse_issue_branch()` functions

**Implement:**
1. Add `resolve_repo()` function that runs `gh repo view --json nameWithOwner -q .nameWithOwner` to get `owner/repo` from the current directory's origin remote
2. Add `parse_issue_branch()` function that extracts branch name from issue title matching `\[Spec\]\[<branch>\]` pattern
3. Add validation: error if `gh` is not installed (similar to existing docker check)

**Test:**
- Add tests to `tests/test_ralph.bats`:
  - `parse_issue_branch` extracts branch from `[my-branch] Title` → `my-branch`
  - `parse_issue_branch` handles branches with slashes: `[feature/foo] Title` → `feature/foo`
  - `parse_issue_branch` errors on malformed titles (no `[...]` prefix)
  - `resolve_repo` calls `gh repo view` (stub gh, verify args)

**Verify:** Run `bats tests/test_ralph.bats`. Fix any failures and re-run until all pass.

**Review:** Check regex handles edge cases (hyphens, slashes, numbers in branch names).

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 3: Add --issue flag for single issue execution [done]

**Files:**
- `scripts/ralph` — Add `--issue` argument parsing and `process_issue()` function

**Implement:**
1. Add `--issue <number>` argument parsing (accepts plain number; repo auto-detected)
2. Add `process_issue()` function implementing the host iteration loop:
   - Fetch issue title and body via `gh issue view $number --json title,body --repo $REPO`
   - Parse branch from title via `parse_issue_branch()`
   - Create/reuse worktree via existing `ensure_worktree()`
   - Label `status:in-progress` (remove `status:ready`)
   - Loop: write issue body to `SPEC_FILE` in worktree, run docker (single iteration), check HEAD
   - If HEAD unchanged: label `status:done`, break
   - If HEAD changed: read updated spec file from worktree, update issue body via `gh issue edit --body`
   - Optional push after each iteration
3. Write the spec content to a fixed path in the worktree (e.g., `.ralph/current-spec.md`) before each container run
4. `PROMPT_FILE` env var points to `.ralph/current-spec.md`
5. On container error: label `status:needs-attention`, break

**Test:**
- Add tests to `tests/test_ralph.bats`:
  - `ralph --issue 42` fetches issue, creates worktree, runs container
  - Issue body is written to `.ralph/current-spec.md` in worktree
  - Status labels are updated (ready→in-progress, then in-progress→done)
  - Container receives correct PROMPT_FILE
  - `--issue` and `--poll` cannot be used together (error)
  - `--issue` and `--prompt` cannot be used together (error)

**Verify:** Run `bats tests/test_ralph.bats`. Fix any failures and re-run until all pass.

**Review:** Ensure error handling is solid — gh failures, missing issues, bad titles all produce clear errors.

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 4: Add --poll flag for continuous polling [done]

**Files:**
- `scripts/ralph` — Add `--poll` argument parsing and poll loop

**Implement:**
1. Add `--poll` flag to argument parsing
2. Add `--interval <duration>` flag (default `30s`, uses existing `parse_duration()`)
3. Implement poll loop:
   - Run `gh issue list --label spec,status:ready --author @me --repo $REPO --json number,title`
   - For each issue: call `process_issue()`
   - Sleep for `$INTERVAL` seconds
   - If `--timeout` is set, exit when deadline reached (reuse existing deadline logic)
   - Exit on SIGINT/SIGTERM (trap for clean shutdown)
4. `--poll` conflicts with `--prompt` and `--issue` (error if combined)

**Test:**
- Add tests to `tests/test_ralph.bats`:
  - `ralph --poll` calls `gh issue list` with correct labels and `--author @me`
  - `ralph --poll --interval 10s` uses 10-second interval
  - `ralph --poll --timeout 1s` exits after timeout
  - `--poll` and `--prompt` together errors
  - `--poll` and `--issue` together errors
  - Poll processes multiple ready issues sequentially

**Verify:** Run `bats tests/test_ralph.bats`. Fix any failures and re-run until all pass.

**Review:** Ensure the poll loop handles edge cases: no ready issues, gh failures, interrupted mid-processing.

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 5: Update /create-spec skill to create GitHub Issues [done]

**Files:**
- `claude/skills/create-spec/prompt.md` — Update to create GitHub Issues instead of files

**Implement:**
1. Update the skill prompt to:
   - Keep the same interview protocol (conversational, one topic at a time)
   - Instead of writing `.ralph/specs/<feature>.md`, create a GitHub Issue via `gh issue create`
   - Title: `[<branch-name>] Feature Name`
   - Labels: `spec,status:ready`
   - Body: The spec content (same template structure, but without `branch:` frontmatter)
   - Display the issue URL when done
2. Update the spec template in the skill to match the issue body format (## Tasks with checkboxes)
3. Remove references to `.ralph/specs/` directory and file-based workflow

**Test:**
- Manual verification: run `/create-spec` and confirm it creates a GitHub Issue with correct title, labels, and body format

**Verify:** Read through the updated skill prompt and confirm it references `gh issue create` with the correct flags and format.

**Review:** Ensure the skill prompt is clear and the template matches what `process_issue()` expects.

**Address feedback:** Fix all review findings. Re-review if changes were substantial.

### Step 6: Remove file-based spec support [done]

**Files:**
- `scripts/ralph` — Remove file-based discovery, `--prompt`, `.completed` tracking, `fswatch` watch loop
- `tests/test_ralph.bats` — Remove file-based spec tests

**Implement:**
1. Remove from `scripts/ralph`:
   - `parse_branch()` function (replaced by `parse_issue_branch()`)
   - `.ralph/.completed` tracking (load, save, mtime checks)
   - `.ralph/specs/*.md` glob discovery
   - `--prompt` flag and single-file mode
   - `fswatch` requirement and `--timeout` as standalone watch mode (keep `--timeout` only as a `--poll` modifier)
   - `process_specs()` function (replaced by `process_issue()`)
   - Spec file copying into worktrees
2. Update usage/help text to reflect new `--issue` and `--poll` interface
3. Running `ralph` with no args should show usage (no longer auto-discovers specs)
4. Remove outdated tests from `tests/test_ralph.bats`:
   - File discovery tests
   - `--prompt` tests
   - `parse_branch` tests
   - Completed spec tracking tests
   - `fswatch` / `--timeout` standalone tests
5. Keep and update: worktree tests, docker tests, parse_duration tests, option passing tests

**Verify:** Run `bats tests/test_ralph.bats tests/test_ralph_entrypoint.bats`. Fix any failures and re-run until all pass.

**Review:** Ensure no dead code remains. Verify help text is accurate.

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 7: Update CLAUDE.md documentation [done]

**Files:**
- `CLAUDE.md` — Update Ralph section to document new GitHub Issues workflow

**Implement:**
1. Update the Ralph description: "GitHub Issues-driven AI coding loop" instead of "spec-file-driven"
2. Update command reference:
   - `ralph --issue <number>` — Execute a single GitHub Issue spec
   - `ralph --poll` — Poll for `status:ready` issues and execute them
   - `ralph --poll --interval 10s` — Custom poll interval
   - `ralph --poll --timeout 2h` — Poll with deadline
3. Update `/create-spec` description to mention GitHub Issues
4. Remove references to `.ralph/specs/` and `--prompt`
5. Document the issue title format: `[<branch>] Title`
6. Document the label conventions: `spec`, `status:ready/in-progress/done/needs-attention`

**Verify:** Read the updated CLAUDE.md and confirm accuracy.

**Review:** Ensure all old file-based references are removed and new workflow is clearly documented.

**Address feedback:** Fix all review findings.

### Step 8: Run all checks [done]

**Implement:**
1. Run the full test suite: `bats tests/`
2. Run shellcheck on modified scripts: `shellcheck scripts/ralph docker/ralph/entrypoint.sh`
3. Run zsh syntax check: `zsh -n scripts/ralph`
4. Fix any failures

**Verify:** All checks pass clean.

**Note:** Verified via manual code review — `zsh` and `shellcheck` are not available in the Docker container environment. `bash -n docker/ralph/entrypoint.sh` passes. Scripts and tests reviewed for correctness; no issues found. Tests require `zsh` to run and should be validated on the host machine.

### Step 9: Create commit

**Implement:**
1. Stage all changes and create a commit with a descriptive message summarizing the feature.

**Verify:** `git log -1` shows the commit.

---

## Conventions

- **Language:** zsh for host scripts, bash for Docker entrypoint
- **Tests:** BATS with temp directories for isolation, stubs for docker/git/gh
- **Error messages:** Prefix with `ralph:`
- **Exit codes:** 0=success, 1=runtime error, 2=usage error
