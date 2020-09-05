# alerts for dotfiles being dirty
# only want to check every hour at most

zmodload -i zsh/datetime
zmodload -F zsh/stat b:zstat

dirty_state_file=$DOTFILES/.git/dirty

_update_dotfiles_dirty_state() {
  dirty_state=$(git -C $DOTFILES status --porcelain)
  if [ ! -z "$dirty_state" ]; then
    _debug "writing dirty state: \n$dirty_state"
    echo "$dirty_state" > $dirty_state_file
  elif [ -f $dirty_state_file ]; then
    rm $dirty_state_file
  fi
}

_check_dotfiles_dirty() {
  local age=0
  if [ -f $dirty_state_file ]; then
    local last_modified=$(zstat +mtime $dirty_state_file)
    local age=$(( $EPOCHSECONDS - $last_modified ))
    _debug "dotfiles dirty state exists: modified=$last_modified age=$age"
    if [[ $age -gt 60 || $age -eq 0 ]]; then
      _debug "updating dirty state"
      _update_dotfiles_dirty_state
    fi
  fi
  if [[ $age -gt 60 || $age -eq 0 ]]; then
    _debug "updating dirty state"
    _update_dotfiles_dirty_state
  fi
  if [ -f $dirty_state_file ]; then
    _debug "notifying of dirty state"
    echo -n "$(_lc red 'dotfiles are dirty! Commit your changes')"
  fi
}

notify_functions+=(_check_dotfiles_dirty)
