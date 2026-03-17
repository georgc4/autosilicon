#!/usr/bin/env bash
# AutoSilicon — OpenLane2 Installation Guide
# OpenLane2 uses Nix for reproducible builds.
set -euo pipefail

echo "AutoSilicon — OpenLane2 Installation"
echo "======================================"
echo ""

# Step 1: Check for Nix
if command -v nix &>/dev/null; then
    echo "[✓] Nix is installed: $(nix --version)"
else
    echo "[1/3] Installing Nix (Determinate Systems installer)..."
    echo "  This will install the Nix package manager."
    echo ""
    read -rp "  Proceed? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        curl --proto '=https' --tlsv1.2 -sSf -L \
            https://install.determinate.systems/nix | sh -s -- install
        echo ""
        echo "  Nix installed. You may need to restart your shell."
        echo "  Re-run this script after restarting."
        exit 0
    else
        echo "  Skipping Nix install."
        exit 0
    fi
fi

# Step 2: Clone OpenLane2
OPENLANE_DIR="${OPENLANE_ROOT:-$HOME/openlane2}"
if [ -d "$OPENLANE_DIR" ]; then
    echo "[✓] OpenLane2 directory exists at $OPENLANE_DIR"
else
    echo "[2/3] Cloning OpenLane2..."
    git clone https://github.com/efabless/openlane2 "$OPENLANE_DIR"
    echo "  Cloned to $OPENLANE_DIR"
fi

# Step 3: Test nix-shell
echo "[3/3] Testing OpenLane2 nix-shell (this may take a while on first run)..."
echo "  Running: cd $OPENLANE_DIR && nix-shell --run 'openlane --version'"
cd "$OPENLANE_DIR" && nix-shell --run 'openlane --version' 2>/dev/null && \
    echo "  OpenLane2 is working!" || \
    echo "  WARNING: nix-shell test failed. Check Nix installation."

echo ""
echo "Done! Set OPENLANE_ROOT=$OPENLANE_DIR in your shell profile."
echo "Then run 'make foc-pnr-check' from the AutoSilicon root."
