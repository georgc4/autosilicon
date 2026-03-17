#!/usr/bin/env bash
# AutoSilicon — Tool Installation (macOS Apple Silicon)
# Installs: Homebrew EDA tools, Python venv, OpenLane2 + nix cache fix
set -euo pipefail

echo "AutoSilicon — Tool Installation"
echo "================================"
echo ""

# Check we're on macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo "ERROR: This script is for macOS. For Linux, install tools via your package manager:"
    echo "  apt install yosys verilator iverilog"
    echo "  pip install cocotb numpy scipy pyyaml"
    exit 1
fi

# Check for Homebrew
if ! command -v brew &>/dev/null; then
    echo "ERROR: Homebrew not found. Install from https://brew.sh"
    exit 1
fi

PROJ_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── Step 1: Homebrew EDA tools ──────────────────────────────────────────────
echo "[1/5] Installing simulation and synthesis tools via Homebrew..."
brew install yosys verilator icarus-verilog surfer
brew install --cask klayout

# ── Step 2: Python venv ─────────────────────────────────────────────────────
echo ""
echo "[2/5] Setting up Python virtual environment..."
VENV_DIR="$PROJ_ROOT/.venv"
# cocotb 2.0.1 requires Python <= 3.13
PYTHON_BIN="python3.13"
if ! command -v "$PYTHON_BIN" &>/dev/null; then
    echo "  python3.13 not found. Installing via Homebrew..."
    brew install python@3.13
    PYTHON_BIN="/opt/homebrew/bin/python3.13"
fi
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    echo "  Created venv at $VENV_DIR (using $("$PYTHON_BIN" --version))"
elif ! "$VENV_DIR/bin/python3" --version 2>&1 | grep -q "3\.13"; then
    echo "  Existing venv uses wrong Python version, recreating..."
    rm -rf "$VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    echo "  Recreated venv with $("$PYTHON_BIN" --version)"
else
    echo "  Venv already exists at $VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --quiet cocotb cocotbext-wishbone numpy scipy mpmath matplotlib pyyaml

# ── Step 3: Fix Nix binary cache for OpenLane2 ─────────────────────────────
echo ""
echo "[3/5] Configuring Nix binary cache for OpenLane2..."
NIX_CUSTOM_CONF="/etc/nix/nix.custom.conf"
CACHIX_SUB="https://openlane.cachix.org"
CACHIX_KEY="openlane.cachix.org-1:qqdwh+QMNGmZAuyeQJTH9ErW57OWSvdtuwfBKdS254E="

if command -v nix &>/dev/null; then
    if grep -q "openlane.cachix.org" "$NIX_CUSTOM_CONF" 2>/dev/null; then
        echo "  OpenLane cachix already configured in $NIX_CUSTOM_CONF"
    else
        echo "  Adding OpenLane binary cache to $NIX_CUSTOM_CONF (needs sudo)..."
        sudo tee -a "$NIX_CUSTOM_CONF" > /dev/null <<EOF

# OpenLane2 binary cache — avoids rebuilding from source
extra-substituters = $CACHIX_SUB
extra-trusted-public-keys = $CACHIX_KEY
EOF
        echo "  Done. Restarting nix-daemon..."
        sudo launchctl kickstart -k system/org.nixos.nix-daemon 2>/dev/null || true
        echo "  nix-daemon restarted."
    fi
else
    echo "  Nix not installed — skipping. Install Nix first if you need OpenLane2 PnR."
fi

# ── Step 4: Clone OpenLane2 ─────────────────────────────────────────────────
echo ""
echo "[4/5] Setting up OpenLane2..."
OPENLANE_DIR="$PROJ_ROOT/openlane2"
if [ -d "$OPENLANE_DIR" ]; then
    echo "  OpenLane2 already cloned at $OPENLANE_DIR"
    echo "  Updating..."
    git -C "$OPENLANE_DIR" pull --ff-only 2>/dev/null || echo "  (pull failed, using existing)"
else
    echo "  Cloning OpenLane2..."
    git clone https://github.com/efabless/openlane2 "$OPENLANE_DIR"
    echo "  Cloned to $OPENLANE_DIR"
fi

# Quick smoke test: check if nix can resolve the openlane flake
if command -v nix &>/dev/null; then
    echo "  Testing nix binary cache (this should download, NOT build)..."
    if timeout 60 nix build "$OPENLANE_DIR#packages.aarch64-darwin.openlane" --dry-run 2>&1 | head -5; then
        echo "  Binary cache is working."
    else
        echo "  WARNING: nix dry-run failed. You may still see source builds."
        echo "  Try: cd $OPENLANE_DIR && nix develop"
    fi
fi

# ── Step 5: Verify ──────────────────────────────────────────────────────────
echo ""
echo "[5/5] Verifying installations..."
echo -n "  yosys: "; yosys --version 2>/dev/null | head -1 || echo "NOT FOUND"
echo -n "  verilator: "; verilator --version 2>/dev/null | head -1 || echo "NOT FOUND"
echo -n "  iverilog: "; iverilog -V 2>/dev/null | head -1 || echo "NOT FOUND"
echo -n "  surfer: "; surfer --version 2>/dev/null || echo "NOT FOUND"
echo -n "  python3: "; python3 --version 2>/dev/null || echo "NOT FOUND"
echo -n "  cocotb: "; python3 -c "import cocotb; print(cocotb.__version__)" 2>/dev/null || echo "NOT FOUND"
echo -n "  nix: "; nix --version 2>/dev/null || echo "NOT FOUND"
echo "  venv: $VENV_DIR"
if [ -d "$OPENLANE_DIR" ]; then
    echo "  openlane2: $OPENLANE_DIR"
else
    echo "  openlane2: NOT CLONED"
fi

echo ""
echo "Done! Next steps:"
echo "  1. Run 'make toolcheck' to verify all tools"
echo "  2. Test OpenLane2: cd $OPENLANE_DIR && nix develop --command openlane --version"
echo "  3. If nix still builds from source, check /etc/nix/nix.custom.conf"
