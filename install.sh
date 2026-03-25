#!/usr/bin/env bash
# ============================================================
# Greenhouse Control – Installer
# Verwendung:
#   curl -fsSL https://raw.githubusercontent.com/daTobi1/greenhouse-control/master/install.sh | bash
# oder lokal:
#   bash install.sh
#
# Voraussetzung: Debian 12 Bookworm / Raspberry Pi OS Bookworm (minimal)
# ============================================================
set -euo pipefail

REPO_URL="https://github.com/daTobi1/greenhouse-control.git"
SERVICE_NAME="greenhouse"
PORT="${GREENHOUSE_PORT:-80}"
DEFAULT_USER="pi"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
step()    { echo -e "\n${BOLD}$*${NC}"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

echo ""
echo "============================================================"
echo -e "  ${BOLD}Greenhouse Control – Installation${NC}"
echo "============================================================"
echo ""

# ── Terminal-Eingabe vorbereiten (funktioniert auch bei curl | bash) ──
exec 3</dev/tty 2>/dev/null || exec 3</dev/null

# ── Benutzer abfragen ────────────────────────────────────────
if [ -n "${GREENHOUSE_USER:-}" ]; then
  SERVICE_USER="$GREENHOUSE_USER"
else
  echo -e "  Unter welchem Benutzer soll der Service laufen?"
  echo -e "  Standard: ${GREEN}${DEFAULT_USER}${NC}"
  echo ""
  # Countdown – bei beliebigem Tastendruck abbrechen
  KEYPRESS=false
  for i in $(seq 300 -1 1); do
    printf "\r  Drücke eine Taste um den Benutzer einzugeben (automatisch ${GREEN}${DEFAULT_USER}${NC} in %3ds) " "$i"
    if read -rn1 -t1 _ <&3 2>/dev/null; then
      KEYPRESS=true
      break
    fi
  done
  echo ""
  INPUT_USER=""
  if [ "$KEYPRESS" = true ]; then
    printf "  Benutzer: "
    read -r INPUT_USER <&3 2>/dev/null || true
  fi
  SERVICE_USER="${INPUT_USER:-$DEFAULT_USER}"
fi

# Prüfe ob Benutzer existiert, ggf. anlegen
if ! id "$SERVICE_USER" &>/dev/null; then
  info "Benutzer '$SERVICE_USER' existiert noch nicht – wird angelegt..."
  sudo useradd -m -s /bin/bash "$SERVICE_USER"
  ok "Benutzer '$SERVICE_USER' angelegt (Home: /home/${SERVICE_USER})"
fi

INSTALL_DIR="${GREENHOUSE_DIR:-/home/${SERVICE_USER}/greenhouse-control}"

echo ""
echo "  Zielverzeichnis : $INSTALL_DIR"
echo "  Port            : $PORT"
echo "  Benutzer        : $SERVICE_USER"
echo "============================================================"
echo ""

# ── Root-Rechte prüfen ──────────────────────────────────────
if ! sudo -n true 2>/dev/null; then
  info "sudo-Passwort wird für Systeminstallationen benötigt."
fi

# ── Basiswerkzeuge sicherstellen ────────────────────────────
step "1/7  Basiswerkzeuge prüfen und installieren"

if ! command -v apt-get >/dev/null 2>&1; then
  error "apt-get nicht gefunden – dieses Script benötigt Debian/Raspberry Pi OS."
fi

sudo apt-get update -qq

for pkg in curl git ca-certificates; do
  if ! dpkg -s "$pkg" &>/dev/null; then
    info "Installiere $pkg..."
    sudo apt-get install -y "$pkg" -qq
  fi
done
ok "Basiswerkzeuge vorhanden"

# ── Python-Version prüfen ───────────────────────────────────
step "2/7  Python prüfen"

# Python 3 installieren falls nicht vorhanden
if ! command -v python3 >/dev/null 2>&1; then
  info "Python3 nicht gefunden – installiere..."
  sudo apt-get install -y python3 python3-pip python3-venv -qq
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

info "Python $PY_VERSION gefunden"

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
  error "Python 3.10 oder neuer wird benötigt (gefunden: $PY_VERSION).

  Raspberry Pi OS / Debian Bullseye liefert nur Python 3.9.
  Bitte auf Bookworm upgraden:
    https://www.raspberrypi.com/software/

  Alternativ (nur für erfahrene Nutzer):
    sudo apt-get install -y python3.11 python3.11-venv python3.11-pip"
fi

ok "Python $PY_VERSION – OK"

# ── System-Pakete installieren ──────────────────────────────
step "3/7  System-Pakete installieren"

SYSTEM_PKGS=(
  # Python Build-Tools
  python3-pip python3-venv python3-dev build-essential pkg-config

  # Bluetooth / BLE
  bluetooth bluez bluez-tools libbluetooth-dev libglib2.0-dev
  dbus libdbus-1-dev rfkill

  # Kamera / Video
  ffmpeg v4l-utils

  # System-Dienste & Werkzeuge
  libcap2-bin libssl-dev
)

info "Installiere System-Pakete (kann etwas dauern)..."
sudo apt-get install -y "${SYSTEM_PKGS[@]}" -qq || \
  warn "Einige Pakete konnten nicht installiert werden."

# OpenCV als System-Paket (schneller als pip auf ARM)
if ! python3 -c "import cv2" 2>/dev/null; then
  info "Versuche System-OpenCV zu installieren..."
  sudo apt-get install -y python3-opencv -qq 2>/dev/null || true
fi

ok "System-Pakete installiert"

# ── Bluetooth einrichten ────────────────────────────────────
step "4/7  Bluetooth einrichten"

sudo systemctl enable bluetooth.service --quiet 2>/dev/null || true
sudo systemctl start  bluetooth.service         2>/dev/null || true

# Bluetooth-Blockierung aufheben (falls soft-blocked)
if command -v rfkill >/dev/null 2>&1; then
  sudo rfkill unblock bluetooth 2>/dev/null || true
  ok "Bluetooth entsperrt (rfkill)"
fi

# Benutzer zur bluetooth-Gruppe hinzufügen
if getent group bluetooth >/dev/null 2>&1; then
  sudo usermod -a -G bluetooth "$SERVICE_USER" 2>/dev/null || true
  ok "Benutzer '$SERVICE_USER' zur Gruppe 'bluetooth' hinzugefügt"
fi

# Benutzer zur video-Gruppe hinzufügen (Kamera-Zugriff)
if getent group video >/dev/null 2>&1; then
  sudo usermod -a -G video "$SERVICE_USER" 2>/dev/null || true
  ok "Benutzer '$SERVICE_USER' zur Gruppe 'video' hinzugefügt"
fi

# Benutzer zur netdev-Gruppe hinzufügen (WLAN-Steuerung)
if getent group netdev >/dev/null 2>&1; then
  sudo usermod -a -G netdev "$SERVICE_USER" 2>/dev/null || true
  ok "Benutzer '$SERVICE_USER' zur Gruppe 'netdev' hinzugefügt"
fi

# Benutzer zur gpio-Gruppe hinzufügen (GPIO-Zugriff)
if getent group gpio >/dev/null 2>&1; then
  sudo usermod -a -G gpio "$SERVICE_USER" 2>/dev/null || true
  ok "Benutzer '$SERVICE_USER' zur Gruppe 'gpio' hinzugefügt"
fi

# sudoers-Regel: Reboot, Shutdown und Service-Neustart ohne Passwort
SUDOERS_FILE="/etc/sudoers.d/greenhouse"
sudo tee "$SUDOERS_FILE" > /dev/null <<EOF
# Greenhouse Control – erlaubt dem Service-User System-Operationen
${SERVICE_USER} ALL=(ALL) NOPASSWD: /sbin/reboot
${SERVICE_USER} ALL=(ALL) NOPASSWD: /sbin/shutdown
${SERVICE_USER} ALL=(ALL) NOPASSWD: /bin/systemctl restart greenhouse
${SERVICE_USER} ALL=(ALL) NOPASSWD: /bin/systemctl restart greenhouse.service
${SERVICE_USER} ALL=(ALL) NOPASSWD: /usr/bin/tailscale up *, /usr/bin/tailscale up, /usr/bin/tailscale down
EOF
sudo chmod 440 "$SUDOERS_FILE"
ok "sudo-Rechte für Reboot/Shutdown/Restart/Tailscale eingerichtet"

# Port 80 ohne Root erlauben
if ! grep -q 'ip_unprivileged_port_start=80' /etc/sysctl.d/80-unprivileged-port.conf 2>/dev/null; then
  echo 'net.ipv4.ip_unprivileged_port_start=80' | sudo tee /etc/sysctl.d/80-unprivileged-port.conf > /dev/null
  sudo sysctl --system > /dev/null 2>&1
  ok "Port 80 für unprivilegierte Prozesse freigegeben"
fi

# Polkit-Regel für WLAN-Steuerung via NetworkManager
POLKIT_RULES_DIR="/etc/polkit-1/rules.d"
POLKIT_LEGACY_DIR="/etc/polkit-1/localauthority/50-local.d"
if [ -d "$POLKIT_RULES_DIR" ]; then
  sudo tee "${POLKIT_RULES_DIR}/10-greenhouse-network.rules" > /dev/null <<'RULES'
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 &&
        subject.isInGroup("netdev")) {
        return polkit.Result.YES;
    }
});
RULES
  ok "Polkit-Regel für WLAN-Steuerung eingerichtet (rules.d)"
