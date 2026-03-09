# Dotfiles Repository

Personal dotfiles for configuring macOS and Linux (Arch) development machines.
Primarily used for Elasticsearch development at Elastic.

## Architecture

### Two-Phase Setup

- **`bootstrap`**: One-time machine provisioning. Detects platform (macOS/Arch Linux) and architecture (arm64/x86_64), installs dependencies via Homebrew or pacman, creates SSH keys (`id_ed25519` and `github_ed25519`), clones this repo, then runs `setup`.
- **`setup`**: Idempotent configuration linker. Symlinks all config files into `$HOME`, loads host-specific roles, and sets up per-host configuration. Safe to re-run at any time.

### Role-Based Configuration

Machines are configured through a **host → roles** mapping:

```
hosts/<hostname>/roles    # lists role names, one per line
roles/<role>/install      # optional: first-time setup, runs before setup (sourced)
roles/<role>/setup        # optional: runs during setup (sourced)
roles/<role>/zsh_plugin   # optional: symlinked to ~/.zsh/plugins/<role>.zsh
roles/<role>/requires     # optional: lists role dependencies, one per line
roles/<role>/hooks/       # optional: git hooks installed by the role's setup script
```

The `setup` script reads `hosts/$(hostname)/roles` to determine which roles to activate. Each role can provide a setup script and/or a zsh plugin that gets sourced on shell startup.

### Current Hosts

| Host       | Roles                                       | Notes                        |
|------------|---------------------------------------------|------------------------------|
| prometheus | git, elasticsearch, elasticsearch-support, java, jdk | Full ES dev + OpenJDK work |
| charlotte  | git, elasticsearch, elasticsearch-support, java      | ES development             |
| maelle     | git, elasticsearch, elasticsearch-support, java      | ES development             |
| pandora    | git                                                  | Minimal setup              |

### Current Roles

| Role                   | Purpose                                                    |
|------------------------|------------------------------------------------------------|
| git                    | Git user config, SSH commit signing, allowed signers setup |
| elasticsearch          | Gradle init script for ES builds, project directory aliases, pre-push hook|
| elasticsearch-support  | GCloud auth plugin, Teleport/k8s env switching             |
| java                   | jenv initialization, JDK scanning/installation helpers     |
| jdk                    | OpenJDK development shortcut (`cdj`)                       |
| node                   | fnm (Fast Node Manager) initialization                     |

## Directory Structure

```
bootstrap              # One-time machine setup script
setup                  # Idempotent config linker (symlinks everything)
hosts/                 # Per-machine configuration
  <hostname>/
    roles              # List of roles to activate
    brewfile           # Homebrew packages (macOS only)
    ssh_host_config    # Host-specific SSH config (optional)
roles/                 # Modular configuration units
  <role>/
    setup              # Run during setup (optional)
    zsh_plugin         # Sourced in shell (optional)
    install            # First-time install script (optional)
    requires           # Role dependencies (optional)
    hooks/             # Git hooks installed by setup (optional)
git/
  config               # Global gitconfig (symlinked to ~/.gitconfig)
  ignore               # Global gitignore
zsh/
  zshrc                # Main shell config (symlinked to ~/.zshrc)
  plugins/             # Shared zsh plugins (always loaded)
    pipenv.zsh         # Auto-activates pipenv environments on cd
ssh/
  config               # Base SSH config (symlinked to ~/.ssh/config)
vim/
  vimrc                # Vim config with vim-plug, NERDTree, lightline
gradle/
  properties           # Global gradle.properties (caching enabled)
  elasticsearch.gradle # Gradle Enterprise / Develocity build scans
hooks/
  pre-commit           # Shellcheck + zsh syntax checks on staged files
claude/
  CLAUDE.md            # Global Claude Code instructions (symlinked to ~/.claude/CLAUDE.md)
  skills/              # Claude Code skills (symlinked to ~/.claude/skills)
    create-spec/       # /create-spec — interactive Ralph spec generator
scripts/
  ta                   # Terminal Agent tool (subcommand dispatcher, symlinked to ~/bin/ta)
  ta-wt                # Worktree manager (list, create, remove, prune, status)
  ta-workspace         # Tmux workspace session manager (create, list, attach, kill)
  ta-ff                # Fork-and-focus session creator
  ta-report            # Session report generator (markdown output)
  ta-tmux              # Tmux introspection (sessions, windows, panes, capture)
  gradlew.sh           # Find and run gradlew from any subdirectory
  git-make-worktree    # Deprecated: wrapper for ta wt create
  git-prune-branches   # Clean up merged branches via GitHub API
  detect-platform      # Platform detection helper
  update-dotfiles      # Pull and re-run setup
  macos/               # macOS system preference scripts
```

