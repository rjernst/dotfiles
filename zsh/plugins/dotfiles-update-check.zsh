# Periodically check if dotfiles are behind upstream and notify the user.
# Runs git fetch in the background (at most once per 4 hours) and caches
# the result. The notification is displayed on the *next* shell startup
# so there is zero latency impact on the current session.

_dotfiles_check_updates() {
  local var_dir="$ZDOTDIR/var"
  local cache_file="$var_dir/dotfiles-behind-count"
  local fetch_stamp="$var_dir/dotfiles-fetch-stamp"

  # Display any pending notification from a previous background check
  if [[ -f "$cache_file" ]]; then
    local behind
    behind=$(<"$cache_file")
    if [[ "$behind" -gt 0 ]] 2>/dev/null; then
      print -P "%F{yellow}Dotfiles are $behind commit(s) behind upstream. Run %F{green}refresh-dotfiles%F{yellow} to update.%f"
    fi
    rm -f "$cache_file"
  fi

  # Only fetch at most once every 4 hours
  local now
  now=$(date +%s)
  local last_fetch=0
  [[ -f "$fetch_stamp" ]] && last_fetch=$(<"$fetch_stamp")
  local age=$(( now - last_fetch ))

  if (( age < 14400 )); then
    return
  fi

  # Background fetch + check (disowned so it doesn't block the shell)
  {
    echo "$now" > "$fetch_stamp"
    git -C "$DOTFILES" fetch --quiet 2>/dev/null || return
    local count
    count=$(git -C "$DOTFILES" rev-list --count HEAD..@{upstream} 2>/dev/null)
    if [[ -n "$count" && "$count" -gt 0 ]]; then
      echo "$count" > "$cache_file"
    fi
  } &!
}

_dotfiles_check_updates
unfunction _dotfiles_check_updates
