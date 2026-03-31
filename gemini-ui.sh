#!/bin/bash
set -e

# ─────────────────────────────────────────────
#  Gemini UI — One-click installer & launcher
# ─────────────────────────────────────────────

INSTALL_DIR="$HOME/.gemini-ui"
ZIP_NAME="gemini-ui-app.zip"
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

log()   { echo -e "${GREEN}[✓]${NC} $1"; }
info()  { echo -e "${BLUE}[i]${NC} $1"; }
warn()  { echo -e "${RED}[!]${NC} $1"; }

banner

# ── Detect OS ──
OS="$(uname -s)"
ARCH="$(uname -m)"

# ── Pre-flight: ask for sudo once on Linux ──
if [ "$OS" = "Linux" ]; then
  info "Some packages may need admin privileges to install."
  info "You may be prompted for your password once."
  echo ""
  sudo -v 2>/dev/null || true
  # Keep sudo alive in background
  while true; do sudo -n true 2>/dev/null; sleep 50; kill -0 "$$" 2>/dev/null || exit; done &
  SUDO_KEEPALIVE_PID=$!
fi

info "Checking dependencies..."

# ── 1. Install Homebrew (macOS) if needed ──
if [ "$OS" = "Darwin" ]; then
  if ! command -v brew &> /dev/null; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for this session
    if [ "$ARCH" = "arm64" ]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    else
      eval "$(/usr/local/bin/brew shellenv)"
    fi
    log "Homebrew installed"
  fi
fi

# ── 2. Install Node.js if needed ──
if ! command -v node &> /dev/null; then
  info "Installing Node.js..."
  if [ "$OS" = "Darwin" ]; then
    brew install node
  elif [ "$OS" = "Linux" ]; then
    if command -v apt-get &> /dev/null; then
      curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
      sudo apt-get install -y nodejs
    elif command -v dnf &> /dev/null; then
      curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash -
      sudo dnf install -y nodejs
    elif command -v yum &> /dev/null; then
      curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash -
      sudo yum install -y nodejs
    else
      warn "Cannot auto-install Node.js on this Linux distro."
      echo "    Install Node.js 18+ manually: https://nodejs.org/"
      exit 1
    fi
  else
    warn "Unsupported OS: $OS"
    echo "    Install Node.js 18+ manually: https://nodejs.org/"
    exit 1
  fi
  log "Node.js installed"
fi
log "Node.js $(node -v)"

# ── 3. Install Python 3 if needed ──
if ! command -v python3 &> /dev/null; then
  info "Installing Python 3..."
  if [ "$OS" = "Darwin" ]; then
    brew install python3
  elif [ "$OS" = "Linux" ]; then
    if command -v apt-get &> /dev/null; then
      sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
    elif command -v dnf &> /dev/null; then
      sudo dnf install -y python3 python3-pip
    elif command -v yum &> /dev/null; then
      sudo yum install -y python3 python3-pip
    fi
  fi
  log "Python 3 installed"
fi
log "Python $(python3 --version | cut -d' ' -f2)"

# ── 4. Install Gemini CLI if needed ──
if ! command -v gemini &> /dev/null; then
  info "Installing Gemini CLI..."
  npm install -g @google/gemini-cli
  log "Gemini CLI installed"
else
  log "Gemini CLI $(gemini --version 2>/dev/null | tail -1)"
fi

# ── 5. Install Pandoc + LaTeX for PDF generation ──
if ! command -v pandoc &> /dev/null; then
  info "Installing Pandoc..."
  if [ "$OS" = "Darwin" ]; then
    brew install pandoc
  elif command -v apt-get &> /dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq pandoc
  elif command -v dnf &> /dev/null; then
    sudo dnf install -y -q pandoc
  fi
  log "Pandoc installed"
else
  log "Pandoc $(pandoc --version | head -1 | cut -d' ' -f2)"
fi

if ! command -v pdflatex &> /dev/null && ! command -v xelatex &> /dev/null; then
  info "Installing LaTeX engine (for PDF generation)..."
  if [ "$OS" = "Darwin" ]; then
    if brew install --cask basictex 2>/dev/null; then
      eval "$(/usr/libexec/path_helper)" 2>/dev/null || true
      export PATH="/Library/TeX/texbin:$PATH"
      log "LaTeX engine installed"
    else
      warn "LaTeX install needs your password. Run manually after:"
      echo -e "${DIM}    brew install --cask basictex${NC}"
      echo -e "${DIM}    (PDF generation will use fallback until then)${NC}"
    fi
  elif command -v apt-get &> /dev/null; then
    sudo apt-get install -y -qq texlive-latex-base texlive-fonts-recommended texlive-latex-extra
    log "LaTeX engine installed"
  elif command -v dnf &> /dev/null; then
    sudo dnf install -y -q texlive-scheme-basic
    log "LaTeX engine installed"
  fi
