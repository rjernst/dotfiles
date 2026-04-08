#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/ta and scripts/ta-wt (list subcommand)
# Uses temp git repos to simulate a project with worktrees.

setup() {
  TA="${BATS_TEST_FILENAME%/*}/../scripts/ta"
  TA_WT="${BATS_TEST_FILENAME%/*}/../scripts/ta-wt"

  export GIT_AUTHOR_NAME="Test"
  export GIT_AUTHOR_EMAIL="test@test.com"
  export GIT_COMMITTER_NAME="Test"
  export GIT_COMMITTER_EMAIL="test@test.com"

  # Disable commit signing for tests
  export GIT_CONFIG_GLOBAL="$BATS_TEST_TMPDIR/gitconfig"
  git config --global commit.gpgsign false
  git config --global tag.gpgsign false
  git config --global init.defaultBranch main

  # Create a bare "upstream" repo
  git init --bare "$BATS_TEST_TMPDIR/upstream.git"

  # Create the project repo
  export PROJECT="$BATS_TEST_TMPDIR/project"
  git init "$PROJECT"
  git -C "$PROJECT" commit --allow-empty -m "initial commit"
  git -C "$PROJECT" branch -M main
  git -C "$PROJECT" remote add upstream "$BATS_TEST_TMPDIR/upstream.git"
  git -C "$PROJECT" push upstream main
}

# --- ta dispatcher tests ---

@test "ta with no args shows usage" {
  command -v zsh >/dev/null 2>&1 || skip "requires zsh"
  run zsh "$TA"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "ta unknown command fails" {
  command -v zsh >/dev/null 2>&1 || skip "requires zsh"
  run zsh "$TA" bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown command"* ]]
}

@test "ta wt dispatches to ta-wt" {
  command -v zsh >/dev/null 2>&1 || skip "requires zsh"
  run zsh "$TA" wt
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage: ta wt"* ]]
}

# --- ta wt list tests ---

@test "wt list with no worktrees shows only main" {
  cd "$PROJECT"
  run "$TA_WT" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"main"* ]]
  [[ "$output" == *"BRANCH"* ]]
}

@test "wt list shows current for active worktree" {
  cd "$PROJECT"
  run "$TA_WT" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"current"* ]]
}

@test "wt list shows multiple worktrees" {
  cd "$PROJECT"
  # Create a feature branch with a commit ahead of main
  git checkout -b feature-a
  git commit --allow-empty -m "feature a work"
  git checkout main

  # Create worktree for it
  git worktree add "$BATS_TEST_TMPDIR/wt-feature-a" feature-a

  run "$TA_WT" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"main"* ]]
  [[ "$output" == *"feature-a"* ]]
}

@test "wt list shows dirty status" {
  cd "$PROJECT"
  git checkout -b dirty-branch
  git commit --allow-empty -m "dirty branch"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-dirty" dirty-branch

  # Make it dirty
  echo "modified" > "$BATS_TEST_TMPDIR/wt-dirty/newfile.txt"

  run "$TA_WT" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"dirty"* ]]
}

@test "wt list --json returns valid JSON" {
  cd "$PROJECT"
  run "$TA_WT" list --json
  [ "$status" -eq 0 ]

  # Validate JSON and check structure
  echo "$output" | jq . > /dev/null
  local count
  count="$(echo "$output" | jq length)"
  [ "$count" -ge 1 ]

  # Check required fields
  echo "$output" | jq -e '.[0] | has("branch", "status", "ahead", "behind", "path")'
}

@test "wt list --json with multiple worktrees" {
  cd "$PROJECT"
  git checkout -b json-branch
  git commit --allow-empty -m "json branch"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-json" json-branch

  run "$TA_WT" list --json
  [ "$status" -eq 0 ]

  local count
  count="$(echo "$output" | jq length)"
  [ "$count" -eq 2 ]
}

@test "wt list --full shows commit message" {
  cd "$PROJECT"
  run "$TA_WT" list --full
  [ "$status" -eq 0 ]
  [[ "$output" == *"LAST COMMIT"* ]]
  [[ "$output" == *"initial commit"* ]]
}

@test "wt list --json --full includes commit fields" {
  cd "$PROJECT"
  run "$TA_WT" list --json --full
  [ "$status" -eq 0 ]

  echo "$output" | jq -e '.[0] | has("commit_message", "commit_date")'
}

@test "wt list shows ahead/behind counts" {
  cd "$PROJECT"
  git checkout -b ahead-branch
  git commit --allow-empty -m "ahead 1"
  git commit --allow-empty -m "ahead 2"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-ahead" ahead-branch

  run "$TA_WT" list --json
  [ "$status" -eq 0 ]

  # The ahead-branch should be 2 ahead of main
  local ahead
  ahead="$(echo "$output" | jq '.[] | select(.branch == "ahead-branch") | .ahead')"
  [ "$ahead" -eq 2 ]
}

@test "wt list unknown option fails" {
  cd "$PROJECT"
  run "$TA_WT" list --bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown option"* ]]
}

# --- ta wt create tests ---

@test "wt create with explicit path" {
  cd "$PROJECT"
  local wt_dir="$BATS_TEST_TMPDIR/wt-created"

  # Push a branch to upstream so it exists on the remote
  git checkout -b feat-create
  git commit --allow-empty -m "feat create"
  git push upstream feat-create
  git checkout main
  git branch -D feat-create

  run "$TA_WT" create feat-create "$wt_dir"
  [ "$status" -eq 0 ]
  [[ "$output" == *"$wt_dir"* ]]
  [ -d "$wt_dir" ]
}

