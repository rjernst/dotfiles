#!/bin/bash
set -e

# The host gitconfig is mounted read-only, so create a writable copy
RALPH_GITCONFIG="$HOME/.ralph-gitconfig"
if [[ -f "$HOME/.gitconfig" ]]; then
  cp "$HOME/.gitconfig" "$RALPH_GITCONFIG"
else
  touch "$RALPH_GITCONFIG"
fi
export GIT_CONFIG_GLOBAL="$RALPH_GITCONFIG"

git config --global user.name "${GIT_USER:-ralph}"
git config --global user.email "${GIT_EMAIL:-ralph@localhost}"
git config --global --add safe.directory /work

ITERATION=0
while true; do
  ITERATION=$((ITERATION + 1))

  if [[ ${MAX_ITERATIONS:-0} -gt 0 && $ITERATION -gt $MAX_ITERATIONS ]]; then
    echo "ralph: reached max iterations ($MAX_ITERATIONS)"
    break
  fi

  echo "══════════════════════════════════════════"
  echo "  ralph: iteration $ITERATION"
  echo "══════════════════════════════════════════"

  claude -p \
    --dangerously-skip-permissions \
    --model "${MODEL:-sonnet}" \
    < "$PROMPT_FILE" || {
      echo "ralph: claude exited with error ($?), continuing..."
    }

  if [[ ${PUSH:-0} -eq 1 ]]; then
    git push || echo "ralph: push failed, continuing..."
  fi
done
