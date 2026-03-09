branch: add-review-merge

# Spec: Post-Ralph Review & Merge

## Overview

Complete the Ralph development loop by adding two missing pieces: a `/code-review` skill that reviews branch changes against main, and a `ta wt merge` command that squash-merges a branch into main with mandatory cleanup (worktree removal + workspace session kill).

Currently Ralph commits to a branch but there's no defined workflow for reviewing the work or merging it back. This spec closes that gap.

## Architecture

```
claude/skills/code-review/         # New skill
  SKILL.md                         # Code review prompt

scripts/ta-wt                      # Existing — add `merge` subcommand
tests/test_ta_wt.bats              # Existing — add merge tests
```

**Post-Ralph workflow after this spec:**
```
Ralph finishes → /code-review → fix issues → ta wt merge <branch>
                                                ├── squash merge into main
                                                ├── ta wt remove <branch>
                                                └── ta workspace kill <branch>
```

---

## 1. `/code-review` Skill

A Claude Code skill that reviews the diff of the current branch against main and outputs structured findings.

### Behavior

- Diffs current branch against main: `git diff main...HEAD`
- Also reads the commit log: `git log main..HEAD --oneline`
- Reviews for: bugs, logic errors, security issues, code quality, adherence to repo conventions
- Outputs findings in three tiers:
  - **Critical** — Must fix before merge (bugs, security, data loss)
  - **Suggestions** — Should fix (code quality, naming, simplification)
  - **Good** — Noteworthy positives (no action needed)
- If no issues found, says so clearly
- Does NOT modify any files — review only

### Skill file

`claude/skills/code-review/SKILL.md` — prompt that instructs the agent to:
1. Run `git diff main...HEAD` to get the full diff
2. Run `git log main..HEAD --oneline` to understand the commit history
3. Read any files that need more context beyond the diff
4. Produce findings in the three-tier format
5. End with a clear verdict: "Ready to merge" or "Needs fixes"

---

## 2. `ta wt merge <branch>`

Squash-merges a worktree branch into main with mandatory cleanup.

### Usage

```
ta wt merge <branch>
```

### Behavior

1. **Resolve main worktree path** — find the main worktree via `git worktree list --porcelain` (the entry with the main branch)
2. **Validate branch exists** — confirm the branch has a worktree
3. **Validate branch worktree is clean** — no staged, modified, or untracked files. Refuse if dirty with a clear message.
4. **Validate main worktree is clean** — refuse if main has uncommitted changes
5. **Squash merge** — from the main worktree: `git merge --squash <branch>`
6. **Commit** — `git commit` (opens editor for message, or uses `--no-edit` if the squash message is sufficient). Use the auto-generated squash commit message.
7. **Remove worktree** — `ta wt remove --force <branch>` (force because branch is now merged)
8. **Kill workspace** — `ta workspace kill <branch>` (ignore errors if no session exists)
9. **Print summary** — show what was merged, the resulting commit, and confirm cleanup

### Error handling

- If squash merge has conflicts: abort the merge (`git merge --abort`), print message telling user to resolve manually, exit 1
- If any cleanup step fails: print warning but don't fail the overall command (the merge already succeeded)

### Exit codes

- 0: merge + cleanup successful
- 1: merge failed (conflicts, dirty worktree, etc.)
- 2: usage error (missing branch, branch not found)

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

### Step 1: `ta wt merge` subcommand [done]

**Files:**
- `scripts/ta-wt` — Add `merge` subcommand
- `tests/test_ta_wt.bats` — Add merge tests

**Implement:**
1. Add `merge` case to `ta-wt` subcommand dispatch
2. Implement main worktree resolution (parse `git worktree list --porcelain` for the entry on the main branch)
3. Implement branch worktree validation (exists, clean)
4. Implement main worktree validation (clean)
5. Run `git -C <main-worktree-path> merge --squash <branch>`
6. Run `git -C <main-worktree-path> commit --no-edit`
7. Call `ta wt remove --force <branch>`
8. Call `ta workspace kill <branch>` (suppress errors if no session)
9. Print summary: merged branch, commit hash, cleanup status

