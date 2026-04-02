#!/bin/bash
# Setup AutoScout24 scraper on macOS
set -e

echo "Installing Python dependencies..."
pip3 install -r "$(dirname "$0")/requirements.txt"

echo "Installing Chromium browser for Playwright..."
python3 -m playwright install chromium

echo ""
echo "Setup complete!"
echo ""
echo "To start the app:"
echo "  python3 autoscout24/app.py --host 0.0.0.0"
echo ""
echo "Then open the URL shown in terminal — works on Mac and iPhone (same Wi-Fi)."