elif [ -d "$(dirname "$POLKIT_LEGACY_DIR")" ]; then
  sudo mkdir -p "$POLKIT_LEGACY_DIR"
  sudo tee "${POLKIT_LEGACY_DIR}/10-greenhouse-network.pkla" > /dev/null <<'PKLA'
[Greenhouse WiFi Management]
Identity=unix-group:netdev
Action=org.freedesktop.NetworkManager.*
ResultAny=yes
ResultInactive=yes
ResultActive=yes
PKLA
  ok "Polkit-Regel für WLAN-Steuerung eingerichtet (pkla)"
fi

# ── Repo klonen oder aktualisieren ──────────────────────────
step "5/7  Repository"

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
step "6/7  Python-Umgebung"

info "Erstelle Virtual Environment..."
python3 -m venv venv
source venv/bin/activate

pip install --quiet --upgrade pip setuptools wheel

# requirements.txt installieren
info "Installiere Python-Abhängigkeiten..."
pip install --quiet -r requirements.txt

# OpenCV: pip-Fallback wenn System-Paket nicht im venv sichtbar
if ! python3 -c "import cv2" 2>/dev/null; then
  info "OpenCV nicht im venv – installiere opencv-python-headless..."
  pip install --quiet opencv-python-headless || \
    warn "OpenCV konnte nicht installiert werden – Kamera-Funktionen deaktiviert."
