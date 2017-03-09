#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
echo "Setting up config files"

setup_link() {
  SOURCE=$DIR/$1
  DEST=~/$2
  if [[ -e "$DEST" ]]; then
    echo "Replacing old file $DEST"
    rm $DEST
  fi 
  echo "Linking $SOURCE to $DEST"
  ln -s $SOURCE $DEST
}

setup_link "bash/rc"              ".bashrc"
setup_link "bash/profile"         ".bash_profile"
setup_link "bash/aliases"         ".bash_aliases"
setup_link "bash/env"             ".bash_env"
setup_link "git/bash_completion"  ".bash_gitcompletion"
setup_link "git/config"           ".gitconfig"
setup_link "git/ignore"           ".gitignore"
setup_link "hg/rc"                ".hgrc"
setup_link "vim/rc"               ".vimrc"
setup_link "tmux/conf"            ".tmux.conf"

echo "Sourcing bash files"
source ~/.bash_profile # todo might need to change this for linux vs mac
