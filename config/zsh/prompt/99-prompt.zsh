#!/usr/bin/env zsh

setopt prompt_subst
bindkey '^a' beginning-of-line
bindkey '^e' end-of-line

#
#  COMPACT->
#  STATUS
#  |-LEFT_INFO----------------------------------RIGHT_INFO-|
#  |->                                                TIME-|
#

_set_status() {
  pr_last_status=${pr_last_status:-$?}
  pr_status=""

  if [ $pr_last_status -ne 0 ]; then
    local status_failed="[ failed: $pr_last_status]"
  fi
  local elapsed=$(( $SECONDS - ${timer:-$SECONDS} ))
  unset timer
  if [ $elapsed -gt 1 ]; then
    local status_timer="[ timer: "
    local hours=$(( $elapsed / 3600 ))
    [ $hours -gt 0 ] && status_timer+="$hours hours "
    elapsed=$(( $elapsed % 3600 ))
    local mins=$(( $elapsed / 60 ))
    [ $mins -gt 0 ] && status_timer+="$mins mins "
    local secs=$(( $elapsed % 60 ))
    [ $secs -gt 0 ] && status_timer+="$secs secs "
    status_timer+="]"
  fi
  if [[ ! -z "$status_timer" || ! -z "$status_failed" ]]; then
    pr_status="⇧ $(_lc red $status_failed) $(_lc yellow $status_timer)"$'\n'
  fi
}

# omit escapes, at least the color ones we may output for prompt
_visible_strlen() {
  local str=$1
  local zero='%([BSUbfksu]|([FK]|){*})'
  local strlen=${#${(S%%)str//$~zero/}}
  echo -n $strlen
}


_set_right_info() {
  right_info_values=()
  pr_right_info=
  for info_getter in $right_info_functions; do
    local info=$($info_getter)
    if [ ! -z "$info" ]; then
      right_info_values+=($info)
      pr_right_info+="[ $info ]"
    fi
  done
}

_set_left_info() {
  local chars_left=$1
  left_info_values=()
  pr_left_info=
  for info_getter in $left_info_functions; do
    local info=$($info_getter $chars_left)
    if [ ! -z "$info" ]; then
      local info_len=${#info}
      (( chars_left -= info_len ))
      if [ $chars_left -ge 0 ]; then
        left_info_values+=($info)
        pr_left_info+="[ $info ]"
      else
        break
      fi
    fi
  done
}

# setup env vars dynamically before PROMPT is resolved
precmd() {
  # this must be the first call, so it can capture the last exit status
  _set_status

  _set_right_info
  local right_info_len=$(_visible_strlen $pr_right_info)
  local termwidth=$(( $COLUMNS - 2 ))
  local max_left_info_len=$(( $termwidth - 5 - $right_info_len ))

  _set_left_info $max_left_info_len
  local left_info_len=$(_visible_strlen $pr_left_info)
  local fillbar_len=$(( $termwidth - 5 - $left_info_len - $right_info_len ))

  # NOTE: if fillbar size is negative, this blows up. thats a small screen, oh well
  if [ $fillbar_len -gt 0 ]; then
    fillbar="${sc[shift_in]}${sc[hbar]}"
    while [ $fillbar_len -gt 0 ]; do
      fillbar+="${sc[hbar]}"
      (( fillbar_len -= 1 ))
    done
    fillbar+="${sc[hbar]}${sc[shift_out]}"
  fi

  pr_prompt="➜  "
  pr_time=$(_lc 101 "[$(date +%T)]")
}

rewrite_prompt_on_enter() {
  # stash the previous prompts
  OLD_PROMPT="$PROMPT"
  OLD_RPROMPT="$RPROMPT"

  # write a new compact prompt, without all the extra space
  local compact_prompt=$pr_time
  for info in $left_info_values; do
    compact_prompt+="[$info]"
  done
  for info in $right_info_values; do
    compact_prompt+="[$info]"
  done
  compact_prompt+=$pr_prompt

  PROMPT='$compact_prompt'
  # add back the status info above the rewritten prompt if it is there
  if [ ! -z "$pr_status" ]; then
    PROMPT='$pr_status'$'\n'$PROMPT
  fi

  # reset everything back
  RPROMPT=""
  zle reset-prompt
  PROMPT="$OLD_PROMPT"
  RPROMPT="$OLD_RPROMPT"

  # start the timer for the next command
  timer=${timer:-$SECONDS}
  if [ -z $BUFFER ]; then
    pr_last_status=0
  else
    unset pr_last_status
  fi 
  zle accept-line
}
zle -N rewrite_prompt_on_enter
bindkey "^M" rewrite_prompt_on_enter

_info_line() {
  echo -n "$(_special ul hbar)${pr_left_info}${fillbar}${pr_right_info}$(_special hbar ur)"
}

_left_prompt() {
  echo -n "$(_special ll hbar)$pr_prompt"
}

_right_prompt() {
  echo -n "$(_lc yellow $pr_time)$(_special hbar lr)"
}

PROMPT='${sc[set_charset]}$pr_status
$(_info_line)
$(_left_prompt)'

RPROMPT='$(_right_prompt)'