@test "wt create with default path" {
  cd "$PROJECT"

  # Push a branch to upstream
  git checkout -b feat-default
  git commit --allow-empty -m "feat default"
  git push upstream feat-default
  git checkout main
  git branch -D feat-default

  run "$TA_WT" create feat-default
  [ "$status" -eq 0 ]

  # Default path should be ../<repo-name>-<branch>
  [[ "$output" == *"project-feat-default"* ]]
}

@test "wt create with slashes in branch name generates correct path" {
  cd "$PROJECT"

  git checkout -b feature/slash-test
  git commit --allow-empty -m "slash test"
  git push upstream feature/slash-test
  git checkout main
  git branch -D feature/slash-test

  run "$TA_WT" create feature/slash-test
  [ "$status" -eq 0 ]
  [[ "$output" == *"project-feature-slash-test"* ]]
}

@test "wt create nonexistent branch fails" {
  cd "$PROJECT"

  run "$TA_WT" create nonexistent-branch
  [ "$status" -eq 1 ]
  [[ "$output" == *"not found on remote"* ]]
}

@test "wt create with --remote override" {
  cd "$PROJECT"

  # Add an 'origin' remote and push a branch there
  git remote add origin "$BATS_TEST_TMPDIR/upstream.git"
  git checkout -b feat-origin
  git commit --allow-empty -m "feat origin"
  git push origin feat-origin
  git checkout main
  git branch -D feat-origin

  local wt_dir="$BATS_TEST_TMPDIR/wt-origin"
  run "$TA_WT" create feat-origin "$wt_dir" --remote origin
  [ "$status" -eq 0 ]
  [ -d "$wt_dir" ]
}

@test "wt create remote fallback upstream then origin" {
  cd "$PROJECT"

  # Push a branch only to upstream
  git checkout -b feat-fallback
  git commit --allow-empty -m "feat fallback"
  git push upstream feat-fallback
  git checkout main
  git branch -D feat-fallback

  local wt_dir="$BATS_TEST_TMPDIR/wt-fallback"
  run "$TA_WT" create feat-fallback "$wt_dir"
  [ "$status" -eq 0 ]
  [ -d "$wt_dir" ]
}

@test "wt create falls back to origin when no upstream" {
  # Create a repo with only 'origin' remote
  local proj2="$BATS_TEST_TMPDIR/project2"
  git init "$proj2"
  git -C "$proj2" commit --allow-empty -m "init"
  git -C "$proj2" branch -M main
  git -C "$proj2" remote add origin "$BATS_TEST_TMPDIR/upstream.git"

  # Push a branch to origin
  git -C "$proj2" checkout -b feat-origin-only
  git -C "$proj2" commit --allow-empty -m "origin only"
  git -C "$proj2" push origin feat-origin-only
  git -C "$proj2" checkout main
  git -C "$proj2" branch -D feat-origin-only

  cd "$proj2"
  local wt_dir="$BATS_TEST_TMPDIR/wt-origin-only"
  run "$TA_WT" create feat-origin-only "$wt_dir"
  [ "$status" -eq 0 ]
  [ -d "$wt_dir" ]
}

@test "wt create existing path fails" {
  cd "$PROJECT"

  git checkout -b feat-exists
  git commit --allow-empty -m "feat exists"
  git push upstream feat-exists
  git checkout main
  git branch -D feat-exists

  mkdir -p "$BATS_TEST_TMPDIR/wt-exists"
  run "$TA_WT" create feat-exists "$BATS_TEST_TMPDIR/wt-exists"
  [ "$status" -eq 1 ]
  [[ "$output" == *"path already exists"* ]]
}

