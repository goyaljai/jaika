#!/usr/bin/env bash
# Jaika — Deployment script for chroot Ubuntu / VPS
# Usage: bash deploy.sh

set -euo pipefail

JAIKA_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$JAIKA_DIR/.venv"
PORT="${JAIKA_PORT:-5244}"

echo "=== Jaika Deploy ==="
echo "Directory: $JAIKA_DIR"

# ── System dependencies ──────────────────────────────────────
echo "[1/5] Installing system packages..."
if command -v apt-get &>/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3 python3-pip python3-venv pandoc texlive-latex-base texlive-fonts-recommended 2>/dev/null || true
elif command -v pkg &>/dev/null; then
  pkg install -y python pandoc texlive-installer 2>/dev/null || true
fi

# ── Python venv ──────────────────────────────────────────────
echo "[2/5] Setting up Python venv..."
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r "$JAIKA_DIR/requirements.txt" -q

# ── .env ─────────────────────────────────────────────────────
echo "[3/5] Checking .env..."
if [ ! -f "$JAIKA_DIR/.env" ]; then
  cp "$JAIKA_DIR/.env.example" "$JAIKA_DIR/.env"
  echo "!! Created .env from .env.example — edit it with your Google OAuth credentials"
  echo "!! Then re-run this script"
  exit 1
fi

# ── Data directories ─────────────────────────────────────────
echo "[4/5] Creating data directories..."
mkdir -p "$JAIKA_DIR/data/users" "$JAIKA_DIR/data/skills"

# ── Run ──────────────────────────────────────────────────────
echo "[5/5] Starting Jaika on 0.0.0.0:$PORT ..."
cd "$JAIKA_DIR"
# Workers × threads = max concurrent requests.
# 4 workers × 4 threads = 16 slots — handles ~10 simultaneous users comfortably.
# File-level locking (fcntl) in sessions.py and auth.py keeps writes safe under threads.
exec "$VENV_DIR/bin/gunicorn" \
  --bind "0.0.0.0:$PORT" \
  --workers 4 \
  --threads 4 \
  --timeout 120 \
  --access-logfile - \
  "app:app"
