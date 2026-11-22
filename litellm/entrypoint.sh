#!/usr/bin/env bash
# Wraps the LiteLLM proxy: loads model IDs + provider keys from env files and restarts the
# proxy whenever local/llmconfig.env changes (edited from the mhsquare UI). The API never
# needs the Docker socket to apply new keys.
set -u

DIR=/mh/litellm
CFG="$DIR/config.yaml"; [ -f "$CFG" ] || CFG="$DIR/config.example.yaml"
DEFAULTS="$DIR/llm-defaults.env"
ENVF=/mh/local/llmconfig.env

# Parse KEY=value lines WITHOUT shell evaluation (values are never executed).
load_env_file() {
  [ -f "$1" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]] || continue
    [ -n "${BASH_REMATCH[2]}" ] && export "${BASH_REMATCH[1]}=${BASH_REMATCH[2]}"
  done < "$1"
}

# MHSQUARE_* lines (the app's own virtual key) don't affect the proxy — don't restart for them.
checksum() { { cat "$CFG" "$DEFAULTS"; grep -v '^MHSQUARE_' "$ENVF"; } 2>/dev/null | md5sum; }

trap 'kill -TERM "${PID:-0}" 2>/dev/null; wait "${PID:-0}"; exit 0' TERM INT

while true; do
  (
    load_env_file "$DEFAULTS"
    load_env_file "$ENVF"
    exec litellm --config "$CFG" --port 4000
  ) &
  PID=$!
  SUM="$(checksum)"
  restarted=0
  while kill -0 "$PID" 2>/dev/null; do
    sleep 3
    if [ "$(checksum)" != "$SUM" ]; then
      echo "[mhsquare] llm config changed — restarting LiteLLM"
      kill -TERM "$PID"; wait "$PID"
      restarted=1
      break
    fi
  done
  if [ "$restarted" = 0 ]; then
    wait "$PID"; code=$?
    echo "[mhsquare] LiteLLM exited with code $code"
    exit "$code"
  fi
done