fi

# RPi.GPIO (Pi 1-4); Pi 5 nutzt gpiod
if python3 -c "import RPi.GPIO" 2>/dev/null; then
  ok "RPi.GPIO bereits vorhanden"
elif pip install --quiet RPi.GPIO 2>/dev/null; then
  ok "RPi.GPIO installiert"
else
  warn "RPi.GPIO nicht verfügbar (Pi 5 oder kein Raspberry Pi)."
  warn "Lüftersteuerung läuft im Mock-Modus."
fi

# Importe verifizieren
info "Überprüfe Python-Importe..."
IMPORT_ERRORS=0
for mod in fastapi uvicorn aiosqlite bleak; do
  if python3 -c "import $mod" 2>/dev/null; then
    ok "  $mod"
  else
    warn "  $mod FEHLT"
    IMPORT_ERRORS=$((IMPORT_ERRORS + 1))
  fi
done
if [ $IMPORT_ERRORS -gt 0 ]; then
  warn "$IMPORT_ERRORS Python-Modul(e) fehlen – pip install -r requirements.txt manuell ausführen."
fi

deactivate

# ── BLE-Berechtigung setzen ─────────────────────────────────
PYTHON_BIN="$(readlink -f "$INSTALL_DIR/venv/bin/python3")"
if sudo setcap 'cap_net_raw,cap_net_admin+eip' "$PYTHON_BIN" 2>/dev/null; then
  ok "BLE-Rechte (setcap) gesetzt"
