#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for zsh functions: _find_latest_jdk, _find_pipenv,
# _dotfiles_check_updates, and _set_env

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  export DOTFILES="$(cd "${BATS_TEST_FILENAME%/*}/.." && pwd)"
  export MOCK_BIN="$BATS_TEST_TMPDIR/mock_bin"
  mkdir -p "$HOME" "$MOCK_BIN"

  # Create mock jenv binary that handles init (no-op) and versions (cats data file)
  cat > "$MOCK_BIN/jenv" <<'SCRIPT'
#!/bin/bash
if [ "$1" = "init" ]; then
  exit 0
elif [ "$1" = "versions" ]; then
  cat "$JENV_DATA_FILE"
  exit 0
fi
SCRIPT
  chmod +x "$MOCK_BIN/jenv"

  # Mock security (macOS keychain) — returns a fake API key
  cat > "$MOCK_BIN/security" <<'SCRIPT'
#!/bin/bash
echo "fake-api-key"
SCRIPT
  chmod +x "$MOCK_BIN/security"

  # Mock curl, ping, tsh — no-op success stubs for _set_env tests
  for cmd in curl ping tsh; do
    printf '#!/bin/bash\nexit 0\n' > "$MOCK_BIN/$cmd"
    chmod +x "$MOCK_BIN/$cmd"
  done
}

# --- _find_latest_jdk tests ---

@test "_find_latest_jdk: multiple standard versions" {
  export JENV_DATA_FILE="$BATS_TEST_TMPDIR/jenv_versions"
  cat > "$JENV_DATA_FILE" <<'EOF'
  system
  17
  17.0
  17.0.12
  21
  21.0
  21.0.5
* 23 (set by /home/user/.jenv/version)
  23.0
EOF

  run zsh "${BATS_TEST_FILENAME%/*}/helpers/find_latest_jdk.zsh"

  [ "$status" -eq 0 ]
  [[ "${lines[0]}" == "LATEST=23" ]]
}

@test "_find_latest_jdk: single version" {
  export JENV_DATA_FILE="$BATS_TEST_TMPDIR/jenv_versions"
  cat > "$JENV_DATA_FILE" <<'EOF'
  system
* 21 (set by /home/user/.jenv/version)
  21.0
EOF

  run zsh "${BATS_TEST_FILENAME%/*}/helpers/find_latest_jdk.zsh"

  [ "$status" -eq 0 ]
  [[ "${lines[0]}" == "LATEST=21" ]]
}

@test "_find_latest_jdk: system only" {
  export JENV_DATA_FILE="$BATS_TEST_TMPDIR/jenv_versions"
  cat > "$JENV_DATA_FILE" <<'EOF'
* system (set by /home/user/.jenv/version)
EOF

  run zsh "${BATS_TEST_FILENAME%/*}/helpers/find_latest_jdk.zsh"

  [ "$status" -eq 0 ]
  [[ "${lines[0]}" == "LATEST=" ]]
}

@test "_find_latest_jdk: includes single-digit version" {
  export JENV_DATA_FILE="$BATS_TEST_TMPDIR/jenv_versions"
  cat > "$JENV_DATA_FILE" <<'EOF'
  system
  8
  8.0
  17
  17.0
* 21 (set by /home/user/.jenv/version)
  21.0
EOF

  run zsh "${BATS_TEST_FILENAME%/*}/helpers/find_latest_jdk.zsh"

  [ "$status" -eq 0 ]
  [[ "${lines[0]}" == "LATEST=21" ]]
  [[ "$output" == *"VERSION=8"* ]]
}

# --- _find_pipenv tests ---

@test "_find_pipenv: Pipfile in current directory" {
  local project="$BATS_TEST_TMPDIR/project"
  mkdir -p "$project"
  touch "$project/Pipfile"

  run zsh "${BATS_TEST_FILENAME%/*}/helpers/find_pipenv.zsh" "$project"

  [ "$status" -eq 0 ]
  [[ "$output" == *"PIPENV_ROOT=$project"* ]]
}

@test "_find_pipenv: Pipfile in parent directory" {
  local project="$BATS_TEST_TMPDIR/project"
  mkdir -p "$project/sub"
  touch "$project/Pipfile"

  run zsh "${BATS_TEST_FILENAME%/*}/helpers/find_pipenv.zsh" "$project/sub"

  [ "$status" -eq 0 ]
  [[ "$output" == *"PIPENV_ROOT=$project"* ]]
}

@test "_find_pipenv: Pipfile in grandparent directory" {
  local project="$BATS_TEST_TMPDIR/project"
  mkdir -p "$project/a/b"
  touch "$project/Pipfile"

  run zsh "${BATS_TEST_FILENAME%/*}/helpers/find_pipenv.zsh" "$project/a/b"

  [ "$status" -eq 0 ]
  [[ "$output" == *"PIPENV_ROOT=$project"* ]]
}

@test "_find_pipenv: no Pipfile" {
  local empty="$BATS_TEST_TMPDIR/empty_tree/a/b"
  mkdir -p "$empty"

  run zsh "${BATS_TEST_FILENAME%/*}/helpers/find_pipenv.zsh" "$empty"

  [ "$status" -eq 0 ]
  [[ "$output" == *"PIPENV_ROOT=UNSET"* ]]
}

@test "_find_pipenv: nearest Pipfile wins" {
  local outer="$BATS_TEST_TMPDIR/outer"
  mkdir -p "$outer/inner/sub"
  touch "$outer/Pipfile"
  touch "$outer/inner/Pipfile"

  run zsh "${BATS_TEST_FILENAME%/*}/helpers/find_pipenv.zsh" "$outer/inner/sub"

  [ "$status" -eq 0 ]
  [[ "$output" == *"PIPENV_ROOT=$outer/inner"* ]]
}

# --- _dotfiles_check_updates tests ---

@test "_dotfiles_check_updates: displays notification when behind" {
  export ZDOTDIR="$BATS_TEST_TMPDIR/zdotdir"
  mkdir -p "$ZDOTDIR/var"
  echo "3" > "$ZDOTDIR/var/dotfiles-behind-count"
  echo "$(date +%s)" > "$ZDOTDIR/var/dotfiles-fetch-stamp"

  run zsh "${BATS_TEST_FILENAME%/*}/helpers/dotfiles_check_updates.zsh"

  [ "$status" -eq 0 ]
  [[ "$output" == *"3 commit(s) behind"* ]]
  [[ "$output" == *"CACHE_EXISTS=false"* ]]
}

@test "_dotfiles_check_updates: no notification when count is zero" {
  export ZDOTDIR="$BATS_TEST_TMPDIR/zdotdir"
  mkdir -p "$ZDOTDIR/var"
  echo "0" > "$ZDOTDIR/var/dotfiles-behind-count"
  echo "$(date +%s)" > "$ZDOTDIR/var/dotfiles-fetch-stamp"

  run zsh "${BATS_TEST_FILENAME%/*}/helpers/dotfiles_check_updates.zsh"

  [ "$status" -eq 0 ]
  [[ ! "$output" == *"commit(s) behind"* ]]
  [[ "$output" == *"CACHE_EXISTS=false"* ]]
}

@test "_dotfiles_check_updates: skips fetch when recently checked" {
  export ZDOTDIR="$BATS_TEST_TMPDIR/zdotdir"
  mkdir -p "$ZDOTDIR/var"
  local stamp
  stamp=$(date +%s)
  echo "$stamp" > "$ZDOTDIR/var/dotfiles-fetch-stamp"

  run zsh "${BATS_TEST_FILENAME%/*}/helpers/dotfiles_check_updates.zsh"

  [ "$status" -eq 0 ]
  [[ "$output" == *"STAMP_VALUE=$stamp"* ]]
}

# --- _set_env tests ---

@test "_set_env: rejects missing argument" {
  run zsh "${BATS_TEST_FILENAME%/*}/helpers/set_env.zsh"

  [ "$status" -eq 0 ]
  [[ "$output" == *"RC=1"* ]]
  [[ "$output" == *"Expected a single env parameter"* ]]
}

@test "_set_env: rejects invalid env name" {
  run zsh "${BATS_TEST_FILENAME%/*}/helpers/set_env.zsh" "invalid"

  [ "$status" -eq 0 ]
  [[ "$output" == *"RC=2"* ]]
  [[ "$output" == *"Illegal env name"* ]]
}

@test "_set_env: sets prod environment variables" {
  run zsh "${BATS_TEST_FILENAME%/*}/helpers/set_env.zsh" "prod"

  [ "$status" -eq 0 ]
  [[ "$output" == *"RC=0"* ]]
  [[ "$output" == *"ENV_NAME=prod"* ]]
  [[ "$output" == *"TSH_PROXY=teleport-proxy.secops.elstc.co"* ]]
  [[ "$output" == *"ENV_URL=https://adminconsole.found.no"* ]]
  [[ "$output" == *"API_KEY=fake-api-key"* ]]
}

@test "_set_env: sets staging environment variables" {
  run zsh "${BATS_TEST_FILENAME%/*}/helpers/set_env.zsh" "staging"

  [ "$status" -eq 0 ]
  [[ "$output" == *"RC=0"* ]]
  [[ "$output" == *"TSH_PROXY=teleport-proxy.staging.getin.cloud"* ]]
  [[ "$output" == *"ENV_URL=https://admin.public-api.staging.foundit.no"* ]]
}
