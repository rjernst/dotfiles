_git_branch_info() {
  #local branch=$(git branch 2> /dev/null | sed -e '/^[^*]/d' -e 's/* \(.*\)/\1/')
  local branch=$(git symbolic-ref --short -q HEAD 2>/dev/null)

  if [ ! -z "$branch" ]; then
    local branch_len="${#branch}"
    (( len = 2 + branch_len ))
    echo -n "$len: $branch"
  fi
}

right_info_functions+=(_git_branch_info)
