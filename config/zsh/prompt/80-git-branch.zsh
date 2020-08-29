_git_branch_info() {
  #local branch=$(git branch 2> /dev/null | sed -e '/^[^*]/d' -e 's/* \(.*\)/\1/')
  local branch=$(git symbolic-ref --short -q HEAD 2>/dev/null)

  if [ ! -z "$branch" ]; then
    echo -n " $branch"
  fi
}

right_info_functions+=(_git_branch_info)
