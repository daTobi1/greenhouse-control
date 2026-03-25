#!/usr/bin/env bash
# ============================================================
# Greenhouse Control – Deinstallation
# Verwendung:
#   curl -fsSL https://raw.githubusercontent.com/daTobi1/greenhouse-control/master/uninstall.sh | bash
# oder lokal:
#   bash uninstall.sh
# ============================================================
set -euo pipefail

INSTALL_DIR="${GREENHOUSE_DIR:-$HOME/greenhouse-control}"
SERVICE_NAME="greenhouse"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

echo ""
echo "============================================================"
echo "  Greenhouse Control – Deinstallation"
echo "============================================================"
echo ""

# ── Terminal-Eingabe vorbereiten (funktioniert auch bei curl | bash) ──
exec 3</dev/tty 2>/dev/null || exec 3</dev/null
echo -e "${YELLOW}Folgendes wird entfernt:${NC}"
echo "  - systemd Service '${SERVICE_NAME}'"
echo "  - sudoers-Regel: /etc/sudoers.d/greenhouse"
echo "  - Polkit-Regeln für WLAN-Steuerung"
echo "  - sysctl-Regel für Port 80"
echo "  - Installationsverzeichnis: ${INSTALL_DIR}"
echo ""
printf "Fortfahren? [j/N] "
read -r confirm <&3 2>/dev/null || confirm=""
[[ "$confirm" =~ ^[jJyY]$ ]] || { echo "Abgebrochen."; exit 0; }

# ── Service stoppen und entfernen ───────────────────────────
if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
  sudo systemctl stop "${SERVICE_NAME}"
  ok "Service gestoppt"
fi

if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
  sudo systemctl disable "${SERVICE_NAME}" --quiet
  ok "Service deaktiviert"
fi

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
if [ -f "$SERVICE_FILE" ]; then
  sudo rm "$SERVICE_FILE"
  sudo systemctl daemon-reload
  ok "Service-Datei entfernt"
fi

# ── sudoers-Regel entfernen ───────────────────────────────
if [ -f /etc/sudoers.d/greenhouse ]; then
  sudo rm /etc/sudoers.d/greenhouse
  ok "sudoers-Regel entfernt"
fi

# ── Polkit-Regeln entfernen ──────────────────────────────
if [ -f /etc/polkit-1/rules.d/10-greenhouse-network.rules ]; then
  sudo rm /etc/polkit-1/rules.d/10-greenhouse-network.rules
  ok "Polkit-Regel entfernt (rules.d)"
fi
if [ -f /etc/polkit-1/localauthority/50-local.d/10-greenhouse-network.pkla ]; then
  sudo rm /etc/polkit-1/localauthority/50-local.d/10-greenhouse-network.pkla
  ok "Polkit-Regel entfernt (pkla)"
fi

# ── sysctl-Regel für Port 80 entfernen ───────────────────
if [ -f /etc/sysctl.d/80-unprivileged-port.conf ]; then
  sudo rm /etc/sysctl.d/80-unprivileged-port.conf
  sudo sysctl --system > /dev/null 2>&1
  ok "sysctl-Regel für Port 80 entfernt"
fi

# ── Python-Berechtigungen zurücksetzen ─────────────────────
PYTHON_BIN="${INSTALL_DIR}/venv/bin/python3"
if [ -f "$PYTHON_BIN" ]; then
  sudo setcap -r "$(readlink -f "$PYTHON_BIN")" 2>/dev/null || true
fi

# ── Installationsverzeichnis entfernen ─────────────────────
if [ -d "$INSTALL_DIR" ]; then
  echo ""
  printf "Verzeichnis '${INSTALL_DIR}' (inkl. Datenbank und Timelapse-Aufnahmen) löschen? [j/N] "
  read -r confirm2 <&3 2>/dev/null || confirm2=""
  if [[ "$confirm2" =~ ^[jJyY]$ ]]; then
    rm -rf "$INSTALL_DIR"
    ok "Verzeichnis entfernt"
  else
    warn "Verzeichnis beibehalten: ${INSTALL_DIR}"
  fi
fi

echo ""
echo "============================================================"
echo -e "  ${GREEN}Deinstallation abgeschlossen.${NC}"
echo "============================================================"
echo ""
