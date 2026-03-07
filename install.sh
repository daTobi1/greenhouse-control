#!/usr/bin/env bash
# ============================================================
# Greenhouse Control – Installation Script
# Raspberry Pi OS (Bookworm / Bullseye)
# Run as: bash install.sh
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${USER:-pi}"

echo "==> Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y \
  python3-pip python3-venv \
  python3-opencv \
  ffmpeg \
  bluetooth bluez libbluetooth-dev \
  libglib2.0-dev

echo "==> Creating Python virtual environment..."
cd "$SCRIPT_DIR"
python3 -m venv venv
source venv/bin/activate

echo "==> Installing Python dependencies..."
pip install --upgrade pip
pip install fastapi "uvicorn[standard]" aiosqlite bleak

# RPi.GPIO is usually pre-installed; install if missing
python3 -c "import RPi.GPIO" 2>/dev/null || pip install RPi.GPIO

# Try system OpenCV first, fall back to pip
python3 -c "import cv2" 2>/dev/null || pip install opencv-python-headless

echo "==> Creating timelapse directories..."
mkdir -p timelapse/frames timelapse/output

echo "==> Setting up Bluetooth permissions..."
# Allow the user to use BLE without root
sudo setcap 'cap_net_raw,cap_net_admin+eip' "$(readlink -f venv/bin/python3)" 2>/dev/null || true

echo "==> Installing systemd service..."
SERVICE_FILE="/etc/systemd/system/greenhouse.service"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Greenhouse Control Dashboard
After=network.target bluetooth.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${SCRIPT_DIR}/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable greenhouse.service

echo ""
echo "============================================================"
echo " Installation complete!"
echo ""
echo " Start:    sudo systemctl start greenhouse"
echo " Status:   sudo systemctl status greenhouse"
echo " Logs:     sudo journalctl -u greenhouse -f"
echo ""
echo " Dashboard: http://$(hostname -I | awk '{print $1}'):8080"
echo "============================================================"
