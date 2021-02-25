
_pipenv_activate() {
  echo -n "Activating pipenv..."
  export VIRTUAL_ENV=$(pipenv --venv)
  local envname=$(cat $VIRTUAL_ENV/pyvenv.cfg | grep prompt | awk '{ print $3 }' | sed 's/[\(\)]//g')
  export PIPENV_NAME=$envname
  export PIPENV_ACTIVE=1

  export PYTHONDONTWRITEBYTECODE=1
  export PIP_DISABLE_PIP_VERSION_CHECK=1
  local python_path=$(pipenv run env | grep PIP_PYTHON_PATH)
  export $python_path

  export OLD_PATH=$PATH
  PATH=$VIRTUAL_ENV/bin:$PATH
  echo "$PIPENV_NAME"
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

_find_pipenv() {
  local dir=$PWD
  while [ ! -f $dir/Pipfile ]; do
    if [ "$dir" = "/" ]; then
      return
    fi
    dir=$(cd $dir/..; pwd)
  done
  export PIPENV_ROOT=$dir
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
    _find_pipenv
    if [ -d "$PIPENV_ROOT" ]; then
      _pipenv_activate
    fi
  fi
}

autoload -U add-zsh-hook
add-zsh-hook chpwd _pipenv_auto
