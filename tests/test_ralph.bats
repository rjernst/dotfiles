#!/usr/bin/env bats

# Tests for scripts/ralph (host wrapper) argument parsing and validation.
# Stubs docker, git, and security so no container is actually started.

setup() {
  RALPH="${BATS_TEST_FILENAME%/*}/../scripts/ralph"
  export TMPDIR="$BATS_TEST_TMPDIR"

  # Create a project dir with a spec file in .ralph/specs/
  PROJECT="$BATS_TEST_TMPDIR/project"
  mkdir -p "$PROJECT/.ralph/specs"
  printf 'branch: test-branch\n\ntest prompt\n' > "$PROJECT/.ralph/specs/test.md"

  # Stub docker — record the command and exit
  DOCKER_LOG="$BATS_TEST_TMPDIR/docker.log"
  export DOCKER_LOG
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  cat > "$BATS_TEST_TMPDIR/bin/docker" <<'STUB'
#!/bin/bash
# Fail image inspect so builds always run
if [[ "$1" == "image" && "$2" == "inspect" ]]; then
  exit 1
fi
echo "$@" >> "$DOCKER_LOG"
# For 'build' print a fake image id
if [[ "$1" == "build" ]]; then
  echo "sha256:fake"
fi
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/docker"
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"

  # Stub security — return fake OAuth credentials
  cat > "$BATS_TEST_TMPDIR/bin/security" <<'STUB'
#!/bin/bash
echo '{"claudeAiOauth":{"accessToken":"fake-token"}}'
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/security"

  # Stub git — handles config, worktree, remote, ls-remote, rev-parse.
  # Control behavior via env vars: GIT_TOPLEVEL, GIT_WORKTREE_LIST,
  # GIT_REMOTE, GIT_LS_REMOTE_FOUND
  export GIT_TOPLEVEL=""
  export GIT_WORKTREE_LIST=""
  export GIT_REMOTE="origin"
  export GIT_LS_REMOTE_FOUND="0"
  cat > "$BATS_TEST_TMPDIR/bin/git" <<'STUB'
#!/bin/bash
# Handle -C <dir> prefix
if [[ "$1" == "-C" ]]; then
  shift 2
fi

case "$1" in
  config)
    echo "stub"
    ;;
  rev-parse)
    if [[ "$2" == "--show-toplevel" ]]; then
      echo "${GIT_TOPLEVEL:-$(pwd)}"
    elif [[ "$2" == "--git-common-dir" ]]; then
      echo "${GIT_TOPLEVEL:-$(pwd)}/.git"
    fi
    ;;
  worktree)
    case "$2" in
      list)
        echo "${GIT_WORKTREE_LIST:-}"
        ;;
      add)
        shift 2
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --track|--force|--detach) shift ;;
            -b|-B) shift 2 ;;
            *)
              mkdir -p "$1"
              break
              ;;
          esac
        done
        ;;
    esac
    ;;
  remote)
    echo "${GIT_REMOTE:-origin}"
    ;;
  ls-remote)
    if [[ "${GIT_LS_REMOTE_FOUND}" == "1" ]]; then
      exit 0
    else
      exit 2
    fi
    ;;
  *)
    command git "$@"
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/git"
}

# --- help / usage tests ---

@test "ralph --help shows usage" {
  run zsh "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"--prompt"* ]]
}

@test "ralph -h shows usage" {
  run zsh "$RALPH" -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
}

@test "ralph fails with unknown option" {
  cd "$PROJECT"
  run zsh "$RALPH" --bogus
  [ "$status" -eq 1 ]
  [[ "$output" == *"Unknown option"* ]]
}

# --- spec discovery tests ---

@test "ralph reads specs from .ralph/specs/" {
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  grep -q 'PROMPT_FILE=.ralph/specs/test.md' "$DOCKER_LOG"
}

@test "ralph fails when no spec files found" {
  cd "$BATS_TEST_TMPDIR"
  run zsh "$RALPH"
  [ "$status" -eq 1 ]
  [[ "$output" == *"no spec files found"* ]]
}

@test "ralph processes multiple specs sequentially" {
  printf 'branch: branch-a\n\nfirst spec\n' > "$PROJECT/.ralph/specs/aaa.md"
  printf 'branch: branch-b\n\nsecond spec\n' > "$PROJECT/.ralph/specs/bbb.md"
  rm "$PROJECT/.ralph/specs/test.md"
  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  grep -q 'PROMPT_FILE=.ralph/specs/aaa.md' "$DOCKER_LOG"
  grep -q 'PROMPT_FILE=.ralph/specs/bbb.md' "$DOCKER_LOG"
}

@test "ralph skips spec without branch directive" {
  # Add a spec without branch:
  printf '## Tasks\ndo stuff\n' > "$PROJECT/.ralph/specs/no-branch.md"
  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  # The spec without branch: should be skipped with a warning
  [[ "$output" == *"skipping .ralph/specs/no-branch.md"* ]]
  [[ "$output" == *"missing required 'branch:' directive"* ]]
  # The other spec (test.md) should still run
  grep -q 'PROMPT_FILE=.ralph/specs/test.md' "$DOCKER_LOG"
}

# --- --prompt override tests ---

