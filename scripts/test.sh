#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# This project uses separate Docker volumes from the running application.
compose=(docker compose -p taskbot-tests -f docker-compose.yml -f docker-compose.test.yml)
"${compose[@]}" up -d --wait postgres redis
"${compose[@]}" exec -T postgres sh -c '
  if ! psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT 1 FROM pg_database WHERE datname = '\''taskbot_test'\''" | grep -q 1; then
    createdb -U "$POSTGRES_USER" taskbot_test
  fi
'
"${compose[@]}" build test
"${compose[@]}" run --rm --no-deps test "$@"
# Deliberately retain test volumes/cache; `docker compose -p taskbot-tests ... down` stops them.
