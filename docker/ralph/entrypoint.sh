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
    <<PROMPT || echo "ralph: claude exited with error ($?), continuing..."
You are an AI coding agent. You will be invoked repeatedly — once per task.
Read the spec file at \`$PROMPT_FILE\` for what to build.

Your job this iteration: implement EXACTLY ONE task, then stop.

Steps:
1. Study the spec and existing codebase (especially CLAUDE.md) to understand patterns
2. Check git log to see what has already been implemented
3. Pick the FIRST incomplete task from the spec
4. Implement that single task fully — no stubs or placeholders
5. Write tests as appropriate for the project
6. Run the tests
7. If tests pass, mark ONLY that task done in the spec file and commit
8. If tests fail, fix before committing

IMPORTANT: Do NOT implement more than one task. After committing, stop immediately.
The loop will call you again for the next task.

Rules:
- One task, one commit, then stop
- Follow conventions in CLAUDE.md if it exists
- Search the codebase before assuming something isn't implemented
PROMPT

  if [[ ${PUSH:-0} -eq 1 ]]; then
    git push || echo "ralph: push failed, continuing..."
  fi
done
