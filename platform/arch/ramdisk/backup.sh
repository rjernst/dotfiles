#!/bin/bash

BACKUP_DIR=$HOME/.local/ramdisk-backup
// initial restore on startup
rsync -avz $BACKUP_DIR/ /mnt/ramdisk

while true; do
    inotifywait -r -e modify,attrib,close_write,move,create,delete /mnt/ramdisk
    rsync -avz --delete --exclude .gradle --exclude build --exclude build-bootstrap --exclude build-idea --exclude .vagrant /mnt/ramdisk/ $BACKUP_DIR
done
