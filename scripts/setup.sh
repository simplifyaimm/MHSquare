#!/usr/bin/env sh
# First-time setup: copy example configs (never overwrites) and generate random secrets in .env.
set -e
cd "$(dirname "$0")/.."

rand() { head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "${1:-48}"; }

copy() { [ -f "$2" ] && echo "keep   $2" || { cp "$1" "$2"; echo "create $2"; }; }
copy litellm/config.example.yaml litellm/config.yaml
copy config/routing.example.yaml config/routing.yaml

if [ -f .env ]; then
  echo "keep   .env"
else
  sed \
    -e "s|^MHSQUARE_SECRET_KEY=.*|MHSQUARE_SECRET_KEY=$(rand 64)|" \
    -e "s|^MHSQUARE_ADMIN_PASSWORD=.*|MHSQUARE_ADMIN_PASSWORD=$(rand 20)|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rand 32)|" \
    -e "s|^LITELLM_MASTER_KEY=.*|LITELLM_MASTER_KEY=sk-$(rand 40)|" \
    .env.example > .env
  echo "create .env (random secrets generated)"
  echo
  echo "Your login:  $(grep ^MHSQUARE_ADMIN_EMAIL= .env | cut -d= -f2)"
  echo "Password:    $(grep ^MHSQUARE_ADMIN_PASSWORD= .env | cut -d= -f2)"
  echo "(edit MHSQUARE_ADMIN_EMAIL / PASSWORD in .env before the first 'docker compose up' to change)"
fi

[ -d .git ] && sh scripts/install-hooks.sh || true
