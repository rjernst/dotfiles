#!/usr/bin/env bats

# Integration tests for scripts/ralph (Python rewrite).
# Unit tests for pure functions live in tests/test_ralph.py (pytest).
# Stubs docker, git, gh, and security so no container is actually started.

setup() {
  RALPH="${BATS_TEST_FILENAME%/*}/../scripts/ralph"
  export TMPDIR="$BATS_TEST_TMPDIR"

  # Create a project dir
  PROJECT="$BATS_TEST_TMPDIR/project"
  mkdir -p "$PROJECT"

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

  # Stub gh — default no-op (returns valid JSON)
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo '{}'
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  # Stub git — handles config, worktree, remote, ls-remote, rev-parse.
  # Control behavior via env vars: GIT_TOPLEVEL, GIT_WORKTREE_LIST,
  # GIT_REMOTE, GIT_LS_REMOTE_FOUND, GIT_REPO
  export GIT_TOPLEVEL=""
  export GIT_WORKTREE_LIST=""
  export GIT_REMOTE="origin"
  export GIT_LS_REMOTE_FOUND="0"
  export GIT_REPO="owner/repo"
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
    elif [[ "$2" == "HEAD" ]]; then
      echo "${GIT_HEAD:-abc1234567890}"
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
    if [[ "$2" == "get-url" ]]; then
      echo "git@github.com:${GIT_REPO:-owner/repo}.git"
    else
      echo "${GIT_REMOTE:-origin}"
    fi
    ;;
  ls-remote)
    if [[ "${GIT_LS_REMOTE_FOUND}" == "1" ]]; then
      exit 0
    else
      exit 2
    fi
    ;;
  symbolic-ref)
    if [[ "$2" == "--short" ]]; then
      echo "main"
    else
      echo "refs/remotes/origin/main"
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
  run "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"--issue"* ]]
  [[ "$output" == *"--poll"* ]]
}

@test "ralph -h shows usage" {
  run "$RALPH" -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
}

@test "ralph fails with unknown option" {
  cd "$PROJECT"
  run "$RALPH" --bogus
  [ "$status" -eq 1 ]
  [[ "$output" == *"unknown option"* ]]
}

@test "ralph with no args shows usage" {
  cd "$PROJECT"
  run "$RALPH"
  [ "$status" -eq 2 ]
  [[ "$output" == *"no mode specified"* ]]
  [[ "$output" == *"--issue"* ]]
  [[ "$output" == *"--poll"* ]]
}

# --- gh requirement test ---

@test "ralph fails when gh is not installed" {
  cd "$PROJECT"
  # Remove gh stub from PATH
  rm "$BATS_TEST_TMPDIR/bin/gh"
  # Ensure python3 is available
  ln -sf "$(command -v python3)" "$BATS_TEST_TMPDIR/bin/python3"
  run env PATH="$BATS_TEST_TMPDIR/bin:/usr/bin" "$RALPH" --issue 1
  [ "$status" -eq 1 ]
  [[ "$output" == *"gh is not installed"* ]]
}

# --- option passing tests ---

@test "ralph --packages uses custom image tag" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[pkg-test] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 1 --packages "nodejs openjdk-21-jdk"
  [ "$status" -eq 0 ]
  # Should have a build with EXTRA_PACKAGES and a custom tag
  grep -q 'EXTRA_PACKAGES=nodejs openjdk-21-jdk' "$DOCKER_LOG"
  grep -q 'ralph:custom-' "$DOCKER_LOG"
}

@test "ralph uses uid-based image tag without --packages" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[uid-test] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 1
  [ "$status" -eq 0 ]
  grep -q "ralph:uid-" "$DOCKER_LOG"
}

@test "ralph --push passes PUSH=1" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[push-test] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 1 --push
  [ "$status" -eq 0 ]
  grep -q 'PUSH=1' "$DOCKER_LOG"
}

@test "ralph without --push passes PUSH=0" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[nopush-test] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 1
  [ "$status" -eq 0 ]
  grep -q 'PUSH=0' "$DOCKER_LOG"
}

@test "ralph passes default model to container" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[model-test] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 1
  [ "$status" -eq 0 ]
  grep -q 'MODEL=sonnet' "$DOCKER_LOG"
}

@test "ralph --model passes custom model" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[model-test] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 1 --model opus
  [ "$status" -eq 0 ]
  grep -q 'MODEL=opus' "$DOCKER_LOG"
}