@test "ralph --prompt accepts custom file" {
  mkdir -p "$PROJECT/specs"
  printf 'branch: spec-branch\n\nspec content\n' > "$PROJECT/specs/myspec.md"
  cd "$PROJECT"
  run zsh "$RALPH" --prompt specs/myspec.md
  [ "$status" -eq 0 ]
  # Docker run should have PROMPT_FILE=specs/myspec.md
  grep -q 'PROMPT_FILE=specs/myspec.md' "$DOCKER_LOG"
}

@test "ralph --prompt fails when file does not exist" {
  cd "$PROJECT"
  run zsh "$RALPH" --prompt no-such-file.md
  [ "$status" -eq 1 ]
  [[ "$output" == *"prompt file not found: no-such-file.md"* ]]
}

# --- option passing tests ---

@test "ralph passes default model to container" {
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  grep -q 'MODEL=sonnet' "$DOCKER_LOG"
}

@test "ralph --model passes custom model" {
  cd "$PROJECT"
  run zsh "$RALPH" --model opus
  [ "$status" -eq 0 ]
  grep -q 'MODEL=opus' "$DOCKER_LOG"
}

@test "ralph passes max iterations" {
  cd "$PROJECT"
  run zsh "$RALPH" 5
  [ "$status" -eq 0 ]
  grep -q 'MAX_ITERATIONS=5' "$DOCKER_LOG"
}

@test "ralph defaults to unlimited iterations" {
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  grep -q 'MAX_ITERATIONS=0' "$DOCKER_LOG"
}

@test "ralph --push passes PUSH=1" {
  cd "$PROJECT"
  run zsh "$RALPH" --push
  [ "$status" -eq 0 ]
  grep -q 'PUSH=1' "$DOCKER_LOG"
}

@test "ralph without --push passes PUSH=0" {
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  grep -q 'PUSH=0' "$DOCKER_LOG"
}

@test "ralph --packages uses custom image tag" {
  cd "$PROJECT"
  run zsh "$RALPH" --packages "nodejs openjdk-21-jdk"
  [ "$status" -eq 0 ]
  # Should have a build with EXTRA_PACKAGES and a custom tag
  grep -q 'EXTRA_PACKAGES=nodejs openjdk-21-jdk' "$DOCKER_LOG"
  grep -q 'ralph:custom-' "$DOCKER_LOG"
}

@test "ralph uses uid-based image tag without --packages" {
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  grep -q "ralph:uid-" "$DOCKER_LOG"
}

@test "ralph mounts project directory at /work" {
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  # The docker run command should contain the /work mount
  grep -q '/work' "$DOCKER_LOG"
}

# --- worktree tests ---

@test "ralph parses branch: from spec file" {
  printf 'branch: feature/test\n\n## Tasks\n- [ ] Do it\n' > "$PROJECT/.ralph/specs/test.md"
  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  [[ "$output" == *"using worktree"* ]]
  [[ "$output" == *"feature/test"* ]]
}

@test "ralph reuses existing worktree" {
  printf 'branch: feature/existing\n\n## Tasks\n' > "$PROJECT/.ralph/specs/test.md"
  # Simulate existing worktree at a known path
  local existing_path="$BATS_TEST_TMPDIR/project-feature-existing"
  mkdir -p "$existing_path"
  export GIT_TOPLEVEL="$PROJECT"
  # Porcelain format: stanza ends with blank line
  export GIT_WORKTREE_LIST="worktree $existing_path
HEAD abc1234567890
branch refs/heads/feature/existing"
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  # Docker should mount the existing worktree path
  grep -Fq "$existing_path:/work" "$DOCKER_LOG"
  # Spec should be copied into existing worktree
  [ -f "$existing_path/.ralph/specs/test.md" ]
}

@test "ralph creates worktree from remote branch" {
  printf 'branch: feature/remote\n\n## Tasks\n' > "$PROJECT/.ralph/specs/test.md"
  export GIT_TOPLEVEL="$PROJECT"
  export GIT_LS_REMOTE_FOUND="1"
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  # Worktree directory should be created
  local wt_path="$BATS_TEST_TMPDIR/project-feature-remote"
  [ -d "$wt_path" ]
  # Docker should mount worktree path
  grep -Fq "$wt_path:/work" "$DOCKER_LOG"
}

@test "ralph creates worktree from main when branch not on remote" {
  printf 'branch: feature/new\n\n## Tasks\n' > "$PROJECT/.ralph/specs/test.md"
  export GIT_TOPLEVEL="$PROJECT"
  export GIT_LS_REMOTE_FOUND="0"
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  # Worktree directory should be created
  local wt_path="$BATS_TEST_TMPDIR/project-feature-new"
  [ -d "$wt_path" ]
  grep -Fq "$wt_path:/work" "$DOCKER_LOG"
}

@test "ralph copies spec file into worktree" {
  printf 'branch: feature/copy\n\n## Tasks\n- [ ] task 1\n' > "$PROJECT/.ralph/specs/test.md"
  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  local wt_path="$BATS_TEST_TMPDIR/project-feature-copy"
  # Spec file should be copied to worktree
  [ -f "$wt_path/.ralph/specs/test.md" ]
  # Content should match
  grep -q "task 1" "$wt_path/.ralph/specs/test.md"
}

@test "ralph docker mount uses worktree path when branch specified" {
  printf 'branch: feature/mount\n\n## Tasks\n' > "$PROJECT/.ralph/specs/test.md"
  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  local wt_path="$BATS_TEST_TMPDIR/project-feature-mount"
  # Docker run should mount worktree, not project dir
  grep -Fq "$wt_path:/work" "$DOCKER_LOG"
}
