#!/usr/bin/env zsh

setopt prompt_subst
bindkey '^a' beginning-of-line
bindkey '^e' end-of-line

#
#  |-(~/mypwd/colored)---------------------------(gitinfo)-|
#  |->                                                TIME-|
#
#  ULCORNER + HBAR + ( + PWD + ) HBAR(repeated) +
#
#

git_prompt_info() {
  #local branch=$(git branch 2> /dev/null | sed -e '/^[^*]/d' -e 's/* \(.*\)/\1/')
  local branch=$(git symbolic-ref --short -q HEAD 2>/dev/null)

  if [ ! -z "$branch" ]; then
    echo -n "[ $branch ]"
  fi
}

# setup env vars dynamically before PROMPT is resolved
precmd() {
  PR_LAST_STATUS=${PR_LAST_STATUS:-$?}
  PR_STATUS=""
  if [ $PR_LAST_STATUS -ne 0 ]; then
    local status_failed="[ failed: $PR_LAST_STATUS]"
  fi
  local elapsed=$(( $SECONDS - ${TIMER:-$SECONDS} ))
  unset TIMER
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
    PR_STATUS="⇧ $PR_LIGHT_RED$status_failed $PR_LIGHT_YELLOW$status_timer$PR_NO_COLOR"$'\n'
  fi

  local termwidth=$(( $COLUMNS - 2 ))
  local info_repo=$(git_prompt_info)
  local info_repo_len=${#info_repo}
  PR_INFO_PWD=${(%):-%~}
  local info_pwd_len=${#PR_INFO_PWD}
  local ul_corner="${PR_SHIFT_IN}${PR_ULCORNER}${PR_HBAR}${PR_SHIFT_OUT}"
  local fillbar_size=$(( $termwidth - 9 - $info_pwd_len - $info_repo_len ))
  local fillbar="${PR_SHIFT_IN}${PR_HBAR}"
  if [ $fillbar_size -gt 0 ]; then
    while [ $fillbar_size -gt 0 ]; do
      fillbar+="${PR_HBAR}"
      (( fillbar_size -= 1 ))
    done
  else
    PR_INFO_PWD="%$(( $info_pwd_len + $fillbar_size ))<...<%~%<<"
  fi
  fillbar+="${PR_HBAR}${PR_SHIFT_OUT}"
  local ur_corner="${PR_SHIFT_IN}${PR_HBAR}${PR_URCORNER}${PR_SHIFT_OUT}"
  PR_INFOBAR="${ul_corner}[ ${PR_GREEN}$PR_INFO_PWD${PR_NO_COLOR} ]${fillbar}${info_repo}${ur_corner}"

  PR_PROMPT="➜  "
  PR_TIME="[$(date +%T)]"
}

rewrite_prompt_on_enter() {
  OLD_PROMPT="$PROMPT"
  OLD_RPROMPT="$RPROMPT"
  PROMPT='$PR_LIGHT_YELLOW$PR_TIME${PR_LIGHT_GREEN}[$PR_INFO_PWD]$PR_NO_COLOR $PR_PROMPT'
  if [ ! -z "$PR_STATUS" ]; then
    PROMPT='$PR_STATUS'$'\n'$PROMPT
  fi
  RPROMPT=""
  zle reset-prompt
  PROMPT="$OLD_PROMPT"
  RPROMPT="$OLD_RPROMPT"
  TIMER=${TIMER:-$SECONDS}
  if [ -z $BUFFER ]; then
    PR_LAST_STATUS=0
  else
    unset PR_LAST_STATUS
  fi 
  zle accept-line
}

autoload colors zsh/terminfo
if [[ "$terminfo[colors]" -ge 8 ]]; then
  colors
fi
for color in RED GREEN YELLOW BLUE MAGENTA CYAN WHITE; do
  #eval PR_$color="%{$terminfo[bold]$fg[${(L)color}]%}"
  eval PR_$color="%{$terminfo[bold]$fg[${(L)color}]%}"
  eval PR_LIGHT_$color="%{$fg[${(L)color}]%}"
  (( count = $count + 1 ))
done
PR_NO_COLOR="%{$terminfo[sgr0]%}"

typeset -A altchar
set -A altchar ${(s..)terminfo[acsc]}
PR_SET_CHARSET="%{$terminfo[enacs]%}"
PR_SHIFT_IN="%{$terminfo[smacs]%}"
PR_SHIFT_OUT="%{$terminfo[rmacs]%}"
PR_HBAR=${altchar[q]:--}
PR_ULCORNER=${altchar[l]:--}
PR_LLCORNER=${altchar[m]:--}
PR_LRCORNER=${altchar[j]:--}
PR_URCORNER=${altchar[k]:--}

zle -N rewrite_prompt_on_enter
bindkey "^M" rewrite_prompt_on_enter

PROMPT='$PR_SET_CHARSET$PR_STATUS
$PR_INFOBAR
${PR_SHIFT_IN}${PR_LLCORNER}${PR_HBAR}${PR_SHIFT_OUT}$PR_PROMPT'

RPROMPT='$PR_YELLOW$PR_TIME$PR_NO_COLOR${PR_SHIFT_IN}${PR_HBAR}${PR_LRCORNER}${PR_SHIFT_OUT}'
