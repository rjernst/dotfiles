branch: add-merge-skill

# Spec: Merge Skill

## Overview

A Claude Code `/merge` skill that intelligently merges worktree branches back into their base branch. The skill detects context automatically:

1. **Inside a worktree**: Determines the base branch (the branch this worktree was forked from) and merges into it via `ta wt merge`.
2. **On a base branch** (main, master, or any branch that exists on `upstream`/`origin`): Presents a list of worktrees using `ta wt status --json`, lets the user pick one, and merges it.

This also requires updating `ta wt merge` to support a `--target <branch>` flag, since it currently hardcodes `main` as the merge target.

## Architecture

```
claude/skills/merge/          # New skill
  SKILL.md                    # Merge skill prompt

scripts/ta-wt                 # Existing — update merge subcommand
  cmd_merge()                 # Add --target flag, update _ahead_behind and _classify_status

tests/test_ta_wt.bats         # Existing — add merge --target tests
```

**Skill workflow:**
```
/merge invoked
  ├── Detect: am I in a worktree? (git worktree list --porcelain, compare cwd)
  │
  ├─ YES (in a worktree):
  │    ├── Determine current branch
  │    ├── Determine base branch (see Base Branch Detection below)
  │    ├── Run: ta wt merge --target <base-branch> <current-branch>
  │    └── Report result
  │
  └─ NO (on a base branch):
       ├── Run: ta wt status --json
       ├── Filter to branches whose base is the current branch
       ├── Present list to user (branch, status, ahead count, dirty status)
       ├── User picks one (or multiple)
       └── Run: ta wt merge --target <current-branch> <selected-branch>
```

**Base branch detection logic:**
```
1. If `upstream` remote exists (fork workflow):
   - List branches on upstream: git ls-remote --heads upstream
   - A branch is a "base branch" if it exists on upstream
   - The base branch for a worktree is found via:
     git merge-base --is-ancestor <upstream-branch> <worktree-branch>
     Pick the closest ancestor (fewest commits between them)

2. If only `origin` remote exists (personal project):
   - Only main (or master) is considered a base branch
   - The base branch is always main/master
```

---

## 1. `ta wt merge --target <branch>`

Update the existing `cmd_merge` in `scripts/ta-wt` to accept a `--target` flag specifying which branch to merge into (defaults to `main` for backwards compatibility).

### Changes

- Add `--target <branch>` option parsing to `cmd_merge()`
- Replace hardcoded `"main"` references with the target variable
- Update the worktree lookup to find the target branch's worktree (not hardcoded "main")
- Update `_ahead_behind()` to accept an optional second argument for the comparison branch (default: `main`)
- Update `_classify_status()` to accept an optional third argument for the base branch (default: `main`)
- Update the error messages to reference the target branch name instead of "main"
- Update the success message: `ta: merged '<branch>' into <target> (<hash>)`
- Update usage: `usage: ta wt merge <branch> [--target <branch>]`

### Validation

- Target branch must have a worktree checked out
- Target worktree must be clean
- Source branch must have a worktree checked out
- Source worktree must be clean

---

## 2. `/merge` Claude Code Skill

Create `claude/skills/merge/SKILL.md` — a skill prompt that guides Claude through the merge workflow.

### Behavior when inside a worktree

