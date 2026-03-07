#!/usr/bin/env bash
# ============================================================
# Greenhouse Control – Installer
# Verwendung:
#   curl -fsSL https://raw.githubusercontent.com/daTobi1/greenhouse-control/master/install.sh | bash
# oder lokal:
#   bash install.sh
# ============================================================
set -euo pipefail

REPO_URL="https://github.com/daTobi1/greenhouse-control.git"
INSTALL_DIR="${GREENHOUSE_DIR:-$HOME/greenhouse-control}"
SERVICE_NAME="greenhouse"
PORT="${GREENHOUSE_PORT:-8080}"
SERVICE_USER="${USER:-pi}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

echo ""
echo "============================================================"
echo "  Greenhouse Control – Installation"
echo "  Zielverzeichnis: $INSTALL_DIR"
echo "  Port: $PORT"
echo "============================================================"
echo ""

# ── Voraussetzungen prüfen ──────────────────────────────────
command -v python3 >/dev/null 2>&1 || error "Python3 nicht gefunden"
command -v git     >/dev/null 2>&1 || error "git nicht gefunden"

# ── System-Pakete installieren ──────────────────────────────
info "Installiere System-Pakete..."
sudo apt-get update -qq
sudo apt-get install -y \
  python3-pip python3-venv \
  ffmpeg \
  bluetooth bluez libbluetooth-dev libglib2.0-dev \
  libcap2-bin \
  2>/dev/null || warn "Einige Pakete konnten nicht installiert werden (nicht Raspberry Pi OS?)"

# OpenCV: System-Paket bevorzugen
sudo apt-get install -y python3-opencv 2>/dev/null || true

# ── Repo klonen oder aktualisieren ─────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
  info "Vorhandene Installation gefunden – aktualisiere..."
  git -C "$INSTALL_DIR" fetch --quiet origin
  git -C "$INSTALL_DIR" reset --hard origin/master --quiet
  ok "Repo aktualisiert"
else
  info "Klone Repository nach $INSTALL_DIR..."
  git clone --quiet "$REPO_URL" "$INSTALL_DIR"
  ok "Repo geklont"
fi

cd "$INSTALL_DIR"

# ── Python Virtual Environment ──────────────────────────────
info "Richte Python Virtual Environment ein..."
python3 -m venv venv
source venv/bin/activate

pip install --quiet --upgrade pip
pip install --quiet fastapi "uvicorn[standard]" aiosqlite bleak

# RPi.GPIO (nur auf Raspberry Pi)
python3 -c "import RPi.GPIO" 2>/dev/null || \
  pip install --quiet RPi.GPIO 2>/dev/null || \
  warn "RPi.GPIO nicht verfügbar (kein Raspberry Pi?)"

# OpenCV: pip-Fallback wenn System-Paket fehlt
python3 -c "import cv2" 2>/dev/null || \
  pip install --quiet opencv-python-headless || \
  warn "OpenCV nicht installiert – Kamera-Funktionen deaktiviert"

deactivate
ok "Python-Umgebung bereit"

# ── Verzeichnisse anlegen ───────────────────────────────────
mkdir -p "$INSTALL_DIR/timelapse/frames" "$INSTALL_DIR/timelapse/output"

# ── Bluetooth-Berechtigung für Python ──────────────────────
PYTHON_BIN="$(readlink -f "$INSTALL_DIR/venv/bin/python3")"
sudo setcap 'cap_net_raw,cap_net_admin+eip' "$PYTHON_BIN" 2>/dev/null || \
  warn "setcap fehlgeschlagen – BLE benötigt evtl. sudo"

# ── systemd Service ─────────────────────────────────────────
info "Installiere systemd Service..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Greenhouse Control Dashboard
After=network.target bluetooth.target
Wants=bluetooth.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/uvicorn main:app --host 0.0.0.0 --port ${PORT}
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.service" --quiet
sudo systemctl restart "${SERVICE_NAME}.service"

sleep 2
if sudo systemctl is-active --quiet "${SERVICE_NAME}"; then
  ok "Service läuft"
else
  warn "Service nicht gestartet – prüfe: sudo journalctl -u ${SERVICE_NAME} -n 30"
fi

# ── Fertig ──────────────────────────────────────────────────
IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
echo ""
echo "============================================================"
echo -e "  ${GREEN}Installation abgeschlossen!${NC}"
echo ""
echo "  Dashboard:  http://${IP}:${PORT}"
echo ""
echo "  Befehle:"
echo "    Status:  sudo systemctl status ${SERVICE_NAME}"
echo "    Logs:    sudo journalctl -u ${SERVICE_NAME} -f"
echo "    Stopp:   sudo systemctl stop ${SERVICE_NAME}"
echo "============================================================"
echo ""