# --- worktree tests ---

@test "ralph --issue creates worktree from remote branch" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[feature/remote] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  export GIT_LS_REMOTE_FOUND="1"
  cd "$PROJECT"
  run "$RALPH" --issue 1
  [ "$status" -eq 0 ]
  local wt_path="$BATS_TEST_TMPDIR/project-feature-remote"
  [ -d "$wt_path" ]
  grep -Fq "$wt_path:/work" "$DOCKER_LOG"
}

@test "ralph --issue creates worktree from main when branch not on remote" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[feature/new] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  export GIT_LS_REMOTE_FOUND="0"
  cd "$PROJECT"
  run "$RALPH" --issue 1
  [ "$status" -eq 0 ]
  local wt_path="$BATS_TEST_TMPDIR/project-feature-new"
  [ -d "$wt_path" ]
  grep -Fq "$wt_path:/work" "$DOCKER_LOG"
}

@test "ralph --issue reuses existing worktree" {
  local existing_path="$BATS_TEST_TMPDIR/project-feature-existing"
  mkdir -p "$existing_path"
  export GIT_TOPLEVEL="$PROJECT"
  export GIT_WORKTREE_LIST="worktree $existing_path
HEAD abc1234567890
branch refs/heads/feature/existing"

  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[feature/existing] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  cd "$PROJECT"
  run "$RALPH" --issue 1
  [ "$status" -eq 0 ]
  grep -Fq "$existing_path:/work" "$DOCKER_LOG"
}

# --- frontmatter integration tests ---

@test "ralph --issue uses branch from frontmatter" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "Fix Auth Middleware"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "---\nbranch: fix-auth\n---\n# Spec"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 1
  [ "$status" -eq 0 ]
  [[ "$output" == *"processing issue #1 on branch fix-auth"* ]]
}

@test "ralph --issue uses base branch from frontmatter" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  # Track what git worktree add receives
  cat > "$BATS_TEST_TMPDIR/bin/git" <<'STUB'
#!/bin/bash
if [[ "$1" == "-C" ]]; then shift 2; fi
case "$1" in
  config) echo "stub" ;;
  rev-parse)
    if [[ "$2" == "--show-toplevel" ]]; then echo "${GIT_TOPLEVEL:-$(pwd)}"
    elif [[ "$2" == "--git-common-dir" ]]; then echo "${GIT_TOPLEVEL:-$(pwd)}/.git"
    elif [[ "$2" == "--verify" ]]; then exit 0
    elif [[ "$2" == "HEAD" ]]; then echo "abc1234567890"
    fi ;;
  worktree)
    case "$2" in
      list) echo "${GIT_WORKTREE_LIST:-}" ;;
      add)
        echo "WORKTREE_ADD: $@" >> "$GH_LOG"
        shift 2
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --track|--force|--detach) shift ;;
            -b|-B) shift 2 ;;
            *) mkdir -p "$1"; break ;;
          esac
        done ;;
    esac ;;
  remote)
    if [[ "$2" == "get-url" ]]; then echo "git@github.com:${GIT_REPO:-owner/repo}.git"
    else echo "${GIT_REMOTE:-origin}"
    fi ;;
  ls-remote) exit 2 ;;
  symbolic-ref)
    if [[ "$2" == "--short" ]]; then echo "main"
    else echo "refs/remotes/origin/main"
    fi ;;
  *) command git "$@" ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/git"

  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "Fix Auth Middleware"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "---\nbranch: fix-auth\nbase: 8.x\n---"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 1
  [ "$status" -eq 0 ]
  # Verify worktree add used the base branch (8.x)
  grep -q 'WORKTREE_ADD.*8.x' "$GH_LOG"
}

@test "ralph --issue falls back to title branch when no frontmatter" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[fallback-branch] Old Style Title"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "No frontmatter body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 1
  [ "$status" -eq 0 ]
  [[ "$output" == *"processing issue #1 on branch fallback-branch"* ]]
}

@test "ralph --issue errors when no branch in frontmatter or title" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "No Branch Anywhere"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "No frontmatter either"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 1
  [ "$status" -eq 1 ]
  [[ "$output" == *"cannot parse branch"* ]]
}

# --- --issue flag tests ---