else
  log "LaTeX engine found"
fi

# ── 6. Unzip app into hidden directory ──
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$SCRIPT_DIR/$ZIP_NAME" ]; then
  warn "Cannot find $ZIP_NAME in the same directory as this script."
  warn "Make sure gemini-ui-app.zip is next to gemini-ui.sh"
  exit 1
fi

info "Setting up in $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
unzip -qo "$SCRIPT_DIR/$ZIP_NAME" -d "$INSTALL_DIR"
log "App extracted"

# ── 6. Create Python venv & install Flask ──
if [ ! -d "$INSTALL_DIR/venv" ]; then
  info "Creating Python virtual environment..."
  python3 -m venv "$INSTALL_DIR/venv"
  log "Virtual environment created"
fi

info "Installing dependencies..."
"$INSTALL_DIR/venv/bin/pip" install -q flask
log "Dependencies installed"

# ── 7. Create data directories ──
mkdir -p "$INSTALL_DIR/data/uploads"
mkdir -p "$INSTALL_DIR/data/skills"
mkdir -p "$INSTALL_DIR/data/sessions"
mkdir -p "$INSTALL_DIR/data/outputs"

# ── 8. Create .gemini config dir if needed ──
mkdir -p "$HOME/.gemini"
mkdir -p "$HOME/.gemini/skills"

# ── 9. Authenticate with Gemini if needed ──
if [ ! -f "$HOME/.gemini/oauth_creds.json" ] && [ ! -f "$HOME/.config/gemini/oauth_creds.json" ]; then
  info "No Gemini credentials found. Opening browser for Google sign-in..."
  echo -e "${DIM}    A browser window will open. Sign in with your Google account.${NC}"
  echo -e "${DIM}    After signing in, return here.${NC}"
  echo ""
  gemini --prompt "hello" > /dev/null 2>&1 || true
  echo ""
  log "Authentication complete"
else
  log "Gemini credentials found"
fi

# ── 10. Kill any existing process on the port ──
lsof -ti:$PORT 2>/dev/null | xargs kill -9 2>/dev/null || true

# ── 11. Launch the server ──
info "Starting Gemini UI server..."

cd "$INSTALL_DIR"
"$INSTALL_DIR/venv/bin/python" app.py > "$INSTALL_DIR/server.log" 2>&1 &
SERVER_PID=$!

for i in $(seq 1 15); do
  if curl -s http://localhost:$PORT > /dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -s http://localhost:$PORT > /dev/null 2>&1; then
  warn "Server failed to start. Check $INSTALL_DIR/server.log"
  exit 1
fi

log "Server running (PID: $SERVER_PID)"

# ── 12. Open browser ──
echo ""
echo -e "${GREEN}${BOLD}  ┌─────────────────────────────────────┐${NC}"
echo -e "${GREEN}${BOLD}  │                                     │${NC}"
echo -e "${GREEN}${BOLD}  │   Go to: http://localhost:${PORT}      │${NC}"
echo -e "${GREEN}${BOLD}  │                                     │${NC}"
echo -e "${GREEN}${BOLD}  │   Press Ctrl+C to stop the server   │${NC}"
echo -e "${GREEN}${BOLD}  │                                     │${NC}"
echo -e "${GREEN}${BOLD}  └─────────────────────────────────────┘${NC}"
echo ""

if command -v open &> /dev/null; then
  open "http://localhost:$PORT"
elif command -v xdg-open &> /dev/null; then
  xdg-open "http://localhost:$PORT"
fi

cleanup() {
  echo ""
  info "Shutting down..."
  kill $SERVER_PID 2>/dev/null || true
  # Kill sudo keepalive if running
  [ -n "${SUDO_KEEPALIVE_PID:-}" ] && kill $SUDO_KEEPALIVE_PID 2>/dev/null || true
  log "Server stopped. Goodbye!"
  exit 0
}

trap cleanup SIGINT SIGTERM
wait $SERVER_PID 2>/dev/null
