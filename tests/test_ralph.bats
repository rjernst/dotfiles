#!/usr/bin/env bats

# Tests for scripts/ralph (host wrapper) argument parsing and validation.
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

  # Stub gh — default no-op
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "stub"
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
  run zsh "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"--issue"* ]]
  [[ "$output" == *"--poll"* ]]
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

@test "ralph with no args shows usage" {
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 2 ]
  [[ "$output" == *"no mode specified"* ]]
  [[ "$output" == *"--issue"* ]]
  [[ "$output" == *"--poll"* ]]
}

# --- parse_duration tests ---

@test "parse_duration: plain number treated as seconds" {
  run zsh -c '
    eval "$(sed -n "/^parse_duration/,/^}/p" "'"$RALPH"'")"
    parse_duration 30
  '
  [ "$status" -eq 0 ]
  [ "$output" = "30" ]
}

@test "parse_duration: seconds suffix" {
  run zsh -c '
    eval "$(sed -n "/^parse_duration/,/^}/p" "'"$RALPH"'")"
    parse_duration 30s
  '
  [ "$status" -eq 0 ]
  [ "$output" = "30" ]
}

@test "parse_duration: minutes" {
  run zsh -c '
    eval "$(sed -n "/^parse_duration/,/^}/p" "'"$RALPH"'")"
    parse_duration 5m
  '
  [ "$status" -eq 0 ]
  [ "$output" = "300" ]
}

@test "parse_duration: hours" {
  run zsh -c '
    eval "$(sed -n "/^parse_duration/,/^}/p" "'"$RALPH"'")"
    parse_duration 2h
  '
  [ "$status" -eq 0 ]
  [ "$output" = "7200" ]
}

@test "parse_duration: days" {
  run zsh -c '
    eval "$(sed -n "/^parse_duration/,/^}/p" "'"$RALPH"'")"
    parse_duration 1d
  '
  [ "$status" -eq 0 ]
  [ "$output" = "86400" ]
}

@test "parse_duration: errors on invalid input" {
  run zsh -c '
    eval "$(sed -n "/^parse_duration/,/^}/p" "'"$RALPH"'")"
    parse_duration abc
  '
  [ "$status" -eq 2 ]
  [[ "$output" == *"invalid duration"* ]]
}

@test "parse_duration: errors on invalid suffix" {
  run zsh -c '
    eval "$(sed -n "/^parse_duration/,/^}/p" "'"$RALPH"'")"
    parse_duration 5x
  '
  [ "$status" -eq 2 ]
  [[ "$output" == *"invalid duration"* ]]
}

@test "parse_duration: errors on empty string" {
  run zsh -c '
    eval "$(sed -n "/^parse_duration/,/^}/p" "'"$RALPH"'")"
    parse_duration ""
  '
  [ "$status" -eq 2 ]
  [[ "$output" == *"invalid duration"* ]]
}

# --- parse_issue_branch tests ---

@test "parse_issue_branch extracts branch from title" {
  run zsh -c '
    eval "$(sed -n "/^parse_issue_branch/,/^}/p" "'"$RALPH"'")"
    parse_issue_branch "[my-branch] Some Title"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "my-branch" ]
}

@test "parse_issue_branch handles branches with slashes" {
  run zsh -c '
    eval "$(sed -n "/^parse_issue_branch/,/^}/p" "'"$RALPH"'")"
    parse_issue_branch "[feature/foo] Title"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "feature/foo" ]
}

@test "parse_issue_branch handles branches with numbers and hyphens" {
  run zsh -c '
    eval "$(sed -n "/^parse_issue_branch/,/^}/p" "'"$RALPH"'")"
    parse_issue_branch "[fix-123-bug] Title"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "fix-123-bug" ]
}

@test "parse_issue_branch errors on malformed title" {
  run zsh -c '
    eval "$(sed -n "/^parse_issue_branch/,/^}/p" "'"$RALPH"'")"
    parse_issue_branch "No brackets here"
  '
  [ "$status" -eq 1 ]
  [[ "$output" == *"cannot parse branch from issue title"* ]]
}

# --- parse_frontmatter tests ---

@test "parse_frontmatter extracts branch from frontmatter" {
  run zsh -c '
    eval "$(sed -n "/^parse_frontmatter/,/^}/p" "'"$RALPH"'")"
    body="---
branch: fix-auth
---
# Spec"
    parse_frontmatter "$body" "branch"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "fix-auth" ]
}

@test "parse_frontmatter extracts base from frontmatter" {
  run zsh -c '
    eval "$(sed -n "/^parse_frontmatter/,/^}/p" "'"$RALPH"'")"
    body="---
branch: fix-auth
base: 8.x
---
# Spec"
    parse_frontmatter "$body" "base"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "8.x" ]
}

