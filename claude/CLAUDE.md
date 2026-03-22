# User Preferences

## Git Commits

- Never add `Co-Authored-By` trailers to commit messages.
- Commit messages should never be bullet lists. Write a concise conceptual explanation of *what* and *why* the commit changes something. Don't enumerate exact changes — the diff speaks for itself.

## Branch Safety

- **Never do development work on the main/master branch.** Before making code changes, check the current branch. If on `main` or `master`, stop and ask the user to switch to a worktree or feature branch first.
- Use `ta wt create <branch>` (or `ta wt create <branch> --from=<base>` to create a new local branch) to create a worktree, or suggest the user create a feature branch.
- The only exceptions are trivial config-only changes the user explicitly requests to make directly on main (e.g., editing CLAUDE.md).