@test "wt create no args shows usage" {
  cd "$PROJECT"
  run "$TA_WT" create
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

# --- ta wt create --from=<base> tests ---

@test "wt create --from=main creates new branch from main" {
  cd "$PROJECT"
  local wt_dir="$BATS_TEST_TMPDIR/wt-from-main"

  run "$TA_WT" create new-feat "$wt_dir" --from=main
  [ "$status" -eq 0 ]
  [[ "$output" == *"$wt_dir"* ]]
  [ -d "$wt_dir" ]

  # Branch should exist
  run git branch --list new-feat
  [[ "$output" == *"new-feat"* ]]

  # Branch should be based on main
  run git merge-base --is-ancestor main new-feat
  [ "$status" -eq 0 ]
}

@test "wt create --from=<base> creates from that base" {
  cd "$PROJECT"

  # Create an 8.x branch
  git checkout -b 8.x
  git commit --allow-empty -m "8.x base"
  git checkout main

  local wt_dir="$BATS_TEST_TMPDIR/wt-from-8x"
  run "$TA_WT" create feat-from-8x "$wt_dir" --from=8.x
  [ "$status" -eq 0 ]
  [ -d "$wt_dir" ]

  # Branch should be based on 8.x
  run git merge-base --is-ancestor 8.x feat-from-8x
  [ "$status" -eq 0 ]
}

@test "wt create --from=main with existing compatible branch creates worktree" {
  cd "$PROJECT"

  # Create a branch that descends from main
  git checkout -b existing-compat
  git commit --allow-empty -m "existing compatible"
  git checkout main

  local wt_dir="$BATS_TEST_TMPDIR/wt-existing-compat"
  run "$TA_WT" create existing-compat "$wt_dir" --from=main
  [ "$status" -eq 0 ]
  [ -d "$wt_dir" ]
}

@test "wt create --from=<base> with existing incompatible branch errors" {
  cd "$PROJECT"

  # Create two divergent branches
  git checkout -b base-branch
  git commit --allow-empty -m "base branch commit"
  git checkout main

  git checkout -b divergent-feat
  git commit --allow-empty -m "divergent commit"
  git checkout main

  run "$TA_WT" create divergent-feat "$BATS_TEST_TMPDIR/wt-divergent" --from=base-branch
  [ "$status" -eq 1 ]
  [[ "$output" == *"not based on"* ]]
}

@test "wt create without --from unchanged behavior" {
  cd "$PROJECT"
  local wt_dir="$BATS_TEST_TMPDIR/wt-no-from"

  # Push a branch to upstream so it exists on the remote
  git checkout -b feat-no-from
  git commit --allow-empty -m "feat no from"
  git push upstream feat-no-from
  git checkout main
  git branch -D feat-no-from

  run "$TA_WT" create feat-no-from "$wt_dir"
  [ "$status" -eq 0 ]
  [ -d "$wt_dir" ]
}

@test "wt create --from=main generates default path" {
  cd "$PROJECT"

  run "$TA_WT" create new-feat-path --from=main
  [ "$status" -eq 0 ]
  [[ "$output" == *"project-new-feat-path"* ]]
}

# --- ta wt remove tests ---

@test "wt remove clean worktree" {
  cd "$PROJECT"
  git checkout -b feat-rm
  git commit --allow-empty -m "feat rm"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-rm" feat-rm

  run "$TA_WT" remove feat-rm
  [ "$status" -eq 0 ]
  [[ "$output" == *"removed"* ]]
  [ ! -d "$BATS_TEST_TMPDIR/wt-rm" ]
}

@test "wt remove dirty worktree refused" {
  cd "$PROJECT"
  git checkout -b feat-rm-dirty
  git commit --allow-empty -m "feat rm dirty"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-rm-dirty" feat-rm-dirty

  echo "dirty" > "$BATS_TEST_TMPDIR/wt-rm-dirty/dirty.txt"

  run "$TA_WT" remove feat-rm-dirty
  [ "$status" -eq 1 ]
  [[ "$output" == *"uncommitted changes"* ]]
  [ -d "$BATS_TEST_TMPDIR/wt-rm-dirty" ]
}

@test "wt remove with --force removes dirty worktree" {
  cd "$PROJECT"
  git checkout -b feat-rm-force
  git commit --allow-empty -m "feat rm force"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-rm-force" feat-rm-force

  echo "dirty" > "$BATS_TEST_TMPDIR/wt-rm-force/dirty.txt"

  run "$TA_WT" remove feat-rm-force --force
  [ "$status" -eq 0 ]
  [[ "$output" == *"removed"* ]]
  [ ! -d "$BATS_TEST_TMPDIR/wt-rm-force" ]
}

@test "wt remove nonexistent branch fails" {
  cd "$PROJECT"
  run "$TA_WT" remove no-such-branch
  [ "$status" -eq 1 ]
  [[ "$output" == *"no worktree found"* ]]
}

@test "wt remove no args shows usage" {
  cd "$PROJECT"
  run "$TA_WT" remove
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "wt remove deletes the local branch" {
  cd "$PROJECT"
  git checkout -b feat-rm-branch
  git commit --allow-empty -m "feat rm branch"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-rm-branch" feat-rm-branch

  run "$TA_WT" remove feat-rm-branch
  [ "$status" -eq 0 ]

  # Branch should be gone
  run git branch --list feat-rm-branch
  [ -z "$output" ]
}

@test "wt remove refuses to remove current worktree" {
  cd "$PROJECT"
  git checkout -b feat-self-remove
  git commit --allow-empty -m "self remove"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-self-remove" feat-self-remove

  # Run ta wt remove from inside the worktree we're trying to remove.
  # This would leave the shell with a dangling cwd and silently break
  # subsequent git operations, so it must be refused.
  cd "$BATS_TEST_TMPDIR/wt-self-remove"
  run "$TA_WT" remove feat-self-remove
  [ "$status" -eq 1 ]
  [[ "$output" == *"cannot remove worktree 'feat-self-remove' while inside it"* ]]

  # Nothing was modified
  [ -d "$BATS_TEST_TMPDIR/wt-self-remove" ]
  run git -C "$PROJECT" branch --list feat-self-remove
  [[ "$output" == *"feat-self-remove"* ]]
}

@test "wt remove refuses from a subdirectory of the current worktree" {
  cd "$PROJECT"
  git checkout -b feat-self-sub
  git commit --allow-empty -m "self sub"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-self-sub" feat-self-sub
  mkdir "$BATS_TEST_TMPDIR/wt-self-sub/nested"

  cd "$BATS_TEST_TMPDIR/wt-self-sub/nested"
  run "$TA_WT" remove feat-self-sub
  [ "$status" -eq 1 ]
  [[ "$output" == *"cannot remove worktree 'feat-self-sub' while inside it"* ]]

  [ -d "$BATS_TEST_TMPDIR/wt-self-sub" ]
}

@test "wt remove calls workspace kill for session cleanup" {
  cd "$PROJECT"
  git checkout -b feat-rm-ws
  git commit --allow-empty -m "feat rm ws"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-rm-ws" feat-rm-ws

  # Create a mock ta-workspace that logs calls
  local mock_dir="$BATS_TEST_TMPDIR/mock-scripts"
  mkdir -p "$mock_dir"
  cat > "$mock_dir/ta-workspace" << MOCK
#!/bin/sh
echo "\$@" >> "$BATS_TEST_TMPDIR/workspace-calls.log"
MOCK
  chmod +x "$mock_dir/ta-workspace"

  # Copy ta-wt to mock dir so it finds our mock ta-workspace via ${0:A:h}
  cp "$TA_WT" "$mock_dir/ta-wt"

  # Set PYTHONPATH so the copied script can find dotlib
  local dotfiles_dir="${BATS_TEST_FILENAME%/*}/.."
  run env PYTHONPATH="$dotfiles_dir/tools/libs" "$mock_dir/ta-wt" remove feat-rm-ws
  [ "$status" -eq 0 ]
  [[ "$output" == *"removed"* ]]

  # Verify workspace kill was called with the branch name
  [ -f "$BATS_TEST_TMPDIR/workspace-calls.log" ]
  run cat "$BATS_TEST_TMPDIR/workspace-calls.log"
  [[ "$output" == *"kill feat-rm-ws"* ]]
}

@test "wt remove succeeds even when no tmux session exists" {
  cd "$PROJECT"
  git checkout -b feat-rm-notmux
  git commit --allow-empty -m "feat rm notmux"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-rm-notmux" feat-rm-notmux

  # No tmux session exists — workspace kill should fail silently
  run "$TA_WT" remove feat-rm-notmux
  [ "$status" -eq 0 ]
  [[ "$output" == *"removed"* ]]
  [ ! -d "$BATS_TEST_TMPDIR/wt-rm-notmux" ]
}

# --- ta wt prune tests ---

@test "wt prune dry-run lists merged branch as candidate" {
  cd "$PROJECT"
  git checkout -b feat-merged
  git commit --allow-empty -m "will merge"
  git checkout main
  git merge feat-merged
  git worktree add "$BATS_TEST_TMPDIR/wt-merged" feat-merged

  run "$TA_WT" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"would remove"* ]]
  [[ "$output" == *"feat-merged"* ]]
  # Worktree should still exist (dry-run)
  [ -d "$BATS_TEST_TMPDIR/wt-merged" ]
}

