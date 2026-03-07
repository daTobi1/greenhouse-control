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
echo -e "${YELLOW}Folgendes wird entfernt:${NC}"
echo "  - systemd Service '${SERVICE_NAME}'"
echo "  - Installationsverzeichnis: ${INSTALL_DIR}"
echo ""
read -rp "Fortfahren? [j/N] " confirm
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

# ── Python-Berechtigungen zurücksetzen ─────────────────────
PYTHON_BIN="${INSTALL_DIR}/venv/bin/python3"
if [ -f "$PYTHON_BIN" ]; then
  sudo setcap -r "$(readlink -f "$PYTHON_BIN")" 2>/dev/null || true
fi

# ── Installationsverzeichnis entfernen ─────────────────────
if [ -d "$INSTALL_DIR" ]; then
  echo ""
  read -rp "Verzeichnis '${INSTALL_DIR}' (inkl. Datenbank und Timelapse-Aufnahmen) löschen? [j/N] " confirm2
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
