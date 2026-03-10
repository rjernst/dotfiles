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
  run zsh "$TA"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "ta unknown command fails" {
  run zsh "$TA" bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown command"* ]]
}

@test "ta wt dispatches to ta-wt" {
  run zsh "$TA" wt
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage: ta wt"* ]]
}

# --- ta wt list tests ---

@test "wt list with no worktrees shows only main" {
  cd "$PROJECT"
  run zsh "$TA_WT" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"main"* ]]
  [[ "$output" == *"BRANCH"* ]]
}

@test "wt list shows current for active worktree" {
  cd "$PROJECT"
  run zsh "$TA_WT" list
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

  run zsh "$TA_WT" list
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

  run zsh "$TA_WT" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"dirty"* ]]
}

@test "wt list --json returns valid JSON" {
  cd "$PROJECT"
  run zsh "$TA_WT" list --json
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

  run zsh "$TA_WT" list --json
  [ "$status" -eq 0 ]

  local count
  count="$(echo "$output" | jq length)"
  [ "$count" -eq 2 ]
}

@test "wt list --full shows commit message" {
  cd "$PROJECT"
  run zsh "$TA_WT" list --full
  [ "$status" -eq 0 ]
  [[ "$output" == *"LAST COMMIT"* ]]
  [[ "$output" == *"initial commit"* ]]
}

@test "wt list --json --full includes commit fields" {
  cd "$PROJECT"
  run zsh "$TA_WT" list --json --full
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

  run zsh "$TA_WT" list --json
  [ "$status" -eq 0 ]

  # The ahead-branch should be 2 ahead of main
  local ahead
  ahead="$(echo "$output" | jq '.[] | select(.branch == "ahead-branch") | .ahead')"
  [ "$ahead" -eq 2 ]
}

@test "wt list unknown option fails" {
  cd "$PROJECT"
  run zsh "$TA_WT" list --bogus
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

  run zsh "$TA_WT" create feat-create "$wt_dir"
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

  run zsh "$TA_WT" create feat-default
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

  run zsh "$TA_WT" create feature/slash-test
  [ "$status" -eq 0 ]
  [[ "$output" == *"project-feature-slash-test"* ]]
}

@test "wt create nonexistent branch fails" {
  cd "$PROJECT"

  run zsh "$TA_WT" create nonexistent-branch
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
  run zsh "$TA_WT" create feat-origin "$wt_dir" --remote origin
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
  run zsh "$TA_WT" create feat-fallback "$wt_dir"
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
  run zsh "$TA_WT" create feat-origin-only "$wt_dir"
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
  run zsh "$TA_WT" create feat-exists "$BATS_TEST_TMPDIR/wt-exists"
  [ "$status" -eq 1 ]
  [[ "$output" == *"path already exists"* ]]
}

@test "wt create no args shows usage" {
  cd "$PROJECT"
  run zsh "$TA_WT" create
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

# --- ta wt remove tests ---

@test "wt remove clean worktree" {
  cd "$PROJECT"
  git checkout -b feat-rm
  git commit --allow-empty -m "feat rm"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-rm" feat-rm

  run zsh "$TA_WT" remove feat-rm
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

  run zsh "$TA_WT" remove feat-rm-dirty
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

  run zsh "$TA_WT" remove feat-rm-force --force
  [ "$status" -eq 0 ]
  [[ "$output" == *"removed"* ]]
  [ ! -d "$BATS_TEST_TMPDIR/wt-rm-force" ]
}

@test "wt remove nonexistent branch fails" {
  cd "$PROJECT"
  run zsh "$TA_WT" remove no-such-branch
  [ "$status" -eq 1 ]
  [[ "$output" == *"no worktree found"* ]]
}

@test "wt remove no args shows usage" {
  cd "$PROJECT"
  run zsh "$TA_WT" remove
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "wt remove deletes the local branch" {
  cd "$PROJECT"
  git checkout -b feat-rm-branch
  git commit --allow-empty -m "feat rm branch"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-rm-branch" feat-rm-branch

  run zsh "$TA_WT" remove feat-rm-branch
  [ "$status" -eq 0 ]

  # Branch should be gone
  run git branch --list feat-rm-branch
  [ -z "$output" ]
}

# --- ta wt prune tests ---

@test "wt prune dry-run lists merged branch as candidate" {
  cd "$PROJECT"
  git checkout -b feat-merged
  git commit --allow-empty -m "will merge"
  git checkout main
  git merge feat-merged
  git worktree add "$BATS_TEST_TMPDIR/wt-merged" feat-merged

  run zsh "$TA_WT" prune
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

  run zsh "$TA_WT" prune
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
  run zsh "$TA_WT" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"no worktrees to prune"* ]]
}

