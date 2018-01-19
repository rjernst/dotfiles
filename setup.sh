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

mkdir -p ~/.bash
setup_link "bash/rc"                  ".bashrc"
setup_link "bash/profile"             ".bash_profile"
setup_link "bash/aliases"             ".bash/aliases"
setup_link "bash/env"                 ".bash/env"
setup_link "bash/iterm2_integration"  ".bash/iterm2_integration"
setup_link "bash/git_completion"      ".bash/git_completion"
mkdir -p ~/.git
setup_link "git/config"               ".gitconfig"
setup_link "git/ignore"               ".gitignore"
setup_link "git/prune-branches"       ".git/prune-branches"
setup_link "hg/rc"                    ".hgrc"
setup_link "vim/rc"                   ".vimrc"
setup_link "tmux/conf"                ".tmux.conf"

mkdir -p ~/.scripts
for script in scripts/*; do
  setup_link $script .scripts/$(basename $script)
done

echo "Sourcing bash files"
source ~/.bashrc # todo might need to change this for linux vs mac
