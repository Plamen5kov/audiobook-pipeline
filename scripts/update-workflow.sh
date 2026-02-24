#!/usr/bin/env bash
# Push a local workflow JSON to the live n8n instance via the REST API.
# Usage: ./scripts/update-workflow.sh [workflow-json-file] [workflow-id]
# Defaults to the audiobook-synthesize workflow.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

JSON_FILE="${1:-$ROOT/n8n-workflows/audiobook-synthesize.json}"
WORKFLOW_ID="${2:-XP0SGdtIlq23CBwM}"
N8N_URL="${N8N_URL:-http://localhost:5678}"

# Load API key from .env if not already set in environment
if [[ -z "${N8N_API_KEY:-}" ]]; then
  ENV_FILE="$ROOT/.env"
  if [[ -f "$ENV_FILE" ]]; then
    N8N_API_KEY=$(grep '^N8N_API_KEY=' "$ENV_FILE" | cut -d= -f2-)
  fi
fi

if [[ -z "${N8N_API_KEY:-}" ]]; then
  echo "ERROR: N8N_API_KEY not set. Add it to .env or export it." >&2
  exit 1
fi

echo "Updating workflow $WORKFLOW_ID from $JSON_FILE ..."

# Build the PUT payload: only the fields the API accepts
PAYLOAD=$(python3 - "$JSON_FILE" <<'EOF'
import json, sys
with open(sys.argv[1]) as f:
    w = json.load(f)
settings = {k: v for k, v in w.get('settings', {}).items() if k == 'executionOrder'}
payload = {
    'name':        w['name'],
    'nodes':       w['nodes'],
    'connections': w['connections'],
    'settings':    settings,
}
print(json.dumps(payload))
EOF
)

RESPONSE=$(curl -s -X PUT \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "$N8N_URL/api/v1/workflows/$WORKFLOW_ID")

# Check for success (response should contain versionId)
if echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK — versionId:', d['versionId'])" 2>/dev/null; then
  echo "Workflow updated successfully."
else
  echo "ERROR: $RESPONSE" >&2
  exit 1
fi
