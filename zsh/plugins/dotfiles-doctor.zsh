# Validate dotfiles installation health.
# Checks symlinks, roles, SSH keys, git signing, plugins, startup time,
# and upstream freshness. Run via: dotfiles-doctor

_doctor_pass() { print -P "  %F{green}OK%f    $1" }
_doctor_warn() { print -P "  %F{yellow}WARN%f  $1"; (( _doctor_warnings++ )) }
_doctor_fail() { print -P "  %F{red}FAIL%f  $1"; (( _doctor_errors++ )) }
_doctor_section() { print -P "\n%F{cyan}[$1]%f" }

_doctor_check_symlinks() {
  _doctor_section "Symlinks"

  # Expected symlinks derived from setup script: src (relative to DOTFILES) -> dst (relative to HOME)
  typeset -A expected_links=(
    [zsh/zshrc]=".zshrc"
    [gradle/properties]=".gradle/gradle.properties"
    [ssh/config]=".ssh/config"
    [git/config]=".gitconfig"
    [git/ignore]=".git/ignore"
    [starship/starship.toml]=".config/starship.toml"
    [gh/config.yml]=".config/gh/config.yml"
    [claude/CLAUDE.md]=".claude/CLAUDE.md"
    [codex/config.toml]=".codex/config.toml"
    [codex/rules/default.rules]=".codex/rules/default.rules"
  )

  local src dst
  for src in ${(k)expected_links}; do
    dst="$HOME/${expected_links[$src]}"
    if [[ -L "$dst" ]]; then
      local target
      target=$(readlink "$dst")
      if [[ "$target" == "$DOTFILES/$src" ]]; then
        _doctor_pass "$dst"
      else
        _doctor_fail "$dst -> $target (expected $DOTFILES/$src)"
      fi
    elif [[ -e "$dst" ]]; then
      _doctor_warn "$dst exists but is not a symlink"
    else
      _doctor_fail "$dst missing"
    fi
  done

  # Dynamically check vim/* files
  for vimfile in "$DOTFILES"/vim/*(N); do
    local name=${vimfile:t}
    dst="$HOME/.vim/$name"
    if [[ -L "$dst" ]]; then
      local target
      target=$(readlink "$dst")
      if [[ "$target" == "$DOTFILES/vim/$name" ]]; then
        _doctor_pass "$dst"
      else
        _doctor_fail "$dst -> $target (expected $DOTFILES/vim/$name)"
      fi
    elif [[ -e "$dst" ]]; then
      _doctor_warn "$dst exists but is not a symlink"
    else
      _doctor_fail "$dst missing"
    fi
  done
}

_doctor_check_stale_plugins() {
  _doctor_section "Plugins"
  local found_stale=0
  for plugin in "$ZDOTDIR"/plugins/*.zsh(N); do
    if [[ -L "$plugin" && ! -e "$plugin" ]]; then
      _doctor_fail "stale symlink: $plugin"
      found_stale=1
    fi
  done
  if (( ! found_stale )); then
    _doctor_pass "no stale plugin symlinks"
  fi
}

_doctor_check_roles() {
  _doctor_section "Roles"
  local roles_file="$DOTFILES/hosts/$(hostname)/roles"
  if [[ ! -f "$roles_file" ]]; then
    _doctor_warn "no roles file at $roles_file"
    return
  fi
  local role
  while IFS= read -r role; do
    [[ -z "$role" || "$role" = \#* ]] && continue
    if [[ -d "$DOTFILES/roles/$role" ]]; then
      _doctor_pass "role $role"
    else
      _doctor_fail "role $role has no directory at roles/$role"
    fi
  done < "$roles_file"
}

_doctor_check_ssh_keys() {
  _doctor_section "SSH Keys"
  local keys=(id_ed25519 github_ed25519)
  for key in $keys; do
    if [[ -f "$HOME/.ssh/$key" ]]; then
      _doctor_pass "$key private key"
    else
      _doctor_fail "$key private key missing"
    fi
    if [[ -f "$HOME/.ssh/$key.pub" ]]; then
      _doctor_pass "$key public key"
    else
      _doctor_fail "$key public key missing"
    fi
  done

  # Check agent has keys loaded
  local agent_keys
  agent_keys=$(ssh-add -l 2>/dev/null)
  if [[ $? -eq 0 && -n "$agent_keys" ]]; then
    _doctor_pass "ssh-agent has keys loaded"
  else
    _doctor_warn "ssh-agent has no keys loaded (run reload-ssh-keys)"
  fi
}

_doctor_check_git_signing() {
  _doctor_section "Git Signing"

  # Check user.config exists
  if [[ -f "$HOME/.git/user.config" ]]; then
    _doctor_pass "~/.git/user.config exists"
  else
    _doctor_fail "~/.git/user.config missing (run roles/git/install)"
  fi

  # Check signing key
  local signing_key
  signing_key=$(git config --global user.signingKey 2>/dev/null)
  if [[ -n "$signing_key" ]]; then
    _doctor_pass "user.signingKey is set"
    if [[ -f "$signing_key" ]]; then
      _doctor_pass "signing key file exists"
    elif [[ -f "$HOME/.ssh/$signing_key" || -f "${signing_key/#\~/$HOME}" ]]; then
      _doctor_pass "signing key file exists"
    else
      _doctor_warn "signing key file not found: $signing_key"
    fi
  else
    _doctor_fail "user.signingKey not configured"
  fi

  # Check allowed signers
  local signers_file
  signers_file=$(git config --global gpg.ssh.allowedSignersFile 2>/dev/null)
  if [[ -n "$signers_file" ]]; then
    _doctor_pass "gpg.ssh.allowedSignersFile is set"
    signers_file="${signers_file/#\~/$HOME}"
    if [[ -f "$signers_file" ]]; then
      _doctor_pass "allowed signers file exists"
    else
      _doctor_fail "allowed signers file missing: $signers_file"
    fi
  else
    _doctor_fail "gpg.ssh.allowedSignersFile not configured"
  fi
}

_doctor_check_startup_time() {
  _doctor_section "Startup Time"
  zmodload zsh/datetime
  local start=$EPOCHREALTIME
  zsh -i -c exit 2>/dev/null
  local end=$EPOCHREALTIME
  local ms=$(( (end - start) * 1000 ))
  local rounded=$(printf '%.0f' $ms)
  if (( ms < 500 )); then
    _doctor_pass "shell startup: ${rounded}ms"
  else
    _doctor_warn "shell startup: ${rounded}ms (>500ms)"
  fi
}

_doctor_check_freshness() {
  _doctor_section "Freshness"
  local behind
  local cache_file="$ZDOTDIR/var/dotfiles-behind-count"

  if [[ -f "$cache_file" ]]; then
    behind=$(<"$cache_file")
  else
    behind=$(git -C "$DOTFILES" rev-list --count HEAD..@{upstream} 2>/dev/null)
  fi

  if [[ -z "$behind" || "$behind" -eq 0 ]] 2>/dev/null; then
    _doctor_pass "up to date with upstream"
  else
    _doctor_warn "$behind commit(s) behind upstream (run refresh-dotfiles)"
  fi
}

_doctor_check_brew_drift() {
  [[ $OSTYPE != darwin* ]] && return
  _doctor_section "Homebrew"

  # Count untracked formulae (on-request, not a dep, not in Brewfile)
  local -A brewfile_formulae as_dep
  local f
  for f in $(brew bundle list --global --formula 2>/dev/null); do
    brewfile_formulae[$f]=1
  done
  for f in $(brew list --installed-as-dependency --formula 2>/dev/null); do
    as_dep[$f]=1
  done
  local untracked_count=0
  for f in $(brew list --installed-on-request --formula 2>/dev/null); do
    (( ${+brewfile_formulae[$f]} || ${+as_dep[$f]} )) || (( untracked_count++ ))
  done

  # Count untracked casks
  local -A brewfile_casks
  for f in $(brew bundle list --global --cask 2>/dev/null); do
    brewfile_casks[$f]=1
  done
  for f in $(brew list --cask 2>/dev/null); do
    (( ${+brewfile_casks[$f]} )) || (( untracked_count++ ))
  done

  # Count missing packages
  local missing_count=0
  local missing
  missing=$(brew bundle check --global --verbose 2>/dev/null | grep -c '^→')
  (( missing )) && missing_count=$missing

  if (( untracked_count == 0 && missing_count == 0 )); then
    _doctor_pass "Brewfile is in sync"
  else
    local detail=""
    (( untracked_count > 0 )) && detail="$untracked_count untracked"
    (( missing_count > 0 )) && {
      [[ -n "$detail" ]] && detail+=", "
      detail+="$missing_count missing"
    }
    _doctor_warn "Brewfile drift: $detail (run brew-drift for details)"
  fi
}

_dotfiles_doctor() {
  local _doctor_errors=0
  local _doctor_warnings=0

  print -P "%F{bold}Dotfiles Doctor%f"

  _doctor_check_symlinks
  _doctor_check_stale_plugins
  _doctor_check_roles
  _doctor_check_ssh_keys
  _doctor_check_git_signing
  _doctor_check_startup_time
  _doctor_check_freshness
  _doctor_check_brew_drift

  echo ""
  if (( _doctor_errors == 0 && _doctor_warnings == 0 )); then
    print -P "%F{green}All checks passed.%f"
  else
    local summary=""
    (( _doctor_warnings > 0 )) && summary="$_doctor_warnings warning(s)"
    (( _doctor_errors > 0 )) && {
      [[ -n "$summary" ]] && summary+=", "
      summary+="$_doctor_errors error(s)"
    }
    print -P "%F{yellow}$summary%f"
  fi
}

# @help dotfiles-doctor -- Validate dotfiles installation health
alias dotfiles-doctor='_dotfiles_doctor'
