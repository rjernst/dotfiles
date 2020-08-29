_git_branch_info() {
  #local branch=$(git branch 2> /dev/null | sed -e '/^[^*]/d' -e 's/* \(.*\)/\1/')
  local branch=$(git symbolic-ref --short -q HEAD 2>/dev/null)
  local detached=$?
  if [ $detached -ne 0 ]; then
    branch=$(git rev-parse --short HEAD)
  fi

  if [ ! -z "$branch" ]; then
    local branch_len="${#branch}"
    (( len = 2 + branch_len ))
    git rev-list -1 MERGE_HEAD &> /dev/null
    if git rev-list -1 MERGE_HEAD &> /dev/null; then
      branch="$branch $(_bc red ✘)"
      (( len += 2 ))
    fi
    if [ $detached -ne 0 ]; then
      branch="$(_lc 229 $branch)"
    fi
    echo -n "$len: $branch"
  fi
}

right_info_functions+=(_git_branch_info)
