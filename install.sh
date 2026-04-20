#!/usr/bin/env bash
# Quick setup for Photo Slideshow.
# Usage:  ./install.sh
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 not found. Install Xcode CLT or python.org build." >&2
    exit 1
fi

if [ ! -d .venv ]; then
    echo "→ Creating virtualenv in .venv"
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "→ Installing Python dependencies"
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo ""
    echo "⚠️  ffmpeg not found. MP4 output will not work until you install it."
    echo "   brew install ffmpeg"
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo ""
    echo "⚠️  ANTHROPIC_API_KEY is not set in this shell."
    echo "   Add to ~/.zshrc:   export ANTHROPIC_API_KEY=sk-ant-..."
fi

cat <<'MSG'

✓ Setup complete.

To launch the menu bar app:
    source .venv/bin/activate && python main.py

To use from the command line:
    source .venv/bin/activate && python cli.py "your prompt" --format mp4

First-run permission prompts you'll see:
  • Full Disk Access (so osxphotos can read the Photos library)
  • Automation → Photos (so the 'album' format can create albums)
  • Notifications (optional, only fires if bundled as a .app)
MSG
