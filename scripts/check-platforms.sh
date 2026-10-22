#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for architecture in arm64 amd64; do
  docker build --platform "linux/$architecture" --target test \
    -t "taskbot-check:$architecture" backend
  docker run --rm --platform "linux/$architecture" --entrypoint python \
    "taskbot-check:$architecture" -c \
    'import av, ctranslate2, faster_whisper; from app.api import app; print("CPU dependencies and API import successfully")'
done
