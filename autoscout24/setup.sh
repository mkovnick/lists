#!/bin/bash
# Setup AutoScout24 scraper on macOS
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "Creating virtual environment..."
python3 -m venv "$VENV_DIR"

echo "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

echo "Installing Chromium browser for Playwright..."
"$VENV_DIR/bin/python" -m playwright install chromium

echo ""
echo "✅ Setup complete!"
echo ""
echo "To start the app:"
echo "  $VENV_DIR/bin/python $SCRIPT_DIR/app.py --host 0.0.0.0"
echo ""
echo "Or activate the venv first:"
echo "  source $VENV_DIR/bin/activate"
echo "  python autoscout24/app.py --host 0.0.0.0"
