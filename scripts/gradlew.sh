#!/bin/bash

DIR=$(pwd)

while [ ! -f $DIR/gradlew ]; do
  DIR=$(cd $DIR/..; pwd)
  if [ $DIR = "/" ]; then
    echo "Could not find gradle wrapper"
    exit 1;
  fi
done 

$DIR/gradlew "$@"