@test "parse_frontmatter returns 1 when no frontmatter" {
  run zsh -c '
    eval "$(sed -n "/^parse_frontmatter/,/^}/p" "'"$RALPH"'")"
    parse_frontmatter "no frontmatter here" "branch"
  '
  [ "$status" -eq 1 ]
}

@test "parse_frontmatter returns 1 when field missing" {
  run zsh -c '
    eval "$(sed -n "/^parse_frontmatter/,/^}/p" "'"$RALPH"'")"
    body="---
branch: fix-auth
---
# Spec"
    parse_frontmatter "$body" "base"
  '
  [ "$status" -eq 1 ]
}

@test "parse_frontmatter ignores extra fields" {
  run zsh -c '
    eval "$(sed -n "/^parse_frontmatter/,/^}/p" "'"$RALPH"'")"
    body="---
branch: fix-auth
base: 8.x
extra: ignored
---
# Spec"
    parse_frontmatter "$body" "branch"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "fix-auth" ]
}

@test "parse_frontmatter handles whitespace after colon" {
  run zsh -c '
    eval "$(sed -n "/^parse_frontmatter/,/^}/p" "'"$RALPH"'")"
    body="---
branch:   fix-auth
---
# Spec"
    parse_frontmatter "$body" "branch"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "fix-auth" ]
}

# --- resolve_repo tests ---

@test "resolve_repo extracts repo from git remote origin URL" {
  GIT_LOG="$BATS_TEST_TMPDIR/git.log"
  export GIT_REPO="owner/repo"

  run zsh -c '
    export PATH="'"$BATS_TEST_TMPDIR/bin"':$PATH"
    eval "$(sed -n "/^resolve_repo/,/^}/p" "'"$RALPH"'")"
    resolve_repo
  '
  [ "$status" -eq 0 ]
  [ "$output" = "owner/repo" ]
}

# --- gh requirement test ---

@test "ralph fails when gh is not installed" {
  cd "$PROJECT"
  # Remove gh stub from PATH
  rm "$BATS_TEST_TMPDIR/bin/gh"
  # Ensure python3 is available
  ln -sf "$(command -v python3)" "$BATS_TEST_TMPDIR/bin/python3"
  local zsh_path
  zsh_path=$(command -v zsh)
  run env PATH="$BATS_TEST_TMPDIR/bin:/usr/bin" "$zsh_path" "$RALPH" --issue 1
  [ "$status" -eq 1 ]
  [[ "$output" == *"gh is not installed"* ]]
}

# --- option passing tests ---

@test "ralph --packages uses custom image tag" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[pkg-test] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "body"
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
  run zsh "$RALPH" --issue 1 --packages "nodejs openjdk-21-jdk"
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
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[uid-test] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "body"
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
  run zsh "$RALPH" --issue 1
  [ "$status" -eq 0 ]
  grep -q "ralph:uid-" "$DOCKER_LOG"
}

@test "ralph --push passes PUSH=1" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[push-test] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "body"
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
  run zsh "$RALPH" --issue 1 --push
  [ "$status" -eq 0 ]
  grep -q 'PUSH=1' "$DOCKER_LOG"
}

@test "ralph without --push passes PUSH=0" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[nopush-test] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "body"
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
  run zsh "$RALPH" --issue 1
  [ "$status" -eq 0 ]
  grep -q 'PUSH=0' "$DOCKER_LOG"
}

@test "ralph passes default model to container" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[model-test] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "body"
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
  run zsh "$RALPH" --issue 1
  [ "$status" -eq 0 ]
  grep -q 'MODEL=sonnet' "$DOCKER_LOG"
}

@test "ralph --model passes custom model" {
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
case "$1" in
  issue)
    case "$2" in
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[model-test] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "body"
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
  run zsh "$RALPH" --issue 1 --model opus
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
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[feature/remote] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "body"
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
  run zsh "$RALPH" --issue 1
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
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[feature/new] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "body"
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
  run zsh "$RALPH" --issue 1
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
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[feature/existing] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "body"
        fi
        ;;
      edit) ;;
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  cd "$PROJECT"
  run zsh "$RALPH" --issue 1
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
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "Fix Auth Middleware"
        elif [[ " $* " == *" -q .body "* ]]; then
          printf '%s\n%s\n%s\n%s' '---' 'branch: fix-auth' '---' '# Spec'
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
  run zsh "$RALPH" --issue 1
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
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "Fix Auth Middleware"
        elif [[ " $* " == *" -q .body "* ]]; then
          printf '%s\n%s\n%s\n%s' '---' 'branch: fix-auth' 'base: 8.x' '---'
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
  run zsh "$RALPH" --issue 1
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
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[fallback-branch] Old Style Title"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "No frontmatter body"
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
  run zsh "$RALPH" --issue 1
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
        if [[ " $* " == *" -q .title "* ]]; then
          echo "No Branch Anywhere"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "No frontmatter either"
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
  run zsh "$RALPH" --issue 1
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
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[test-issue] Test Feature"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "## Tasks\n- [ ] Do thing"
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
  run zsh "$RALPH" --issue 42
  [ "$status" -eq 0 ]
  # Should have fetched the issue title
  grep -q "issue view 42 --json title -q .title --repo owner/repo" "$GH_LOG"
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
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[spec-write-test] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "spec body content"
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
  run zsh "$RALPH" --issue 10
  [ "$status" -eq 0 ]
  # Spec should NOT be written to the worktree
  local wt_path="$BATS_TEST_TMPDIR/project-spec-write-test"
  [ ! -d "$wt_path/.ralph" ]
  # Temp file should be mounted at /tmp/spec.md
  grep -qE '[^ ]+:/tmp/spec.md' "$DOCKER_LOG"
}

