#!/usr/bin/env zsh
# Helper for role tests: runs link_role in a controlled zsh environment.
# Usage: zsh run_role.zsh <role_name>
# Expects HOME and DOTFILES to be set by the caller.

set -e

DRY_RUN=${DRY_RUN:-0}

setup_link() {
  src="$DOTFILES/$1"
  dst="$HOME/$2"
  if [ "$src" -ef "$dst" ]; then
    return
  fi
  if [[ -f "$dst" || -h "$dst" ]]; then
    >&2 echo "WARNING: $dst exists, overwriting"
    if (( DRY_RUN )); then
      echo "[dry-run] rm -f $dst"
    else
      rm -f "$dst"
    fi
  fi
  if (( DRY_RUN )); then
    echo "[dry-run] $src -> $dst"
  else
    echo "$src -> $dst"
    ln -s "$src" "$dst"
  fi
}

link_role() {
  if (( DRY_RUN )); then
    echo "[dry-run] Adding role $1"
  else
    echo "Adding role $1"
  fi
  role_dir=roles/$1
  if [ ! -d "$DOTFILES/$role_dir" ]; then
    echo "ERROR: No role dir at $role_dir"
    return 1
  fi
  install_file=$DOTFILES/$role_dir/install
  if [ -f "$install_file" ]; then
    if (( DRY_RUN )); then
      echo "[dry-run] would run install for $1"
    else
      (source "$install_file") || >&2 echo "WARNING: install for role '$1' failed (exit $?), continuing..."
    fi
  fi
  setup_file=$DOTFILES/$role_dir/setup
  if [ -f "$setup_file" ]; then
    if (( DRY_RUN )); then
      echo "[dry-run] would run setup for $1"
    else
      (source "$setup_file") || >&2 echo "WARNING: setup for role '$1' failed (exit $?), continuing..."
    fi
  fi
  zsh_plugin_file="$role_dir/zsh_plugin"
  if [ -f "$DOTFILES/$zsh_plugin_file" ]; then
    setup_link "$zsh_plugin_file" ".zsh/plugins/$1.zsh"
  fi
}

mkdir -p "$HOME/.zsh/plugins"
link_role "$1"