else
  warn "setcap fehlgeschlagen – BLE-Scan benötigt evtl. sudo."
fi

# ── Verzeichnisse anlegen ────────────────────────────────────
mkdir -p "$INSTALL_DIR/timelapse/frames" "$INSTALL_DIR/timelapse/output"
ok "Verzeichnisse angelegt"

# ── systemd Service ──────────────────────────────────────────
step "7/7  systemd Service"

if ! command -v systemctl >/dev/null 2>&1; then
  warn "systemd nicht gefunden – Service wird nicht eingerichtet."
  warn "Manuell starten: cd $INSTALL_DIR && source venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port $PORT"
else
  # Autostart-Abfrage
  AUTOSTART=true
  echo ""
  echo -e "  Soll die Gewächshaus-Steuerung bei jedem Systemstart"
  echo -e "  automatisch gestartet werden?"
  echo ""
  echo -e "  ${GREEN}[j]${NC} Ja, automatisch starten  ${YELLOW}(empfohlen)${NC}"
  echo -e "  ${YELLOW}[n]${NC} Nein, nur manuell starten"
  echo ""
  AUTOSTART_CHOICE=""
  for i in $(seq 60 -1 1); do
    printf "\r  Auswahl [J/n] (automatisch Ja in %2ds): " "$i"
    if read -rn1 -t1 AUTOSTART_CHOICE <&3 2>/dev/null; then
      echo ""
      break
    fi
  done
  if [ -z "$AUTOSTART_CHOICE" ]; then
    echo ""
    echo "  → Zeitüberschreitung – Autostart wird aktiviert"
    AUTOSTART_CHOICE="j"
  fi

  case "${AUTOSTART_CHOICE,,}" in
    n|nein|no) AUTOSTART=false ;;
    *)          AUTOSTART=true  ;;
  esac

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

  if [ "$AUTOSTART" = true ]; then
    sudo systemctl enable "${SERVICE_NAME}.service" --quiet
    ok "Autostart aktiviert"
  else
    sudo systemctl disable "${SERVICE_NAME}.service" --quiet 2>/dev/null || true
    ok "Autostart deaktiviert – manuell starten mit:"
    info "  sudo systemctl start ${SERVICE_NAME}"
  fi

  # Einmalig jetzt starten
  sudo systemctl restart "${SERVICE_NAME}.service"

  sleep 3
  if sudo systemctl is-active --quiet "${SERVICE_NAME}"; then
    ok "Service läuft"
  else
    warn "Service nicht gestartet – Logs:"
    sudo journalctl -u "${SERVICE_NAME}" -n 20 --no-pager || true
  fi
fi

# ── Fertig ───────────────────────────────────────────────────
IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
echo ""
echo "============================================================"
echo -e "  ${GREEN}${BOLD}Installation abgeschlossen!${NC}"
echo ""
echo "  Dashboard:  http://${IP}:${PORT}"
echo ""
echo "  Befehle:"
echo "    Status:   sudo systemctl status ${SERVICE_NAME}"
echo "    Logs:     sudo journalctl -u ${SERVICE_NAME} -f"
echo "    Neustart: sudo systemctl restart ${SERVICE_NAME}"
echo "    Stopp:    sudo systemctl stop ${SERVICE_NAME}"
echo ""
if id -nG "$SERVICE_USER" 2>/dev/null | grep -qw bluetooth; then
  true
else
  echo -e "  ${YELLOW}Hinweis:${NC} Für Bluetooth bitte einmal ab- und wieder anmelden"
  echo "  (Gruppenänderung wird erst nach neuem Login aktiv)."
  echo ""
fi
echo "============================================================"
echo ""
