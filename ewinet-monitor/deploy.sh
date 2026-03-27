#!/bin/bash
# Ewinet Route Monitor - Deployment Script
# Run with: sudo bash deploy.sh

set -e

INSTALL_DIR="/opt/ewinet-monitor"
SERVICE_USER="ewinet"

echo "========================================="
echo "  Ewinet Route Monitor - Deploy Script"
echo "========================================="

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Run as root (sudo bash deploy.sh)"
    exit 1
fi

# Install system dependencies
echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv mtr-tiny traceroute whois

# Create service user
echo "[2/7] Creating service user..."
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd -r -s /bin/false -d "$INSTALL_DIR" "$SERVICE_USER"
    echo "Created user: $SERVICE_USER"
else
    echo "User $SERVICE_USER already exists"
fi

# Copy application
echo "[3/7] Installing application to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp -r . "$INSTALL_DIR/"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

# Create virtual environment and install deps
echo "[4/7] Setting up Python virtual environment..."
cd "$INSTALL_DIR"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# Create .env if not exists
echo "[5/7] Configuring environment..."
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo "Created .env - EDIT THIS FILE with your credentials!"
    echo "  nano $INSTALL_DIR/.env"
fi

# Create data and logs directories
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/logs"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/data" "$INSTALL_DIR/logs"

# Install PM2
echo "[6/7] Installing PM2..."
if ! command -v pm2 &>/dev/null; then
    if ! command -v node &>/dev/null; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
        apt-get install -y -qq nodejs
    fi
    npm install -g pm2
fi

# Start with PM2
echo "[7/7] Starting service with PM2..."
cd "$INSTALL_DIR"
pm2 start ecosystem.config.js
pm2 save

# Setup PM2 startup
pm2 startup systemd -u root --hp /root 2>/dev/null || true

echo ""
echo "========================================="
echo "  Deployment Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Edit config:   nano $INSTALL_DIR/.env"
echo "  2. Edit providers: nano $INSTALL_DIR/config/providers.yaml"
echo "  3. Restart:       pm2 restart ewinet-monitor"
echo "  4. View logs:     pm2 logs ewinet-monitor"
echo "  5. Test Telegram: cd $INSTALL_DIR && python3 main.py --test-telegram"
echo ""
