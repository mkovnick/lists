#!/bin/bash
# Setup script for AutoScout24 scraper
set -e

echo "Installing Python dependencies..."
pip install -r "$(dirname "$0")/requirements.txt"

echo "Installing Playwright Chromium browser..."
python -m playwright install chromium

echo ""
echo "Setup complete! Usage:"
echo "  python autoscout24/scraper.py <autoscout24-url>"
echo "  python autoscout24/compare.py"