@test "wt prune skips main branch" {
  cd "$PROJECT"
  # Only main exists — nothing to prune
  run zsh "$TA_WT" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"no worktrees to prune"* ]]
}

@test "wt prune skips unmerged branches" {
  cd "$PROJECT"
  git checkout -b feat-unmerged
  git commit --allow-empty -m "not merged"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-unmerged" feat-unmerged

  run zsh "$TA_WT" prune
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

  run zsh "$TA_WT" prune --apply
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

  run zsh "$TA_WT" status
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

  run zsh "$TA_WT" status
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
  run zsh "$TA_WT" status
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

  run zsh "$TA_WT" status
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
  run zsh "$TA_WT" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat-status-current"* ]]
  [[ "$output" == *"current"* ]]
}

@test "wt status skips main branch" {
  cd "$PROJECT"
  # Only main — status should produce no output
  run zsh "$TA_WT" status
  [ "$status" -eq 0 ]
  [[ "$output" != *"main"* ]]
}

@test "wt status --json returns valid JSON" {
  cd "$PROJECT"
  git checkout -b feat-status-json
  git commit --allow-empty -m "json status"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-status-json" feat-status-json

  run zsh "$TA_WT" status --json
  [ "$status" -eq 0 ]

  # Validate JSON
  echo "$output" | jq . > /dev/null
  echo "$output" | jq -e '.[0] | has("branch", "status", "ahead", "behind", "dirty", "path")'
}

@test "wt status --json with no non-main worktrees returns empty array" {
  cd "$PROJECT"
  run zsh "$TA_WT" status --json
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

  run zsh "$TA_WT" merge feat-merge
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

  run zsh "$TA_WT" merge feat-merge-dirty
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

  run zsh "$TA_WT" merge feat-merge-dm
  [ "$status" -eq 1 ]
  [[ "$output" == *"target worktree 'main' has uncommitted changes"* ]]
}

@test "wt merge nonexistent branch fails with exit 2" {
  cd "$PROJECT"
  run zsh "$TA_WT" merge no-such-branch
  [ "$status" -eq 2 ]
  [[ "$output" == *"no worktree found"* ]]
}

@test "wt merge no args shows usage with exit 2" {
  cd "$PROJECT"
  run zsh "$TA_WT" merge
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

  run zsh "$TA_WT" merge feat-merge-conflict
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
  run zsh "$TA_WT" merge feat-merge-nows
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

  run zsh "$TA_WT" merge feat-merge-msg
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

  run zsh "$TA_WT" merge --target 8.x feature-8x
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

  run zsh "$TA_WT" merge --target no-such-target feat-no-target
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

  run zsh "$TA_WT" merge --target base-dirty-target feat-for-dirty-target
  [ "$status" -eq 1 ]
  [[ "$output" == *"target worktree 'base-dirty-target' has uncommitted changes"* ]]
}

@test "wt merge --target defaults to main" {
  cd "$PROJECT"
  git checkout -b feat-explicit-main
  echo "explicit main content" > explicit-main.txt
  git add explicit-main.txt
  git commit -m "add explicit main feature"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-explicit-main" feat-explicit-main

  run zsh "$TA_WT" merge --target main feat-explicit-main
  [ "$status" -eq 0 ]
  [[ "$output" == *"merged 'feat-explicit-main' into main"* ]]

  # Commit exists on main
  [ -f "$PROJECT/explicit-main.txt" ]
}
