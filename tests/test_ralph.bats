#!/usr/bin/env bats

# Tests for scripts/ralph (host wrapper) argument parsing and validation.
# Stubs docker so no container is actually started.

setup() {
  RALPH="${BATS_TEST_FILENAME%/*}/../scripts/ralph"
  export TMPDIR="$BATS_TEST_TMPDIR"

  # Create a project dir with a prompt file
  PROJECT="$BATS_TEST_TMPDIR/project"
  mkdir -p "$PROJECT"
  echo "test prompt" > "$PROJECT/PROMPT.md"

  # Stub docker — record the command and exit
  DOCKER_LOG="$BATS_TEST_TMPDIR/docker.log"
  export DOCKER_LOG
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  cat > "$BATS_TEST_TMPDIR/bin/docker" <<'STUB'
#!/bin/bash
echo "$@" >> "$DOCKER_LOG"
# For 'build -q' print a fake image id
if [[ "$1" == "build" ]]; then
  echo "sha256:fake"
fi
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/docker"
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"

  # Stub git config to avoid depending on host git config
  cat > "$BATS_TEST_TMPDIR/bin/git" <<'STUB'
#!/bin/bash
if [[ "$1" == "config" ]]; then
  echo "stub"
else
  command git "$@"
fi
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/git"
}

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

@test "ralph fails when prompt file is missing" {
  cd "$BATS_TEST_TMPDIR"
  run zsh "$RALPH"
  [ "$status" -eq 1 ]
  [[ "$output" == *"prompt file not found"* ]]
}

@test "ralph fails with unknown option" {
  cd "$PROJECT"
  run zsh "$RALPH" --bogus
  [ "$status" -eq 1 ]
  [[ "$output" == *"Unknown option"* ]]
}

@test "ralph --prompt accepts custom file" {
  mkdir -p "$PROJECT/specs"
  echo "spec content" > "$PROJECT/specs/myspec.md"
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

@test "ralph builds ralph:latest without --packages" {
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  grep -q 'ralph:latest' "$DOCKER_LOG"
}

@test "ralph prompt file must be inside current directory" {
  echo "outside prompt" > "$BATS_TEST_TMPDIR/outside.md"
  cd "$PROJECT"
  run zsh "$RALPH" --prompt "$BATS_TEST_TMPDIR/outside.md"
  [ "$status" -eq 1 ]
  [[ "$output" == *"prompt file must be inside the current directory"* ]]
}

@test "ralph mounts project directory at /work" {
  cd "$PROJECT"
  run zsh "$RALPH"
  [ "$status" -eq 0 ]
  # The docker run command should contain the PWD:/work mount
  grep -q '/work' "$DOCKER_LOG"
}

