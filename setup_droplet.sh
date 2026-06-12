#!/bin/bash
# setup_droplet.sh — One-shot setup for the Digital Ocean self-hosted runner.
# Run as root on a fresh Ubuntu 24.04 droplet.
set -euo pipefail

RUNNER_TOKEN="BWX75ZG7Q5LT4MGMJGNBD23KFN7TQ"
REPO_URL="https://github.com/kinggeorge4/gsccca-lead-gen"
RUNNER_VERSION="2.323.0"

echo "=== 1. System packages ==="
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl \
    tesseract-ocr libtesseract-dev \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 libatk1.0-0 \
    libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2t64 libatspi2.0-0

echo "=== 2. Python packages ==="
pip3 install --break-system-packages \
    playwright beautifulsoup4 lxml gspread \
    google-auth google-auth-oauthlib google-api-python-client \
    pytesseract Pillow

echo "=== 3. Playwright browser ==="
python3 -m playwright install chromium
python3 -m playwright install-deps chromium

echo "=== 4. Cookie directory ==="
mkdir -p /opt/gsccca
chmod 755 /opt/gsccca

echo "=== 5. GitHub Actions runner ==="
cd /root
mkdir -p actions-runner && cd actions-runner

curl -fsSL -o runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
tar xzf runner.tar.gz && rm runner.tar.gz

./config.sh \
    --url "$REPO_URL" \
    --token "$RUNNER_TOKEN" \
    --name "do-droplet" \
    --labels "self-hosted,linux,x64" \
    --unattended \
    --replace

echo "=== 6. Install runner as system service ==="
./svc.sh install root
./svc.sh start

echo ""
echo "✓ Setup complete. Runner status:"
./svc.sh status
