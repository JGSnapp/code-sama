#!/bin/sh
set -e
mkdir -p /workspace/apps /workspace/apps/generated
# Seed bundled manifests into the persistent volume (do not overwrite edits).
if [ -d /opt/code-sama/apps ]; then
  for f in /opt/code-sama/apps/*.yml /opt/code-sama/apps/*.yaml /opt/code-sama/apps/*.json; do
    [ -e "$f" ] || continue
    base=$(basename "$f")
    if [ ! -e "/workspace/apps/$base" ]; then
      cp "$f" "/workspace/apps/$base"
    fi
  done
fi
exec "$@"
