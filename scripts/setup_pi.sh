#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/homerun-clone"
SERVICE_PATH="/etc/systemd/system/homerun-clone.service"
APP_PORT="5000"

if ! command -v sudo >/dev/null 2>&1; then
  echo "This script requires sudo privileges to install packages and register systemd services."
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(whoami)}"

echo "Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y \
  python3-venv \
  python3-pip \
  ffmpeg \
  v4l-utils \
  dvb-tools \
  dvb-apps \
  git

echo "Creating install directory at ${INSTALL_DIR}..."
sudo mkdir -p "${INSTALL_DIR}"
sudo rsync -a --delete \
  --exclude ".git" \
  --exclude "venv" \
  --exclude "__pycache__" \
  "${REPO_DIR}/" "${INSTALL_DIR}/"

echo "Setting ownership for ${RUN_USER}..."
sudo chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"

echo "Creating Python virtual environment..."
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install \
  flask \
  flask-socketio \
  eventlet \
  psutil

echo "Installing systemd service..."
sudo tee "${SERVICE_PATH}" >/dev/null <<EOF
[Unit]
Description=HomeRun Clone TV Tuner
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${INSTALL_DIR}/app
Environment=PATH=${INSTALL_DIR}/venv/bin
ExecStart=${INSTALL_DIR}/venv/bin/python app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable --now homerun-clone.service

echo "Setup complete. Open http://<pi-ip>:${APP_PORT} in your browser."