## Key Conventions

- **Symlink-based**: All config files live in this repo and are symlinked to `$HOME`. Never edit config files in `$HOME` directly.
- **Shell**: Zsh with Zinit plugin manager, Starship prompt.
- **Git signing**: Commits are signed with SSH keys (not GPG). The `git` role sets up `~/.git/user.config` and `~/.ssh/allowed_signers`.
- **Git aliases**: Single-char shortcuts (`s`=status, `co`=checkout, `ci`=commit, `pr`=push -u origin HEAD, etc.) defined in `git/config`.
- **Homebrew**: Per-host `brewfile` is symlinked to `~/.Brewfile`. Supports x86 Rosetta packages via `brewfile-x86`.
- **Local overrides**: `~/.zshrc.local` is sourced at the end of zshrc for machine-specific config not in this repo.
- **SSH**: Uses Ed25519 keys. Base config disables strict host key checking and enables connection multiplexing.

## Common User Commands

- `reload-config` — Re-source `~/.zshrc`
- `refresh-dotfiles` — Pull latest changes, re-run setup, reload config
- `cdd` — `cd` to this dotfiles directory
- `gw` — Run `gradlew` from any subdirectory
- `reload-brewfile` — Install packages from `~/.Brewfile` (macOS)
- `reload-ssh-keys` — Add SSH keys to agent
- `ta wt list` — List worktrees with status, ahead/behind info
- `ta wt create <branch>` — Create a worktree tracking a remote branch
- `ta wt remove <branch>` — Remove a worktree by branch name
- `ta wt prune` — Remove worktrees merged into main (dry-run by default, `--apply` to execute)
- `ta wt status` — Quick one-line status of each worktree
- `ta workspace create <branch>` — Create a tmux session for a worktree
- `ta workspace list` — List `wt-*` tmux sessions
- `ta workspace attach <branch>` — Attach to (or create) a workspace session
- `ta workspace kill <branch>` — Kill a workspace session
- `ta ff <branch> [prompt]` — Fork a branch from main and focus in a tmux session
- `ta report` — Generate a markdown status report of worktrees and sessions
- `ta tmux sessions` — List all tmux sessions
- `ta tmux windows` — List tmux windows
- `ta tmux panes` — List tmux panes with command, PID, CWD
- `ta tmux capture <pane_id>` — Capture scrollback from a pane
- `/create-spec` — Interactive Ralph spec generator (Claude Code skill)

## Making Changes

- **Adding a new host**: Create `hosts/<hostname>/roles` listing desired roles. Optionally add `brewfile` and `ssh_host_config`.
- **Adding a new role**: Create `roles/<rolename>/` with an `install` script (one-time setup), `setup` script (runs every setup), and/or `zsh_plugin` file. Add a `requires` file listing dependency roles if needed. Add the role name to relevant host role files.
- **Adding a Homebrew package**: Edit the appropriate `hosts/<hostname>/brewfile`.
- **Adding a zsh plugin**: For role-specific plugins, add a `zsh_plugin` file to the role. For shared plugins, add to `zsh/plugins/`.
- **Modifying git config**: Edit `git/config`. User-specific settings (name, email, signing key) go in `~/.git/user.config` (generated by `roles/git/install`, not tracked).