@test "wt prune skips dirty worktrees" {
  cd "$PROJECT"
  git checkout -b feat-prune-dirty
  git commit --allow-empty -m "will merge dirty"
  git checkout main
  git merge feat-prune-dirty
  git worktree add "$BATS_TEST_TMPDIR/wt-prune-dirty" feat-prune-dirty

  echo "dirty" > "$BATS_TEST_TMPDIR/wt-prune-dirty/dirty.txt"

  run "$TA_WT" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping"* ]]
  [[ "$output" == *"feat-prune-dirty"* ]]
  [ -d "$BATS_TEST_TMPDIR/wt-prune-dirty" ]
}

@test "wt prune skips current worktree" {
  cd "$PROJECT"
  # Create a branch that is merged, then make it the current worktree
  git checkout -b feat-prune-current
  git commit --allow-empty -m "merged current"
  git checkout main
  git merge feat-prune-current
  git worktree add "$BATS_TEST_TMPDIR/wt-prune-current" feat-prune-current

  # Run prune from the worktree itself
  cd "$BATS_TEST_TMPDIR/wt-prune-current"
  run "$TA_WT" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"no worktrees to prune"* ]]
}

@test "wt prune skips main branch" {
  cd "$PROJECT"
  # Only main exists — nothing to prune
  run "$TA_WT" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"no worktrees to prune"* ]]
}

@test "wt prune skips unmerged branches" {
  cd "$PROJECT"
  git checkout -b feat-unmerged
  git commit --allow-empty -m "not merged"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-unmerged" feat-unmerged

  run "$TA_WT" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"no worktrees to prune"* ]]
  [ -d "$BATS_TEST_TMPDIR/wt-unmerged" ]
}

@test "wt prune --apply removes merged worktrees" {
  cd "$PROJECT"
  git checkout -b feat-prune-apply
  git commit --allow-empty -m "will prune"
  git checkout main
  git merge feat-prune-apply
  git worktree add "$BATS_TEST_TMPDIR/wt-prune-apply" feat-prune-apply

  run "$TA_WT" prune --apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"removed"* ]]
  [ ! -d "$BATS_TEST_TMPDIR/wt-prune-apply" ]

  # Branch should also be deleted
  run git branch --list feat-prune-apply
  [ -z "$output" ]
}

# --- ta wt status tests ---

@test "wt status shows wip for dirty worktree" {
  cd "$PROJECT"
  git checkout -b feat-status-wip
  git commit --allow-empty -m "wip branch"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-status-wip" feat-status-wip

  echo "dirty" > "$BATS_TEST_TMPDIR/wt-status-wip/dirty.txt"

  run "$TA_WT" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat-status-wip"* ]]
  [[ "$output" == *"wip"* ]]
}

@test "wt status shows merged for fully merged branch" {
  cd "$PROJECT"
  git checkout -b feat-status-merged
  git commit --allow-empty -m "merge me"
  git checkout main
  git merge feat-status-merged
  git worktree add "$BATS_TEST_TMPDIR/wt-status-merged" feat-status-merged

  run "$TA_WT" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat-status-merged"* ]]
  [[ "$output" == *"merged"* ]]
}

@test "wt status shows almost for unpushed branch" {
  cd "$PROJECT"
  git checkout -b feat-status-almost
  git commit --allow-empty -m "unpushed"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-status-almost" feat-status-almost

  # Branch has no upstream, so it's "almost" (unpushed)
  run "$TA_WT" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat-status-almost"* ]]
  [[ "$output" == *"almost"* ]]
}

@test "wt status shows ready for pushed branch" {
  cd "$PROJECT"
  git checkout -b feat-status-ready
  git commit --allow-empty -m "pushed"
  git push upstream feat-status-ready
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-status-ready" feat-status-ready

  # Set upstream tracking
  git -C "$BATS_TEST_TMPDIR/wt-status-ready" branch --set-upstream-to=upstream/feat-status-ready

  run "$TA_WT" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat-status-ready"* ]]
  [[ "$output" == *"ready"* ]]
}