@test "ralph --issue updates labels: ready→in-progress→done" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "$@" >> "$GH_LOG"
case "$1" in
  issue)
    case "$2" in
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[label-test] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "body"
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
  # GIT_HEAD stays constant so HEAD before == after → marks done
  run zsh "$RALPH" --issue 5
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
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[prompt-test] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "body"
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
  run zsh "$RALPH" --issue 7
  [ "$status" -eq 0 ]
  grep -q "PROMPT_FILE=/tmp/spec.md" "$DOCKER_LOG"
}

@test "ralph --poll and --issue together errors" {
  cd "$PROJECT"
  run zsh "$RALPH" --poll --issue 42
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
      view)
        if [[ " $* " == *" -q .title "* ]]; then
          echo "[fail-test] Test"
        elif [[ " $* " == *" -q .body "* ]]; then
          echo "body"
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
  run zsh "$RALPH" --issue 99
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
      list) echo "[]" ;;  # No ready issues
    esac
    ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"

  export GIT_TOPLEVEL="$PROJECT"
  cd "$PROJECT"
  # Use short interval and timeout to make the poll loop exit quickly
  run zsh "$RALPH" --poll --interval 1s --timeout 1s
  [ "$status" -eq 0 ]
  # Should have called gh issue list with correct flags
  grep -q 'issue list --label spec,status:ready --author @me --repo owner/repo --json number,title' "$GH_LOG"
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
  run zsh "$RALPH" --poll --interval 10s --timeout 1s
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
  run zsh "$RALPH" --poll --interval 1s --timeout 1s
  [ "$status" -eq 0 ]
  [[ "$output" == *"poll timeout reached"* ]]
}

@test "ralph --poll processes multiple ready issues sequentially" {
  export GH_LOG="$BATS_TEST_TMPDIR/gh.log"
  POLL_ITERATION=0
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo "$@" >> "$GH_LOG"
case "$1" in
  issue)
    case "$2" in
      list)
        # Return two issues on first call, empty on subsequent
        if [ ! -f "$BATS_TEST_TMPDIR/poll_done" ]; then
          touch "$BATS_TEST_TMPDIR/poll_done"
          echo '[{"number":10,"title":"[branch-a] First"},{"number":20,"title":"[branch-b] Second"}]'
        else
          echo "[]"
        fi
        ;;
      view)
        # Extract issue number from args
        num="$3"
        if [[ " $* " == *" -q .title "* ]]; then
          if [ "$num" = "10" ]; then
            echo "[branch-a] First"
          else
            echo "[branch-b] Second"
          fi
        elif [[ " $* " == *" -q .body "* ]]; then
          if [ "$num" = "10" ]; then
            printf '## Tasks\n- [ ] Do A'
          else
            printf '## Tasks\n- [ ] Do B'
          fi
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
  run zsh "$RALPH" --poll --interval 1s --timeout 3s
  [ "$status" -eq 0 ]
  # Should have processed both issues
  [[ "$output" == *"found ready issue #10"* ]]
  [[ "$output" == *"found ready issue #20"* ]]
  [[ "$output" == *"processing issue #10"* ]]
  [[ "$output" == *"processing issue #20"* ]]
}

@test "ralph --interval without --poll errors" {
  cd "$PROJECT"
  run zsh "$RALPH" --interval 10s
  [ "$status" -eq 2 ]
  [[ "$output" == *"--interval requires --poll"* ]]
}

@test "ralph --timeout without --poll errors" {
  cd "$PROJECT"
  run zsh "$RALPH" --timeout 1s
  [ "$status" -eq 2 ]
  [[ "$output" == *"--timeout requires --poll"* ]]
}

@test "ralph --help shows --poll option" {
  run zsh "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--poll"* ]]
  [[ "$output" == *"--interval"* ]]
}

@test "ralph --help shows --issue option" {
  run zsh "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--issue"* ]]
}
