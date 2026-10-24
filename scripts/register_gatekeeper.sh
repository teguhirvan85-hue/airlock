#!/usr/bin/env bash
# Registers the Airlock gatekeeper as a custom agent on the local Switch server
# that Switch Console runs, under your own admin account.
#
# It signs in with the server's generated admin login, mints a personal
# registration key, and registers the agent with it. Nothing secret is
# printed: the agent's API key is saved to ~/.airlock/agents/ (mode 600) and
# the registration key only ever lives in this script's memory.
#
# Run it yourself:  bash scripts/register_gatekeeper.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NATIVE="$HOME/Library/Application Support/switchdash/local-switch-server/native"
ENVF="$NATIVE/run/switch-core.env"
PORTS="$NATIVE/../ports.json"
NAME="airlock-gatekeeper"

[ -r "$ENVF" ] || { echo "Switch server config not found. Is the local server set up?"; exit 1; }
API_PORT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["api"])' "$PORTS")
BASE="http://127.0.0.1:$API_PORT"
curl -sf "$BASE/health" >/dev/null || { echo "Switch server is not running. Click Start in Switch Console."; exit 1; }

EMAIL=$(grep '^GATEWAY_ADMIN_EMAIL=' "$ENVF" | cut -d= -f2-)
JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT

python3 - "$ENVF" "$EMAIL" <<'PY' | curl -sf -c "$JAR" -H 'Content-Type: application/json' -d @- "$BASE/gateway/auth/login" >/dev/null \
  || { echo "Sign-in to the local server failed."; exit 1; }
import json, sys
env, email = sys.argv[1], sys.argv[2]
password = next(l.split("=", 1)[1].rstrip("\n") for l in open(env) if l.startswith("GATEWAY_ADMIN_PASSWORD="))
print(json.dumps({"email": email, "password": password}))
PY
echo "Signed in to the local Switch server."

SWITCH_REGISTRATION_KEY=$(curl -sf -b "$JAR" -H 'Content-Type: application/json' \
  -d '{"label":"airlock"}' "$BASE/gateway/api-keys" | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])') \
  || { echo "Could not create a registration key."; exit 1; }
export SWITCH_REGISTRATION_KEY
echo "Created a personal registration key labelled 'airlock'."

cd "$HERE/../agents/lib"
python3 switch_agent.py register "$NAME" \
  --display-name "Gatekeeper" \
  --description "Checks the Reader's triage summaries against a fixed schema. Runs no model." \
  ${1:+$1}