@test "ralph --issue fetches issue, creates worktree, runs container" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "$@" >> "$GH_LOG"
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[test-issue] Test Feature"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "## Tasks\n- [ ] Do thing"}'
        fi
        ;;
      edit)
        ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 42
  [ "$status" -eq 0 ]
  # Should have fetched the issue title
  grep -q "issue view 42 --json title --repo owner/repo" "$GH_LOG"
  # Should have run docker
  grep -q "PROMPT_FILE=/tmp/spec.md" "$DOCKER_LOG"
  # Should reference the branch
  [[ "$output" == *"processing issue #42 on branch test-issue"* ]]
  [[ "$output" == *"using worktree"* ]]
}

@test "ralph --issue writes spec to temp file mounted into container" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[spec-write-test] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "spec body content"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 10
  [ "$status" -eq 0 ]
  # Spec should NOT be written to the worktree
  local wt_path="$BATS_TEST_TMPDIR/project-spec-write-test"
  [ ! -d "$wt_path/.ralph" ]
  # Temp file should be mounted at /tmp/spec.md
  grep -qE '[^ ]+:/tmp/spec.md' "$DOCKER_LOG"
}

@test "ralph --issue updates labels: ready->in-progress->done" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "$@" >> "$GH_LOG"
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[label-test] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  # GIT_HEAD stays constant so HEAD before == after -> marks done
  run "$RALPH" --issue 5
  [ "$status" -eq 0 ]
  # Should have set in-progress
  grep -q "issue edit 5 --remove-label status:ready --add-label status:in-progress" "$GH_LOG"
  # Should have set done (since no commit was made)
  grep -q "issue edit 5 --remove-label status:in-progress --add-label status:done" "$GH_LOG"
}

@test "ralph --issue passes correct PROMPT_FILE to container" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[prompt-test] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 7
  [ "$status" -eq 0 ]
  grep -q "PROMPT_FILE=/tmp/spec.md" "$DOCKER_LOG"
}

@test "ralph --poll and --issue together errors" {
  cd "$PROJECT"
  run "$RALPH" --poll --issue 42
  [ "$status" -eq 2 ]
  [[ "$output" == *"--poll and --issue cannot be used together"* ]]
}

@test "ralph --issue labels needs-attention on container failure" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "$@" >> "$GH_LOG"
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[fail-test] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  # Make docker run fail
  cat > "$BATS_TEST_TMPDIR/bin/docker" <<'STUB'
#!/bin/bash
if [[ "$1" == "image" && "$2" == "inspect" ]]; then
  exit 1
fi
echo "$@" >> "$DOCKER_LOG"
if [[ "$1" == "build" ]]; then
  echo "sha256:fake"
  exit 0
fi
if [[ "$1" == "run" ]]; then
  exit 1
fi
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/docker"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 99
  [ "$status" -eq 1 ]
  grep -q "issue edit 99 --remove-label status:in-progress --add-label status:needs-attention" "$GH_LOG"
}

# --- --poll flag tests ---

@test "ralph --poll calls gh issue list with correct labels and author" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "$@" >> "$GH_LOG"
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  # Use short interval and timeout to make the poll loop exit quickly
  run "$RALPH" --poll --interval 1s --timeout 1s
  [ "$status" -eq 0 ]
  # Should have called gh issue list with correct flags
  grep -q 'issue list --label spec --label status:ready --author @me --repo owner/repo --json number' "$GH_LOG"
}

@test "ralph --poll --interval 10s uses custom interval" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "$@" >> "$GH_LOG"
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  # --timeout 1s ensures exit before the 10s interval sleep
  run "$RALPH" --poll --interval 10s --timeout 1s
  [ "$status" -eq 0 ]
  [[ "$output" == *"poll timeout reached"* ]]
}

@test "ralph --poll --timeout 1s exits after timeout" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --poll --interval 1s --timeout 1s
  [ "$status" -eq 0 ]
  [[ "$output" == *"poll timeout reached"* ]]
}

@test "ralph --poll processes multiple ready issues sequentially" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "$@" >> "$GH_LOG"
case "$1" in
  issue)
    case "$2" in
      list)
        if [[ " $* " == *"status:blocked"* ]]; then
          # unblock_ready_specs: no blocked issues
          echo "[]"
        elif [ ! -f "$BATS_TEST_TMPDIR/poll_done" ]; then
          # First ready-issue query: return two issue numbers
          touch "$BATS_TEST_TMPDIR/poll_done"
          echo '[{"number": 10}, {"number": 20}]'
        else
          echo "[]"
        fi
        ;;
      view)
        # Extract issue number from args
        num="$3"
        if [[ " $* " == *" --json title "* ]]; then
          if [ "$num" = "10" ]; then
            echo '{"title": "[branch-a] First"}'
          else
            echo '{"title": "[branch-b] Second"}'
          fi
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "## Tasks"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --poll --interval 1s --timeout 3s
  [ "$status" -eq 0 ]
  # Should have processed both issues
  [[ "$output" == *"found ready issue #10"* ]]
  [[ "$output" == *"found ready issue #20"* ]]
  [[ "$output" == *"processing issue #10"* ]]
  [[ "$output" == *"processing issue #20"* ]]
}