@test "wt status shows current for active worktree" {
  cd "$PROJECT"
  git checkout -b feat-status-current
  git commit --allow-empty -m "current"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-status-current" feat-status-current

  # Run from the worktree
  cd "$BATS_TEST_TMPDIR/wt-status-current"
  run "$TA_WT" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat-status-current"* ]]
  [[ "$output" == *"current"* ]]
}

@test "wt status skips main branch" {
  cd "$PROJECT"
  # Only main — status should produce no output
  run "$TA_WT" status
  [ "$status" -eq 0 ]
  [[ "$output" != *"main"* ]]
}

@test "wt status --json returns valid JSON" {
  cd "$PROJECT"
  git checkout -b feat-status-json
  git commit --allow-empty -m "json status"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-status-json" feat-status-json

  run "$TA_WT" status --json
  [ "$status" -eq 0 ]

  # Validate JSON
  echo "$output" | jq . > /dev/null
  echo "$output" | jq -e '.[0] | has("branch", "status", "ahead", "behind", "dirty", "path")'
}

@test "wt status --json with no non-main worktrees returns empty array" {
  cd "$PROJECT"
  run "$TA_WT" status --json
  [ "$status" -eq 0 ]
  local count
  count="$(echo "$output" | jq length)"
  [ "$count" -eq 0 ]
}

# --- ta wt merge tests ---

@test "wt merge clean branch into main succeeds" {
  cd "$PROJECT"
  git checkout -b feat-merge
  echo "feature content" > feature.txt
  git add feature.txt
  git commit -m "add feature"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-merge" feat-merge

  run "$TA_WT" merge feat-merge
  [ "$status" -eq 0 ]
  [[ "$output" == *"merged 'feat-merge' into main"* ]]
  [[ "$output" == *"worktree and branch removed"* ]]

  # Commit exists on main with the feature content
  [ -f "$PROJECT/feature.txt" ]

  # Worktree should be removed
  [ ! -d "$BATS_TEST_TMPDIR/wt-merge" ]

  # Branch should be deleted
  run git -C "$PROJECT" branch --list feat-merge
  [ -z "$output" ]
}

@test "wt merge dirty branch is refused" {
  cd "$PROJECT"
  git checkout -b feat-merge-dirty
  git commit --allow-empty -m "dirty merge"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-merge-dirty" feat-merge-dirty

  echo "dirty" > "$BATS_TEST_TMPDIR/wt-merge-dirty/dirty.txt"

  run "$TA_WT" merge feat-merge-dirty
  [ "$status" -eq 1 ]
  [[ "$output" == *"uncommitted changes"* ]]
}

@test "wt merge with dirty main is refused" {
  cd "$PROJECT"
  git checkout -b feat-merge-dm
  git commit --allow-empty -m "for merge"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-merge-dm" feat-merge-dm

  # Dirty the main worktree
  echo "dirty" > "$PROJECT/dirty-main.txt"

  run "$TA_WT" merge feat-merge-dm
  [ "$status" -eq 1 ]
  [[ "$output" == *"target worktree 'main' has uncommitted changes"* ]]
}

@test "wt merge nonexistent branch fails with exit 2" {
  cd "$PROJECT"
  run "$TA_WT" merge no-such-branch
  [ "$status" -eq 2 ]
  [[ "$output" == *"no worktree found"* ]]
}

