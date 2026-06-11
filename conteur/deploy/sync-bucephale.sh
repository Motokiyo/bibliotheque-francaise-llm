#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="/root/vault/1 Projets/Chroniques-de-Bucéphale"
TARGET_DIR="/srv/conteur/live/chroniques-de-bucephale"

if [[ ! -d "$SOURCE_DIR" ]]; then
  exit 0
fi

install -d -o conteur -g conteur "$TARGET_DIR"
rsync -a --delete --include='*.md' --exclude='*' "$SOURCE_DIR"/ "$TARGET_DIR"/
chown -R conteur:conteur "$TARGET_DIR"
find "$TARGET_DIR" -type d -exec chmod 0750 {} +
find "$TARGET_DIR" -type f -exec chmod 0640 {} +
