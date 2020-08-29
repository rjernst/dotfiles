_pipenv_info() {
  if [ "$PIPENV_ACTIVE" ]; then
    # the snake emoji is 2 wide
    (( len = 3 + ${#PIPENV_NAME} ))
    echo -n "$len:🐍 $PIPENV_NAME"
  fi
}

_pipenv_activate() {
  echo -n "Activating pipenv..."
  export VIRTUAL_ENV=$(pipenv --venv)
  export PIPENV_ROOT=$1
  local envname=$(cat $VIRTUAL_ENV/pyvenv.cfg | grep prompt | awk '{ print $3 }' | sed 's/[\(\)]//g')
  echo "$envname"
  export PIPENV_NAME=$envname
  export PIPENV_ACTIVE=1

  export PYTHONDONTWRITEBYTECODE=1
  export PIP_DISABLE_PIP_VERSION_CHECK=1
  local python_path=$(pipenv run env | grep PIP_PYTHON_PATH)
  export $python_path

  export OLD_PATH=$PATH
  PATH=$VIRTUAL_ENV/bin:$PATH
}

_pipenv_deactivate() {
  echo "Deactivating pipenv: $PIPENV_NAME"
  unset VIRTUAL_ENV
  unset PIPENV_ROOT
  unset PIPENV_NAME
  unset PIPENV_ACTIVE

  unset PYTHONDONTWRITEBYTECODE
  unset PIP_DISABLE_PIP_VERSION_CHECK
  unset PIP_PYTHON_PATH

  PATH=$OLD_PATH
  unset OLD_PATH
}

_find_pipfile() {
  dir=$(pwd)
  while [ ! -f $dir/Pipfile ]; do
    dir=$(cd $dir/..; pwd)
    if [ $dir = "/" ]; then
      return
    fi
  done 
  echo -n "$dir/Pipfile"; return
}

_pipenv_auto() {
  if [ $PIPENV_ACTIVE ]; then
    # check if we exited the env
    if [[ ! "$PWD" = "$PIPENV_ROOT"/* ]]; then
      _pipenv_deactivate
    fi
  fi
  
  # do not chain the if/else so that a new env can be activated at the same time
  if [ ! $PIPENV_ACTIVE ]; then
    local pipfile=$(_find_pipfile)
    if [ "$pipfile" ]; then
      _pipenv_activate $(dirname $pipfile)
    fi
  fi
}

right_info_functions+=(_pipenv_info)
chdir_functions+=(_pipenv_auto)