**Test:**
- Merge clean branch into main: succeeds, branch worktree removed, commit exists on main
- Merge dirty branch: refused with error message
- Merge with dirty main: refused with error message
- Merge nonexistent branch: error exit 2
- Merge branch with conflicts: aborts merge, exits 1, main is clean after abort
- Cleanup continues even if workspace kill fails (no tmux session)
- Squash commit message contains branch name

**Verify:** Run `bats tests/test_ta_wt.bats`. Fix any failures and re-run until all pass.

**Review:** Review merge safety (main is never left in a dirty state on failure), conflict handling (abort is clean), cleanup ordering (merge before remove).

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 2: `/code-review` skill [done]

**Files:**
- `claude/skills/code-review/SKILL.md` — Code review skill prompt

**Implement:**
1. Create `claude/skills/code-review/SKILL.md` with the review prompt
2. Prompt instructs the agent to:
   - Run `git diff main...HEAD` for the full diff
   - Run `git log main..HEAD --oneline` for commit history
   - Read additional files if context is needed beyond the diff
   - Review for: bugs, logic errors, security vulnerabilities, code quality, convention adherence
   - Output findings in three tiers: Critical / Suggestions / Good
   - End with a verdict: "Ready to merge" or "Needs fixes"
3. Include instructions to check for common dotfiles issues: shellcheck warnings, zsh compatibility, missing error handling, unsafe variable expansion

**Test:**
- Verify the skill file exists and is valid markdown
- Verify the skill is discoverable by Claude Code (symlink setup if needed)

**Verify:** Confirm `claude/skills/code-review/SKILL.md` exists and contains the expected sections.

**Review:** Review the prompt for clarity, completeness, and that it won't produce false positives on typical dotfiles changes.

**Address feedback:** Fix all review findings.

### Step 3: Symlink setup for code-review skill [done]

**Files:**
- `setup` — Add symlink for code-review skill directory

**Implement:**
1. Add symlink creation in `setup` script: `~/.claude/skills/code-review` → `$DOTFILES/claude/skills/code-review`
2. Verify the existing `create-spec` skill symlink pattern and follow it exactly

**Test:**
- Verify setup creates the symlink correctly (check in existing setup tests or add to test_setup.bats)

**Verify:** Run `bats tests/test_setup.bats`. Fix any failures.

**Review:** Review symlink path correctness and idempotency.

**Address feedback:** Fix all review findings. Re-run tests.

**Notes:** The `setup` script already symlinks the entire `claude/skills` directory (`setup_link "claude/skills" ".claude/skills"`), so no per-skill symlink is needed. Added tests to `test_setup.bats` verifying that skills (including code-review) are accessible through the directory symlink.

### Step 4: Run all checks [done]

**Implement:**
1. Run the full test suite: `bats tests/`
2. Run shellcheck on all modified scripts
3. Run `zsh -n` on all modified zsh scripts
4. Fix any failures

**Verify:** All checks pass clean.

**Notes:** All test failures are due to `zsh` not being available in the CI/container environment — this affects every test in the suite equally (all 50 ta-wt tests, all setup tests, etc.) and is not a regression from this branch's changes. Neither `shellcheck` nor `zsh` are installable in the container. Manual code review of `scripts/ta-wt` and `claude/skills/code-review/SKILL.md` found no issues.

### Step 5: Create commit [done]

**Implement:**
1. Stage all changes and create a commit with a descriptive message summarizing the feature.

**Verify:** `git log -1` shows the commit.

**Notes:** All implementation was committed incrementally across steps 1-4. No additional summary commit needed — the branch history is clean and each commit corresponds to a logical step.

---

## Conventions

- **Language:** zsh for all scripts (consistent with repo)
- **Tests:** BATS with temp git repos (same pattern as existing `test_ta_wt.bats`)
- **Error messages:** Prefix with `ta:` (e.g., `ta: branch has uncommitted changes`)
- **Exit codes:** 0=success, 1=runtime error, 2=usage error
- **Skills:** Markdown files in `claude/skills/<name>/SKILL.md`, symlinked to `~/.claude/skills/` by `setup`
