_git_branch_info() {
  # TODO: consider async for future, as well as using vcs info native in git
  # https://vincent.bernat.ch/en/blog/2019-zsh-async-vcs-info
  
  local branch
  branch=$(git symbolic-ref --short -q HEAD 2>/dev/null)
  local detached=$?
  if [ $detached -ne 0 ]; then
    branch=$(git rev-parse --short HEAD 2>/dev/null)
  fi

  if [ ! -z "$branch" ]; then
    local branch_len="${#branch}"
    branch=" $branch"
    local color=
    (( len = 2 + branch_len ))
    git rev-list -1 MERGE_HEAD &> /dev/null
    if [ $? -eq 0 ]; then
      branch="$branch $(_bc red ✘)"
      (( len += 2 ))
    fi
    local stashes=$(git stash list)
    if [ ! -z "$stashes" ]; then
      branch="$branch $(_bc yellow ⚑)"
    fi
    if [ $detached -ne 0 ]; then
      branch="$(_lc 227 $branch)"
    elif ! git diff-files --quiet --ignore-submodules --; then
      # unstaged changes
      branch="$(_lc 215 $branch)"
    elif ! git diff-index --cached --quiet HEAD --ignore-submodules --; then
      branch="$(_lc 215 $branch)"
    elif [ ! -z "$(git log -1 @{push}..)" ]; then
      branch="$(_lc 031 $branch)"
    fi
    echo -n "$len:$branch"
  fi
}

right_info_functions+=(_git_branch_info)
