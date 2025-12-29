#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/homerun-clone"
SERVICE_NAME="homerun-clone.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

if ! command -v sudo >/dev/null 2>&1; then
  echo "This script requires sudo privileges to remove the systemd service."
  exit 1
fi

RUN_USER="${SUDO_USER:-$(whoami)}"

echo "Stopping ${SERVICE_NAME} if running..."
sudo systemctl stop "${SERVICE_NAME}" || true

echo "Disabling ${SERVICE_NAME}..."
sudo systemctl disable "${SERVICE_NAME}" || true

if [ -f "${SERVICE_PATH}" ]; then
  echo "Removing ${SERVICE_NAME} service file..."
  sudo rm -f "${SERVICE_PATH}"
fi

echo "Reloading systemd..."
sudo systemctl daemon-reload

echo "Removing install directory ${INSTALL_DIR}..."
sudo rm -rf "${INSTALL_DIR}"

echo "Uninstall complete. Service removed and files deleted."
