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
roles/<role>/setup        # optional: runs during setup (sourced)
roles/<role>/zsh_plugin   # optional: symlinked to ~/.zsh/plugins/<role>.zsh
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
| elasticsearch          | Gradle init script for ES builds, project directory aliases|
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
scripts/
  gradlew.sh           # Find and run gradlew from any subdirectory
  git-make-worktree    # Create git worktree for upstream branch
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

## Making Changes

- **Adding a new host**: Create `hosts/<hostname>/roles` listing desired roles. Optionally add `brewfile` and `ssh_host_config`.
- **Adding a new role**: Create `roles/<rolename>/` with a `setup` script and/or `zsh_plugin` file. Add the role name to relevant host role files.
- **Adding a Homebrew package**: Edit the appropriate `hosts/<hostname>/brewfile`.
- **Adding a zsh plugin**: For role-specific plugins, add a `zsh_plugin` file to the role. For shared plugins, add to `zsh/plugins/`.
- **Modifying git config**: Edit `git/config`. User-specific settings (name, email, signing key) go in `~/.git/user.config` (generated by `roles/git/install`, not tracked).