@test "wt merge no args shows usage with exit 2" {
  cd "$PROJECT"
  run "$TA_WT" merge
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "wt merge with conflicts aborts and leaves main clean" {
  cd "$PROJECT"

  # Create initial content that both branches will diverge from
  echo "initial version" > conflict.txt
  git add conflict.txt
  git commit -m "initial conflict file"

  # Create branch and modify the file
  git checkout -b feat-merge-conflict
  echo "branch version" > conflict.txt
  git add conflict.txt
  git commit -m "branch conflict"
  git checkout main

  # Now diverge main independently to create a real conflict
  echo "main version" > conflict.txt
  git add conflict.txt
  git commit -m "main conflict"

  git worktree add "$BATS_TEST_TMPDIR/wt-merge-conflict" feat-merge-conflict

  run "$TA_WT" merge feat-merge-conflict
  [ "$status" -eq 1 ]

  # Main should be clean after abort
  run git -C "$PROJECT" status --porcelain
  [ -z "$output" ]

  # Worktree should still exist (merge failed, no cleanup)
  [ -d "$BATS_TEST_TMPDIR/wt-merge-conflict" ]
}

@test "wt merge cleanup continues if workspace kill fails" {
  cd "$PROJECT"
  git checkout -b feat-merge-nows
  echo "nows content" > nows-file.txt
  git add nows-file.txt
  git commit -m "no workspace"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-merge-nows" feat-merge-nows

  # No tmux session exists — workspace kill should fail silently
  run "$TA_WT" merge feat-merge-nows
  [ "$status" -eq 0 ]
  [[ "$output" == *"merged"* ]]
}

@test "wt merge squash commit message contains branch name" {
  cd "$PROJECT"
  git checkout -b feat-merge-msg
  echo "msg content" > msg-file.txt
  git add msg-file.txt
  git commit -m "commit for squash msg"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-merge-msg" feat-merge-msg

  run "$TA_WT" merge feat-merge-msg
  [ "$status" -eq 0 ]

  # Check that the commit message on main references the branch
  local msg
  msg="$(git -C "$PROJECT" log -1 --format='%s')"
  [[ "$msg" == *"feat-merge-msg"* ]]
}

@test "wt merge --target merges into specified branch" {
  cd "$PROJECT"

  # Create base branch 8.x with a commit
  git checkout -b 8.x
  git commit --allow-empty -m "8.x base commit"

  # Create feature branch off 8.x
  git checkout -b feature-8x
  echo "feature content" > feature-8x.txt
  git add feature-8x.txt
  git commit -m "feature for 8.x"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-8x" 8.x
  git worktree add "$BATS_TEST_TMPDIR/wt-feature-8x" feature-8x

  run "$TA_WT" merge --target 8.x feature-8x
  [ "$status" -eq 0 ]
  [[ "$output" == *"merged 'feature-8x' into 8.x"* ]]
  [[ "$output" == *"worktree and branch removed"* ]]

  # feature content should be in 8.x worktree
  [ -f "$BATS_TEST_TMPDIR/wt-8x/feature-8x.txt" ]

  # feature worktree should be removed
  [ ! -d "$BATS_TEST_TMPDIR/wt-feature-8x" ]

  # feature branch should be deleted
  run git -C "$PROJECT" branch --list feature-8x
  [ -z "$output" ]
}

@test "wt merge --target nonexistent target fails" {
  cd "$PROJECT"

  git checkout -b feat-no-target
  git commit --allow-empty -m "feat for missing target"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-no-target" feat-no-target

  run "$TA_WT" merge --target no-such-target feat-no-target
  [ "$status" -eq 1 ]
  [[ "$output" == *"cannot find worktree for target branch 'no-such-target'"* ]]
}

@test "wt merge --target dirty target is refused" {
  cd "$PROJECT"

  # Create base branch with a worktree
  git checkout -b base-dirty-target
  git commit --allow-empty -m "base branch"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-base-dirty" base-dirty-target

  # Create feature branch with a worktree
  git checkout -b feat-for-dirty-target
  git commit --allow-empty -m "feat for dirty target"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-feat-dirty-target" feat-for-dirty-target

  # Make the target (base-dirty-target) worktree dirty
  echo "dirty" > "$BATS_TEST_TMPDIR/wt-base-dirty/dirty.txt"

  run "$TA_WT" merge --target base-dirty-target feat-for-dirty-target
  [ "$status" -eq 1 ]
  [[ "$output" == *"target worktree 'base-dirty-target' has uncommitted changes"* ]]
}

@test "wt merge --message uses custom commit message" {
  cd "$PROJECT"
  git checkout -b feat-merge-custom-msg
  echo "custom msg content" > custom-msg.txt
  git add custom-msg.txt
  git commit -m "commit for custom msg"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-merge-custom-msg" feat-merge-custom-msg

  run "$TA_WT" merge --message "My custom squash message" feat-merge-custom-msg
  [ "$status" -eq 0 ]
  [[ "$output" == *"merged"* ]]

  local msg
  msg="$(git -C "$PROJECT" log -1 --format='%s')"
  [ "$msg" = "My custom squash message" ]
}

@test "wt merge --message-file reads message from file" {
  cd "$PROJECT"
  git checkout -b feat-merge-file-msg
  echo "file msg content" > file-msg.txt
  git add file-msg.txt
  git commit -m "commit for file msg"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-merge-file-msg" feat-merge-file-msg

  echo "Message from file" > "$BATS_TEST_TMPDIR/commit-msg.txt"

  run "$TA_WT" merge --message-file "$BATS_TEST_TMPDIR/commit-msg.txt" feat-merge-file-msg
  [ "$status" -eq 0 ]
  [[ "$output" == *"merged"* ]]

  local msg
  msg="$(git -C "$PROJECT" log -1 --format='%s')"
  [ "$msg" = "Message from file" ]
}

@test "wt merge --message and --message-file together fails" {
  cd "$PROJECT"
  git checkout -b feat-merge-both
  git commit --allow-empty -m "both flags"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-merge-both" feat-merge-both

  echo "msg" > "$BATS_TEST_TMPDIR/both-msg.txt"

  run "$TA_WT" merge --message "inline" --message-file "$BATS_TEST_TMPDIR/both-msg.txt" feat-merge-both
  [ "$status" -eq 2 ]
  [[ "$output" == *"cannot use both --message and --message-file"* ]]
}

@test "wt merge --message-file with nonexistent file fails" {
  cd "$PROJECT"
  git checkout -b feat-merge-nofile
  git commit --allow-empty -m "no file"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-merge-nofile" feat-merge-nofile

  run "$TA_WT" merge --message-file "/tmp/nonexistent-file-12345.txt" feat-merge-nofile
  [ "$status" -eq 2 ]
  [[ "$output" == *"message file not found"* ]]
}

@test "wt merge --target defaults to main" {
  cd "$PROJECT"
  git checkout -b feat-explicit-main
  echo "explicit main content" > explicit-main.txt
  git add explicit-main.txt
  git commit -m "add explicit main feature"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-explicit-main" feat-explicit-main

  run "$TA_WT" merge --target main feat-explicit-main
  [ "$status" -eq 0 ]
  [[ "$output" == *"merged 'feat-explicit-main' into main"* ]]

  # Commit exists on main
  [ -f "$PROJECT/explicit-main.txt" ]
}

# --- gh stub helper (used by spec-issue tests) ---
#
# Install a programmable gh stub at $BATS_TEST_TMPDIR/bin/gh that responds
# to `gh issue view`, `gh issue close`, and `gh pr list`. Behavior is
# controlled via env vars the test sets before running ta-wt:
#
#   GH_STUB_LABELS    comma-separated labels returned by `gh issue view`
#                     (default: "spec,status:done")
#   GH_STUB_VIEW_RC   exit code for `gh issue view` (default: 0)
#   GH_STUB_CLOSE_RC  exit code for `gh issue close` (default: 0)
#   GH_STUB_LOG       if set, each invocation's args are appended here
_install_gh_stub() {
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
if [[ -n "${GH_STUB_LOG:-}" ]]; then
  printf '%s\n' "$*" >> "$GH_STUB_LOG"
fi
case "${1:-} ${2:-}" in
  "issue view")
    if [[ "${GH_STUB_VIEW_RC:-0}" != "0" ]]; then
      echo "gh stub: issue view error" >&2
      exit "${GH_STUB_VIEW_RC}"
    fi
    echo "${GH_STUB_LABELS:-spec,status:done}"
    ;;
  "issue close")
    if [[ "${GH_STUB_CLOSE_RC:-0}" != "0" ]]; then
      echo "gh stub: issue close error" >&2
      exit "${GH_STUB_CLOSE_RC}"
    fi
    ;;
  "pr list")
    echo "[]"
    ;;
