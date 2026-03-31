#!/bin/bash
set -e

# ─────────────────────────────────────────────
#  Gemini UI — One-click installer & launcher
# ─────────────────────────────────────────────

INSTALL_DIR="$HOME/.gemini-ui"
ZIP_URL="https://github.com/goyaljai/jaika/raw/refs/heads/main/gemini-ui-app.zip"
ZIP_PATH="/tmp/gemini-ui-app.zip"
PORT=5001

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
DIM='\033[0;90m'
BOLD='\033[1m'
NC='\033[0m'

banner() {
  echo ""
  echo -e "${BLUE}${BOLD}  ╔══════════════════════════════╗${NC}"
  echo -e "${BLUE}${BOLD}  ║        Gemini UI             ║${NC}"
  echo -e "${BLUE}${BOLD}  ║   Terminal → Browser bridge  ║${NC}"
  echo -e "${BLUE}${BOLD}  ╚══════════════════════════════╝${NC}"
  echo ""
}

log()   { echo -e "  ${GREEN}✓${NC}  $1"; }
info()  { echo -e "  ${BLUE}→${NC}  $1"; }
warn()  { echo -e "  ${RED}!${NC}  $1"; }
spin()  {
  local pid=$1 msg=$2
  local chars='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
  while kill -0 "$pid" 2>/dev/null; do
    for (( i=0; i<${#chars}; i++ )); do
      printf "\r  ${BLUE}%s${NC}  %s" "${chars:$i:1}" "$msg"
      sleep 0.1
    done
  done
  wait "$pid" 2>/dev/null
  local rc=$?
  printf "\r"
  return $rc
}

banner

OS="$(uname -s)"
ARCH="$(uname -m)"

# ── Ask for sudo upfront on Linux ──
if [ "$OS" = "Linux" ]; then
  echo -e "  ${BLUE}→${NC}  Some packages need admin privileges."
  echo -e "  ${DIM}   You'll be asked for your password once.${NC}"
  echo ""
  sudo -v || { warn "Could not get sudo. Some installs may fail."; }
  # Keep sudo ticket alive
  ( while true; do sudo -n true 2>/dev/null; sleep 50; kill -0 "$$" 2>/dev/null || exit; done ) &
  SUDO_KEEPALIVE_PID=$!
  echo ""
fi

# ────────────────────────────────
#  1. Dependencies
# ────────────────────────────────

# ── Homebrew (macOS) ──
if [ "$OS" = "Darwin" ] && ! command -v brew &> /dev/null; then
  info "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  [ "$ARCH" = "arm64" ] && eval "$(/opt/homebrew/bin/brew shellenv)" || eval "$(/usr/local/bin/brew shellenv)"
  log "Homebrew"
fi

# ── Node.js ──
if ! command -v node &> /dev/null; then
  info "Installing Node.js..."
  if [ "$OS" = "Darwin" ]; then
    brew install node
  elif command -v apt-get &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - > /dev/null 2>&1
    sudo apt-get install -y -qq nodejs > /dev/null 2>&1
  elif command -v dnf &> /dev/null; then
    curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash - > /dev/null 2>&1
    sudo dnf install -y -q nodejs > /dev/null 2>&1
  else
    warn "Install Node.js 18+ manually: https://nodejs.org/"
    exit 1
  fi
fi
log "Node.js $(node -v)"

# ── Python 3 ──
if ! command -v python3 &> /dev/null; then
  info "Installing Python 3..."
  if [ "$OS" = "Darwin" ]; then
    brew install python3
  elif command -v apt-get &> /dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-venv python3-pip > /dev/null 2>&1
  elif command -v dnf &> /dev/null; then
    sudo dnf install -y -q python3 python3-pip > /dev/null 2>&1
  fi
fi
log "Python $(python3 --version | cut -d' ' -f2)"

# ── Gemini CLI ──
if ! command -v gemini &> /dev/null; then
  npm install -g @google/gemini-cli > /dev/null 2>&1 &
  spin $! "Installing Gemini CLI..."
  log "Gemini CLI installed"
else
  log "Gemini CLI $(gemini --version 2>/dev/null | tail -1)"
fi

# ── Pandoc ──
if ! command -v pandoc &> /dev/null; then
  if [ "$OS" = "Darwin" ]; then
    brew install pandoc > /dev/null 2>&1 &
    spin $! "Installing Pandoc..."
  elif command -v apt-get &> /dev/null; then
    sudo apt-get install -y -qq pandoc > /dev/null 2>&1
  elif command -v dnf &> /dev/null; then
    sudo dnf install -y -q pandoc > /dev/null 2>&1
  fi
  log "Pandoc installed"
else
  log "Pandoc $(pandoc --version | head -1 | cut -d' ' -f2)"
fi

# ── LaTeX engine ──
if ! command -v pdflatex &> /dev/null && ! command -v xelatex &> /dev/null; then
  info "Installing LaTeX engine..."
  if [ "$OS" = "Darwin" ]; then
    brew install --cask basictex 2>/dev/null && {
      eval "$(/usr/libexec/path_helper)" 2>/dev/null || true
      export PATH="/Library/TeX/texbin:$PATH"
      log "LaTeX engine installed"
    } || {
      warn "LaTeX needs password. Run later: brew install --cask basictex"
    }
  elif command -v apt-get &> /dev/null; then
    sudo apt-get install -y -qq texlive-latex-base texlive-fonts-recommended texlive-latex-extra > /dev/null 2>&1 &
    spin $! "Installing LaTeX (this may take a few minutes)..."
    log "LaTeX engine installed"
  elif command -v dnf &> /dev/null; then
    sudo dnf install -y -q texlive-scheme-basic > /dev/null 2>&1 &
    spin $! "Installing LaTeX..."
    log "LaTeX engine installed"
  fi
else
  log "LaTeX engine found"
fi

# ────────────────────────────────
#  2. Download & setup app
# ────────────────────────────────

info "Downloading Gemini UI..."
curl -fsSL "$ZIP_URL" -o "$ZIP_PATH"
log "Downloaded"

mkdir -p "$INSTALL_DIR"
unzip -qo "$ZIP_PATH" -d "$INSTALL_DIR"
rm -f "$ZIP_PATH"
log "App extracted to $INSTALL_DIR"

# ── Python venv ──
if [ ! -d "$INSTALL_DIR/venv" ]; then
  python3 -m venv "$INSTALL_DIR/venv" 2>/dev/null &
  spin $! "Creating virtual environment..."
  log "Virtual environment created"
fi

"$INSTALL_DIR/venv/bin/pip" install -q flask > /dev/null 2>&1 &
spin $! "Installing Flask..."
log "Flask installed"

# ── Data directories ──
mkdir -p "$INSTALL_DIR/data"/{uploads,skills,sessions,outputs}
mkdir -p "$HOME/.gemini/skills"

# ────────────────────────────────
#  3. Authenticate with Google
# ────────────────────────────────

if [ ! -f "$HOME/.gemini/oauth_creds.json" ] && [ ! -f "$HOME/.config/gemini/oauth_creds.json" ]; then
  echo ""
  info "No Gemini credentials found."
  echo -e "  ${DIM}   A browser will open for Google sign-in.${NC}"
  echo -e "  ${DIM}   Sign in, then come back here.${NC}"
  echo ""
  gemini --prompt "hello" > /dev/null 2>&1 || true
  log "Authentication complete"
else
  log "Google credentials found"
fi

# ────────────────────────────────
#  4. Launch server
# ────────────────────────────────

# Kill any existing instance
lsof -ti:$PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
fuser -k $PORT/tcp 2>/dev/null || true

cd "$INSTALL_DIR"
"$INSTALL_DIR/venv/bin/python" app.py > "$INSTALL_DIR/server.log" 2>&1 &
SERVER_PID=$!

# Wait for server
for i in $(seq 1 15); do
  curl -s http://localhost:$PORT > /dev/null 2>&1 && break
  sleep 1
done

if ! curl -s http://localhost:$PORT > /dev/null 2>&1; then
  warn "Server failed to start. Check: $INSTALL_DIR/server.log"
  exit 1
fi

log "Server running (PID: $SERVER_PID)"

echo ""
echo -e "${GREEN}${BOLD}  ┌─────────────────────────────────────┐${NC}"
echo -e "${GREEN}${BOLD}  │                                     │${NC}"
echo -e "${GREEN}${BOLD}  │   Go to: http://localhost:${PORT}      │${NC}"
echo -e "${GREEN}${BOLD}  │                                     │${NC}"
echo -e "${GREEN}${BOLD}  │   Press Ctrl+C to stop the server   │${NC}"
echo -e "${GREEN}${BOLD}  │                                     │${NC}"
echo -e "${GREEN}${BOLD}  └─────────────────────────────────────┘${NC}"
echo ""

# Open browser
if command -v open &> /dev/null; then
  open "http://localhost:$PORT"
elif command -v xdg-open &> /dev/null; then
  xdg-open "http://localhost:$PORT"
fi

# ── Graceful shutdown ──
cleanup() {
  echo ""
  info "Shutting down..."
  kill $SERVER_PID 2>/dev/null || true
  [ -n "${SUDO_KEEPALIVE_PID:-}" ] && kill $SUDO_KEEPALIVE_PID 2>/dev/null || true
  log "Server stopped. Goodbye!"
  exit 0
}

trap cleanup SIGINT SIGTERM
wait $SERVER_PID 2>/dev/null