@test "ralph --poll calls unblock_ready_specs at start of each cycle" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "$@" >> "$GH_LOG"
case "$1" in
  issue)
    case "$2" in
      list) echo "[]" ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --poll --interval 1s --timeout 1s
  [ "$status" -eq 0 ]
  # Should have called gh issue list for blocked issues (unblock_ready_specs)
  grep -q 'issue list --label status:blocked --label spec --repo owner/repo --json number' "$GH_LOG"
}

@test "ralph --issue calls unblock_ready_specs after marking done" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "$@" >> "$GH_LOG"
case "$1" in
  issue)
    case "$2" in
      list)
        # For unblock_ready_specs call — return empty
        echo "[]"
        ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[done-unblock-test] Test"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "body"}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  # GIT_HEAD stays constant so HEAD before == after -> marks done
  run "$RALPH" --issue 5
  [ "$status" -eq 0 ]
  # Should have set done
  grep -q "issue edit 5 --remove-label status:in-progress --add-label status:done" "$GH_LOG"
  # Should have called unblock_ready_specs after done (scans blocked issues)
  grep -q 'issue list --label status:blocked --label spec --repo owner/repo --json number' "$GH_LOG"
}

@test "ralph --issue blocks spec with unmet dependencies" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "$@" >> "$GH_LOG"
case "$1" in
  issue)
    case "$2" in
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[deps-test] Test with deps"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "---\nbranch: deps-test\ndepends: [11]\n---\nSome spec"}'
        elif [[ " $* " == *" --json labels "* ]]; then
          # Issue 11 is NOT done
          echo '{"labels": [{"name": "spec"}, {"name": "status:in-progress"}]}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 42
  [ "$status" -eq 0 ]
  # Should have transitioned to blocked
  grep -q "issue edit 42 --remove-label status:ready --add-label status:blocked" "$GH_LOG"
  # Should report unmet dependencies
  [[ "$output" == *"unmet dependencies"* ]]
  [[ "$output" == *"status:blocked"* ]]
  # Should NOT have run docker (no container started)
  [ ! -f "$DOCKER_LOG" ] || ! grep -q "run" "$DOCKER_LOG"
}

@test "ralph --issue proceeds when all dependencies are met" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "$@" >> "$GH_LOG"
case "$1" in
  issue)
    case "$2" in
      list)
        echo "[]"
        ;;
      view)
        if [[ " $* " == *" --json title "* ]]; then
          echo '{"title": "[deps-ok-test] Test with met deps"}'
        elif [[ " $* " == *" --json body "* ]]; then
          echo '{"body": "---\nbranch: deps-ok-test\ndepends: [11]\n---\nSome spec"}'
        elif [[ " $* " == *" --json labels "* ]]; then
          # Issue 11 IS done
          echo '{"labels": [{"name": "spec"}, {"name": "status:done"}]}'
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  run "$RALPH" --issue 42
  [ "$status" -eq 0 ]
  # Should NOT have transitioned to blocked
  ! grep -q "status:blocked" "$GH_LOG"
  # Should have proceeded to process (docker run)
  [[ "$output" == *"processing issue #42"* ]]
}

@test "ralph --interval without --poll errors" {
  cd "$PROJECT"
  run "$RALPH" --interval 10s
  [ "$status" -eq 2 ]
  [[ "$output" == *"--interval requires --poll"* ]]
}

@test "ralph --timeout without --poll errors" {
  cd "$PROJECT"
  run "$RALPH" --timeout 1s
  [ "$status" -eq 2 ]
  [[ "$output" == *"--timeout requires --poll"* ]]
}

@test "ralph --help shows --poll option" {
  run "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--poll"* ]]
  [[ "$output" == *"--interval"* ]]
}

@test "ralph --help shows --issue option" {
  run "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--issue"* ]]
}