esac
exit 0
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

# --- ta wt merge spec issue tests ---

@test "wt merge closes spec issue on happy path" {
  cd "$PROJECT"
  _install_gh_stub
  export GH_STUB_LOG="$BATS_TEST_TMPDIR/gh.log"

  git checkout -b feat-spec
  echo "spec content" > spec.txt
  git add spec.txt
  git commit -m "spec commit"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-spec" feat-spec

  git -C "$PROJECT" config branch.feat-spec.issue 42

  run "$TA_WT" merge feat-spec
  [ "$status" -eq 0 ]
  [[ "$output" == *"closed spec issue #42"* ]]
  [[ "$output" == *"merged 'feat-spec' into main"* ]]

  # Both gh view (pre-flight) and gh close (post-merge) were invoked
  grep -q "issue view 42" "$BATS_TEST_TMPDIR/gh.log"
  grep -q "issue close 42" "$BATS_TEST_TMPDIR/gh.log"

  # Config entry was unset on successful close
  run git -C "$PROJECT" config --get branch.feat-spec.issue
  [ "$status" -ne 0 ]

  # Branch was deleted
  run git -C "$PROJECT" branch --list feat-spec
  [ -z "$output" ]
}

@test "wt merge without spec issue does not invoke gh" {
  cd "$PROJECT"
  _install_gh_stub
  export GH_STUB_LOG="$BATS_TEST_TMPDIR/gh.log"

  git checkout -b feat-noissue
  echo "noissue content" > noissue.txt
  git add noissue.txt
  git commit -m "no issue"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-noissue" feat-noissue

  # No branch.feat-noissue.issue config set — not a ralph-managed branch.

  run "$TA_WT" merge feat-noissue
  [ "$status" -eq 0 ]
  [[ "$output" == *"merged 'feat-noissue' into main"* ]]
  [[ "$output" != *"closed spec issue"* ]]

  # gh must not have been invoked for issue view/close
  if [ -f "$BATS_TEST_TMPDIR/gh.log" ]; then
    run grep -E "^issue (view|close)" "$BATS_TEST_TMPDIR/gh.log"
    [ "$status" -ne 0 ]
  fi
}

@test "wt merge refuses when spec issue is not status:done" {
  cd "$PROJECT"
  _install_gh_stub
  export GH_STUB_LABELS="spec,status:in-progress"

  git checkout -b feat-inprog
  echo "inprog content" > inprog.txt
  git add inprog.txt
  git commit -m "in progress"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-inprog" feat-inprog

  git -C "$PROJECT" config branch.feat-inprog.issue 99

  run "$TA_WT" merge feat-inprog
  [ "$status" -eq 1 ]
  [[ "$output" == *"#99"* ]]
  [[ "$output" == *"status:done"* ]]

  # Merge did NOT happen — no new file on main
  [ ! -f "$PROJECT/inprog.txt" ]

  # Worktree, branch, and config entry all untouched
  [ -d "$BATS_TEST_TMPDIR/wt-inprog" ]
  run git -C "$PROJECT" branch --list feat-inprog
  [[ "$output" == *"feat-inprog"* ]]
  run git -C "$PROJECT" config --get branch.feat-inprog.issue
  [ "$status" -eq 0 ]
  [[ "$output" == "99" ]]
}

@test "wt merge refuses when gh view fails in pre-flight" {
  cd "$PROJECT"
  _install_gh_stub
  export GH_STUB_VIEW_RC=1

  git checkout -b feat-ghfail
  echo "ghfail content" > ghfail.txt
  git add ghfail.txt
  git commit -m "gh fail"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-ghfail" feat-ghfail

  git -C "$PROJECT" config branch.feat-ghfail.issue 77

  run "$TA_WT" merge feat-ghfail
  [ "$status" -eq 1 ]
  [[ "$output" == *"failed to fetch spec issue #77"* ]]

  # Merge did NOT happen
  [ ! -f "$PROJECT/ghfail.txt" ]
  [ -d "$BATS_TEST_TMPDIR/wt-ghfail" ]
}

@test "wt merge fails loudly if gh close fails after merge landed" {
  cd "$PROJECT"
  _install_gh_stub
  export GH_STUB_CLOSE_RC=1

  git checkout -b feat-closefail
  echo "closefail content" > closefail.txt
  git add closefail.txt
  git commit -m "close fail"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-closefail" feat-closefail

  git -C "$PROJECT" config branch.feat-closefail.issue 55

  run "$TA_WT" merge feat-closefail
  [ "$status" -eq 1 ]
  [[ "$output" == *"failed to close spec issue #55"* ]]
  [[ "$output" == *"merge landed"* ]]
  [[ "$output" == *"/tidy"* ]]

  # Merge DID land on main
  [ -f "$PROJECT/closefail.txt" ]

  # Worktree and branch still exist (cleanup aborted before cmd_remove)
  [ -d "$BATS_TEST_TMPDIR/wt-closefail" ]
  run git -C "$PROJECT" branch --list feat-closefail
  [[ "$output" == *"feat-closefail"* ]]

  # Config entry still present — /tidy can retry the close
  run git -C "$PROJECT" config --get branch.feat-closefail.issue
  [ "$status" -eq 0 ]
  [[ "$output" == "55" ]]
}

