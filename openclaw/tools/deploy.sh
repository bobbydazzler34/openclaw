#!/usr/bin/env bash

set -euo pipefail

SERVER="${SSH_USER}@${SSH_HOST}"
APP_DIR="/opt/openclaw"
SERVICE_NAME="openclaw"

ssh "${SERVER}" <<EOF
cd "${APP_DIR}"
git pull
sudo systemctl restart "${SERVICE_NAME}"
EOF
