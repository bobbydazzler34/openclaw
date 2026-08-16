#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

export PYTHONPATH="$REPO_ROOT"

if [[ -f /etc/openclaw/secrets.env ]]; then
  set -a
  # shellcheck source=/dev/null
  source /etc/openclaw/secrets.env
  set +a
fi

mkdir -p /var/lib/openclaw/bitcoin_balance_monitor

exec "$REPO_ROOT/.venv/bin/python" -m openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor "$@"