@test "wt merge from inside branch worktree actually deletes branch" {
  # Regression test for the zombie-branches bug. Before the os.chdir(main_wt)
  # anchor in cmd_merge, running ta wt merge from inside the branch worktree
  # caused git branch -D in cmd_remove to fail silently (check=False), because
  # Python's cwd was wiped by git worktree remove. The branch was left behind.
  cd "$PROJECT"

  git checkout -b feat-zombie
  echo "zombie content" > zombie.txt
  git add zombie.txt
  git commit -m "zombie"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-zombie" feat-zombie

  cd "$BATS_TEST_TMPDIR/wt-zombie"
  run "$TA_WT" merge feat-zombie
  [ "$status" -eq 0 ]

  # The actual regression assertion: branch must be deleted.
  run git -C "$PROJECT" branch --list feat-zombie
  [ -z "$output" ]

  # Worktree dir is also gone.
  [ ! -d "$BATS_TEST_TMPDIR/wt-zombie" ]
}

# --- ta wt prune spec issue tests ---

@test "wt prune --apply closes spec issue before removing merged worktree" {
  cd "$PROJECT"
  _install_gh_stub
  export GH_STUB_LOG="$BATS_TEST_TMPDIR/gh.log"

  git checkout -b feat-prune-issue
  echo "prune issue content" > pi.txt
  git add pi.txt
  git commit -m "prune issue"
  git checkout main
  git merge feat-prune-issue
  git worktree add "$BATS_TEST_TMPDIR/wt-prune-issue" feat-prune-issue

  git -C "$PROJECT" config branch.feat-prune-issue.issue 11

  run "$TA_WT" prune --apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"closed spec issue #11 for 'feat-prune-issue'"* ]]

  grep -q "issue close 11" "$BATS_TEST_TMPDIR/gh.log"

  # Worktree and branch gone
  [ ! -d "$BATS_TEST_TMPDIR/wt-prune-issue" ]
  run git -C "$PROJECT" branch --list feat-prune-issue
  [ -z "$output" ]

  # Config entry unset
  run git -C "$PROJECT" config --get branch.feat-prune-issue.issue
  [ "$status" -ne 0 ]
}

@test "wt prune --apply skips merged worktree with wrong-label issue" {
  cd "$PROJECT"
  _install_gh_stub
  export GH_STUB_LABELS="spec,status:in-progress"

  git checkout -b feat-prune-wrong
  echo "prune wrong content" > pw.txt
  git add pw.txt
  git commit -m "prune wrong"
  git checkout main
  git merge feat-prune-wrong
  git worktree add "$BATS_TEST_TMPDIR/wt-prune-wrong" feat-prune-wrong

  git -C "$PROJECT" config branch.feat-prune-wrong.issue 22

  run "$TA_WT" prune --apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping 'feat-prune-wrong'"* ]]
  [[ "$output" == *"status:done"* ]]

  # Worktree and branch still exist — nothing was removed
  [ -d "$BATS_TEST_TMPDIR/wt-prune-wrong" ]
  run git -C "$PROJECT" branch --list feat-prune-wrong
  [[ "$output" == *"feat-prune-wrong"* ]]

  # Config entry still present — user can fix labels and retry
  run git -C "$PROJECT" config --get branch.feat-prune-wrong.issue
  [ "$status" -eq 0 ]
}

@test "wt prune dry-run shows will close spec issue suffix" {
  cd "$PROJECT"
  _install_gh_stub

  git checkout -b feat-dry-issue
  echo "dry content" > dry.txt
  git add dry.txt
  git commit -m "dry"
  git checkout main
  git merge feat-dry-issue
  git worktree add "$BATS_TEST_TMPDIR/wt-dry-issue" feat-dry-issue

  git -C "$PROJECT" config branch.feat-dry-issue.issue 33

  run "$TA_WT" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"would remove 'feat-dry-issue'"* ]]
  [[ "$output" == *"will close spec issue #33"* ]]

  # Dry-run: worktree still exists
  [ -d "$BATS_TEST_TMPDIR/wt-dry-issue" ]
}

@test "wt prune --apply closes orphaned spec issue configs" {
  cd "$PROJECT"
  _install_gh_stub
  export GH_STUB_LOG="$BATS_TEST_TMPDIR/gh.log"

  # Stale config entry for a branch that never existed in this repo.
  git -C "$PROJECT" config branch.deleted-long-ago.issue 88

  run "$TA_WT" prune --apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"closed orphaned spec issue #88"* ]]
  [[ "$output" == *"deleted-long-ago"* ]]

  grep -q "issue close 88" "$BATS_TEST_TMPDIR/gh.log"

  # Config entry unset
  run git -C "$PROJECT" config --get branch.deleted-long-ago.issue
  [ "$status" -ne 0 ]
}

@test "wt prune dry-run lists orphaned spec issue configs" {
  cd "$PROJECT"
  _install_gh_stub

  git -C "$PROJECT" config branch.deleted-other.issue 77

  run "$TA_WT" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"would close orphaned spec issue #77"* ]]
  [[ "$output" == *"deleted-other"* ]]
}