1. Run `git worktree list --porcelain` and `pwd` to determine if the current directory is inside a worktree (not the main/base checkout)
2. Determine the current branch: `git branch --show-current`
3. Detect the base branch:
   - Check if `upstream` remote exists: `git remote`
   - If upstream exists: `git ls-remote --heads upstream` to get upstream branch names, then for each upstream branch, check `git merge-base --is-ancestor <upstream-branch> <current-branch>`. Pick the closest ancestor (use `git rev-list --count <upstream-branch>..<current-branch>` — smallest count wins)
   - If only origin: use `main` (or `master` if main doesn't exist)
4. Confirm with the user: "Merge `<branch>` into `<target>`?"
5. Run `ta wt merge --target <target> <branch>`
6. Report the result

### Behavior when on a base branch

1. Detect current branch and confirm it's a base branch (exists on upstream, or is main/master for origin-only repos)
2. Run `ta wt status --json` to get all worktrees
3. Filter to worktrees that are not the current branch
4. Present the list showing: branch name, status (ready/wip/almost/conflict/merged), ahead count, dirty status
5. Ask the user which branch(es) to merge
6. For each selected branch, run `ta wt merge --target <current-branch> <selected-branch>`
7. Report results

### Edge cases

- If not in a worktree AND not on a base branch: inform the user and suggest switching to a base branch
- If no worktrees exist to merge: inform the user
- If selected worktree has status "wip" or "conflict": warn the user before proceeding
- If `ta wt merge` fails (conflicts, dirty state): report the error clearly

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

### Step 1: Add `--target` flag to `ta wt merge` [done]

**Files:**
- `scripts/ta-wt` — Update `cmd_merge()` to accept `--target`

**Implement:**
1. Add `--target` option parsing in `cmd_merge()` with default value `main`
2. Replace all hardcoded `"main"` in `cmd_merge()` with the target variable
3. Update the worktree lookup to find the target branch's worktree path (currently hardcoded to find `"main"`)
4. Update error messages to reference the target branch name
5. Update the usage line for merge subcommand

**Test:**
- Existing merge tests still pass (backwards compatible, default target is `main`)

**Verify:** Run `bats tests/test_ta_wt.bats`. Fix any failures and re-run until all pass.

**Review:** Check that all hardcoded "main" references in cmd_merge are replaced. Ensure backwards compatibility.

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 2: Add BATS tests for `--target` flag [done]

**Files:**
- `tests/test_ta_wt.bats` — Add merge --target tests

**Implement:**
1. Add test: `wt merge --target merges into specified branch` — Create a base branch (e.g. `8.x`) with a worktree, create a feature branch worktree off it, merge feature into `8.x`
2. Add test: `wt merge --target nonexistent target fails` — Target branch has no worktree
3. Add test: `wt merge --target dirty target is refused` — Target worktree has uncommitted changes
4. Add test: `wt merge --target defaults to main` — Verify explicit `--target main` behaves same as no flag

**Test:**
- All new tests pass along with existing merge tests

**Verify:** Run `bats tests/test_ta_wt.bats`. Fix any failures and re-run until all pass.

**Review:** Ensure test isolation — each test sets up its own branches/worktrees and doesn't depend on other tests.

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 3: Update `_ahead_behind` and `_classify_status` to support custom base branch [done]

**Files:**
- `scripts/ta-wt` — Update `_ahead_behind()` and `_classify_status()` helpers

**Implement:**
1. Add optional second parameter to `_ahead_behind()`: `local base="${2:-main}"`, replace hardcoded `main` in the rev-list command
2. Add optional third parameter to `_classify_status()`: `local base="${3:-main}"`, replace hardcoded `main` in merge-base and merge-tree calls
3. All existing callers pass no extra argument, so behavior is unchanged

**Test:**
- Existing status tests still pass

**Verify:** Run `bats tests/test_ta_wt.bats`. Fix any failures and re-run until all pass.

**Review:** Ensure no existing callers are broken by the parameter addition.

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 4: Create `/merge` skill [done]

**Files:**
- `claude/skills/merge/SKILL.md` — New skill file

**Implement:**
1. Write the SKILL.md following the pattern in `claude/skills/code-review/SKILL.md`
2. Include the full workflow logic:
   - Worktree detection via `git worktree list --porcelain` + `pwd`
   - Base branch detection (upstream vs origin-only)
   - Direct merge path (in worktree) with user confirmation
   - Chooser path (on base branch) with `ta wt status --json` output
   - Edge case handling (not in worktree and not on base branch, no worktrees, wip/conflict warnings)
3. Include `$ARGUMENTS` placeholder at the end (matching existing skill convention)

**Test:**
- Manual: verify file exists at `claude/skills/merge/SKILL.md`
- Verify the skill references correct `ta wt` commands

**Verify:** File exists and is valid markdown. Check that all referenced commands (`ta wt merge --target`, `ta wt status --json`, `git worktree list --porcelain`, `git ls-remote --heads`) are correct.

**Review:** Review for completeness — does the prompt cover all cases? Is the base branch detection logic clear enough for the agent to follow?

**Address feedback:** Fix all review findings.

### Step 5: Update `setup` to symlink the merge skill [done]

**Note:** The `setup` script already symlinks the entire `claude/skills` directory to `~/.claude/skills` generically, so the merge skill is automatically included. No changes to `setup` were needed.

**Files:**
- `setup` — Add symlink for the merge skill directory (if not already handled generically)

**Implement:**
1. Check if `setup` already handles symlinking all skills generically (glob pattern) or if each skill needs explicit symlinking
2. If explicit: add symlink for `claude/skills/merge` to `~/.claude/skills/merge`
3. If generic: verify the merge skill will be picked up automatically

**Test:**
- Run `./setup` and verify `~/.claude/skills/merge/SKILL.md` exists as a symlink

**Verify:** `ls -la ~/.claude/skills/merge/SKILL.md` shows symlink to repo path.

**Review:** Ensure symlink path is correct.

**Address feedback:** Fix all review findings.

### Step 6: Run all checks [done]

**Note:** `bats` and `zsh` were not available in the environment and had to be installed from source (`bats-core` via git, `zsh` via romkatv/zsh-bin). Three pre-existing test failures were fixed: test 48 (conflict scenario didn't actually create a conflict — fixed by diverging main after branch creation), tests 49 and 50 (used `--allow-empty` commits that produced nothing to squash-merge — fixed by adding real file changes). The commit message format was also changed from `--no-edit` (which omitted the branch name) to an explicit `-m "Squashed '<branch>' into <target>"` message so the squash commit message includes the branch name as test 50 expected.

**Implement:**
1. Run the full test suite: `bats tests/`
2. Run shellcheck on modified scripts: `shellcheck scripts/ta-wt`
3. Run zsh syntax check: `zsh -n scripts/ta-wt`
4. Fix any failures

**Verify:** All checks pass clean.

### Step 7: Create commit [done]

**Implement:**
1. Stage all changes and create a commit with message: `Add /merge skill with ta wt merge --target support`

**Verify:** `git log -1` shows the commit.

**Note:** The final commit included remaining uncommitted changes: CLAUDE.md documentation updates, hosts/pandora/brewfile (poppler), scripts/ralph (local-only repo support), setup (generic scripts symlinking), and tests/test_setup.bats (tests for setup changes).

---

## Conventions

- **Language:** zsh for all scripts
- **Tests:** BATS framework with temp git repos for isolation
- **Error messages:** Prefix with `ta:` (matching existing ta-wt convention)
- **Exit codes:** 0=success, 1=runtime error, 2=usage error
