_pipenv_info() {
  if [ "$PIPENV_ACTIVE" ]; then
    local envname=$(cat $VIRTUAL_ENV/pyvenv.cfg | grep prompt | awk '{ print $3 }' | sed 's/[\(\)]//g')
    echo -n "🐍 $envname"
  fi
}

right_info_functions+=(_pipenv_info)
