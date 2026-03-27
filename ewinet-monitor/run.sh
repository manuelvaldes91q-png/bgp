#!/bin/bash
# Ewinet Route Monitor - Diagnostico Local
# Uso: ./run.sh [--once] [--daemon] [--debug]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 no encontrado. Instala Python 3.10+"
    exit 1
fi

# Check system deps
MISSING=""
for cmd in mtr traceroute whois; do
    if ! command -v $cmd &> /dev/null; then
        MISSING="$MISSING $cmd"
    fi
done

if [ -n "$MISSING" ]; then
    echo "ERROR: Faltan dependencias del sistema:$MISSING"
    echo ""
    echo "Instalar con:"
    echo "  Ubuntu/Debian: sudo apt install mtr-tiny traceroute whois"
    echo "  Fedora/RHEL:   sudo dnf install mtr traceroute whois"
    echo "  macOS:         brew install mtr traceroute whois"
    exit 1
fi

# Install Python deps if needed
if [ ! -d "venv" ]; then
    echo "Creando entorno virtual..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# Parse args
MODE="--once"
EXTRA_ARGS=""

for arg in "$@"; do
    case $arg in
        --once)    MODE="--once" ;;
        --daemon)  MODE="" ;;
        --debug)   EXTRA_ARGS="$EXTRA_ARGS --log-level DEBUG" ;;
        *)         EXTRA_ARGS="$EXTRA_ARGS $arg" ;;
    esac
done

echo ""
echo "Ejecutando diagnostico de rutas..."
echo ""

python3 main.py $MODE $EXTRA_ARGS
