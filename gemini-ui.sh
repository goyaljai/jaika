#!/bin/bash
set -e

# ─────────────────────────────────────────────
#  Gemini UI — One-click installer & launcher
#  Zero sudo. Everything installs to ~/
# ─────────────────────────────────────────────

INSTALL_DIR="$HOME/.gemini-ui"
ZIP_URL="https://github.com/goyaljai/jaika/raw/refs/heads/main/gemini-ui-app.zip"
ZIP_PATH="/tmp/gemini-ui-app.zip"
PORT=5001
LOCAL_BIN="$HOME/.local/bin"

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
mkdir -p "$LOCAL_BIN"
export PATH="$LOCAL_BIN:$PATH"

# ────────────────────────────────
#  1. Dependencies (all userspace)
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
  else
    # Use nvm — installs to ~/.nvm, no sudo
    export NVM_DIR="$HOME/.nvm"
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash > /dev/null 2>&1
    [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
    nvm install --lts > /dev/null 2>&1 &
    spin $! "Installing Node.js via nvm..."
    log "Node.js installed (via nvm)"
  fi
fi
log "Node.js $(node -v)"

# ── Python 3 ──
if ! command -v python3 &> /dev/null; then
  if [ "$OS" = "Darwin" ]; then
    brew install python3
  else
    warn "Python 3 is required but not installed."
    echo -e "  ${DIM}   Install it: sudo apt install python3 python3-venv${NC}"
    echo -e "  ${DIM}   Then re-run this script.${NC}"
    exit 1
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
  info "Installing Pandoc..."
  if [ "$OS" = "Darwin" ]; then
    brew install pandoc > /dev/null 2>&1 &
    spin $! "Installing Pandoc..."
  else
    # Download static binary — no sudo
    PANDOC_VER="3.6.4"
    if [ "$ARCH" = "x86_64" ]; then
      PANDOC_ARCH="amd64"
    else
      PANDOC_ARCH="arm64"
    fi
    PANDOC_TAR="pandoc-${PANDOC_VER}-linux-${PANDOC_ARCH}.tar.gz"
    curl -fsSL "https://github.com/jgm/pandoc/releases/download/${PANDOC_VER}/${PANDOC_TAR}" -o "/tmp/${PANDOC_TAR}" 2>/dev/null &
    spin $! "Downloading Pandoc..."
    tar -xzf "/tmp/${PANDOC_TAR}" -C /tmp 2>/dev/null
    cp "/tmp/pandoc-${PANDOC_VER}/bin/pandoc" "$LOCAL_BIN/"
    rm -rf "/tmp/${PANDOC_TAR}" "/tmp/pandoc-${PANDOC_VER}"
  fi
  log "Pandoc installed"
else
  log "Pandoc $(pandoc --version | head -1 | cut -d' ' -f2)"
fi

# ── LaTeX engine (TinyTeX — no sudo) ──
if ! command -v pdflatex &> /dev/null; then
  # Check if TinyTeX is installed but not on PATH
  TINYTEX_BIN=""
  if [ "$OS" = "Darwin" ]; then
    TINYTEX_BIN="$HOME/Library/TinyTeX/bin/universal-darwin"
  else
    [ -d "$HOME/.TinyTeX/bin/x86_64-linux" ] && TINYTEX_BIN="$HOME/.TinyTeX/bin/x86_64-linux"
    [ -d "$HOME/.TinyTeX/bin/aarch64-linux" ] && TINYTEX_BIN="$HOME/.TinyTeX/bin/aarch64-linux"
  fi

  if [ -n "$TINYTEX_BIN" ] && [ -f "$TINYTEX_BIN/pdflatex" ]; then
    export PATH="$TINYTEX_BIN:$PATH"
    log "TinyTeX found"
  else
    curl -fsSL https://yihui.org/tinytex/install-bin-unix.sh 2>/dev/null | sh > /dev/null 2>&1 &
    spin $! "Installing TinyTeX (no sudo needed)..."
    if [ "$OS" = "Darwin" ]; then
      export PATH="$HOME/Library/TinyTeX/bin/universal-darwin:$PATH"
    else
      [ -d "$HOME/.TinyTeX/bin/x86_64-linux" ] && export PATH="$HOME/.TinyTeX/bin/x86_64-linux:$PATH"
      [ -d "$HOME/.TinyTeX/bin/aarch64-linux" ] && export PATH="$HOME/.TinyTeX/bin/aarch64-linux:$PATH"
    fi
    log "TinyTeX installed"
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
log "App extracted"

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

lsof -ti:$PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
fuser -k $PORT/tcp 2>/dev/null || true

cd "$INSTALL_DIR"
"$INSTALL_DIR/venv/bin/python" app.py > "$INSTALL_DIR/server.log" 2>&1 &
SERVER_PID=$!

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

if command -v open &> /dev/null; then
  open "http://localhost:$PORT"
elif command -v xdg-open &> /dev/null; then
  xdg-open "http://localhost:$PORT"
fi

cleanup() {
  echo ""
  info "Shutting down..."
  kill $SERVER_PID 2>/dev/null || true
  log "Server stopped. Goodbye!"
  exit 0
}

trap cleanup SIGINT SIGTERM
wait $SERVER_PID 2>/dev/null
