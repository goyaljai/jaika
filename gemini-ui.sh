#!/bin/bash
set -e

# ─────────────────────────────────────────────
#  Gemini UI — One-click installer & launcher
#  Zero sudo. Everything installs to ~/
# ─────────────────────────────────────────────

INSTALL_DIR="$HOME/.gemini-ui"
ZIP_URL="https://github.com/goyaljai/jaika/raw/refs/heads/main/gemini-ui-app.zip"
ZIP_PATH="/tmp/gemini-ui-app.zip"
PORT=5244
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
  if [ "$OS" = "Darwin" ]; then
    brew install node
  else
    export NVM_DIR="$HOME/.nvm"
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash > /dev/null 2>&1
    [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
    nvm install --lts > /dev/null 2>&1 &
    spin $! "Wiring up the runtime..."
  fi
fi
log "Runtime crystallized"

# ── Python 3 ──
if ! command -v python3 &> /dev/null; then
  if [ "$OS" = "Darwin" ]; then
    brew install python3
  else
    warn "Python 3 is required but not installed."
    echo -e "  ${DIM}   Install it: sudo apt install python3 python3-venv${NC}"
    exit 1
  fi
fi
log "Interpreter summoned"

# ── Gemini CLI ──
if ! command -v gemini &> /dev/null; then
  npm install -g @google/gemini-cli > /dev/null 2>&1 &
  spin $! "Conjuring the brain..."
  log "Brain materialized"
else
  log "Brain locked in"
fi

# ── Pandoc ──
if ! command -v pandoc &> /dev/null; then
  if [ "$OS" = "Darwin" ]; then
    brew install pandoc > /dev/null 2>&1 &
    spin $! "Transmuting the renderer..."
  else
    PANDOC_VER="3.6.4"
    [ "$ARCH" = "x86_64" ] && PANDOC_ARCH="amd64" || PANDOC_ARCH="arm64"
    PANDOC_TAR="pandoc-${PANDOC_VER}-linux-${PANDOC_ARCH}.tar.gz"
    curl -fsSL "https://github.com/jgm/pandoc/releases/download/${PANDOC_VER}/${PANDOC_TAR}" -o "/tmp/${PANDOC_TAR}" 2>/dev/null &
    spin $! "Transmuting the renderer..."
    tar -xzf "/tmp/${PANDOC_TAR}" -C /tmp 2>/dev/null
    cp "/tmp/pandoc-${PANDOC_VER}/bin/pandoc" "$LOCAL_BIN/"
    rm -rf "/tmp/${PANDOC_TAR}" "/tmp/pandoc-${PANDOC_VER}"
  fi
  log "Renderer transmuted"
else
  log "Renderer attuned"
fi

# ── LaTeX engine (TinyTeX — no sudo) ──
if ! command -v pdflatex &> /dev/null; then
  TINYTEX_BIN=""
  if [ "$OS" = "Darwin" ]; then
    TINYTEX_BIN="$HOME/Library/TinyTeX/bin/universal-darwin"
  else
    [ -d "$HOME/.TinyTeX/bin/x86_64-linux" ] && TINYTEX_BIN="$HOME/.TinyTeX/bin/x86_64-linux"
    [ -d "$HOME/.TinyTeX/bin/aarch64-linux" ] && TINYTEX_BIN="$HOME/.TinyTeX/bin/aarch64-linux"
  fi

  if [ -n "$TINYTEX_BIN" ] && [ -f "$TINYTEX_BIN/pdflatex" ]; then
    export PATH="$TINYTEX_BIN:$PATH"
    log "Typesetter resonating"
  else
    curl -fsSL https://yihui.org/tinytex/install-bin-unix.sh 2>/dev/null | sh > /dev/null 2>&1 &
    spin $! "Nebulizing the typesetter..."
    if [ "$OS" = "Darwin" ]; then
      export PATH="$HOME/Library/TinyTeX/bin/universal-darwin:$PATH"
    else
      [ -d "$HOME/.TinyTeX/bin/x86_64-linux" ] && export PATH="$HOME/.TinyTeX/bin/x86_64-linux:$PATH"
      [ -d "$HOME/.TinyTeX/bin/aarch64-linux" ] && export PATH="$HOME/.TinyTeX/bin/aarch64-linux:$PATH"
    fi
    log "Typesetter nebulized"
  fi
else
  log "LaTeX engine humming"
fi

# ────────────────────────────────
#  2. Download & setup app
# ────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_ZIP="$SCRIPT_DIR/gemini-ui-app.zip"

if [ -f "$LOCAL_ZIP" ]; then
  cp "$LOCAL_ZIP" "$ZIP_PATH"
  log "Siphoned local artifact"
else
  curl -fsSL "$ZIP_URL" -o "$ZIP_PATH" &
  spin $! "Beaming down the payload..."
  log "Payload acquired"
fi

mkdir -p "$INSTALL_DIR"
unzip -qo "$ZIP_PATH" -d "$INSTALL_DIR"
rm -f "$ZIP_PATH"
log "Blueprint unpacked"

# ── Python venv ──
if [ ! -d "$INSTALL_DIR/venv" ]; then
  python3 -m venv "$INSTALL_DIR/venv" 2>/dev/null &
  spin $! "Distilling the environment..."
  log "Environment distilled"
fi

"$INSTALL_DIR/venv/bin/pip" install -q flask > /dev/null 2>&1 &
spin $! "Saut\u00e9ing the ingredients..."
log "Saut\u00e9ed to perfection"

# ── Data directories ──
mkdir -p "$INSTALL_DIR/data"/{uploads,skills,sessions,outputs}
mkdir -p "$HOME/.gemini/skills"

# ────────────────────────────────
#  3. Authenticate with Google
# ────────────────────────────────

if [ ! -f "$HOME/.gemini/oauth_creds.json" ] && [ ! -f "$HOME/.config/gemini/oauth_creds.json" ]; then
  echo ""
  info "First time? Let's authenticate."
  echo -e "  ${DIM}   A browser will open — sign in with Google.${NC}"
  echo -e "  ${DIM}   Then come back here.${NC}"
  echo ""
  gemini --prompt "hello" > /dev/null 2>&1 || true
  log "Identity crystallized"
else
  log "Identity cascaded"
fi

# ────────────────────────────────
#  4. Launch server
# ────────────────────────────────

# Detect machine's IP (reliable socket method)
LOCAL_IP=$(python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2>/dev/null)
[ -z "$LOCAL_IP" ] && LOCAL_IP="127.0.0.1"

# Allow env override
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-5244}"

lsof -ti:$PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
fuser -k $PORT/tcp 2>/dev/null || true

cd "$INSTALL_DIR"
"$INSTALL_DIR/venv/bin/python" app.py > "$INSTALL_DIR/server.log" 2>&1 &
SERVER_PID=$!

for i in $(seq 1 15); do
  curl -s http://127.0.0.1:$PORT > /dev/null 2>&1 && break
  sleep 1
done

if ! curl -s http://127.0.0.1:$PORT > /dev/null 2>&1; then
  warn "Server failed to start. Check: $INSTALL_DIR/server.log"
  exit 1
fi

log "Engine ignited"

# Re-detect IP now that server is confirmed up
LOCAL_IP=$(python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "127.0.0.1")

echo ""
echo -e "${GREEN}${BOLD}  ┌──────────────────────────────────────────────┐${NC}"
echo -e "${GREEN}${BOLD}  │                                              │${NC}"
echo -e "${GREEN}${BOLD}  │   Local:   http://127.0.0.1:${PORT}             │${NC}"
echo -e "${GREEN}${BOLD}  │   Network: http://${LOCAL_IP}:${PORT}        │${NC}"
echo -e "${GREEN}${BOLD}  │                                              │${NC}"
echo -e "${GREEN}${BOLD}  │   Press Ctrl+C to stop the server            │${NC}"
echo -e "${GREEN}${BOLD}  │                                              │${NC}"
echo -e "${GREEN}${BOLD}  └──────────────────────────────────────────────┘${NC}"
echo ""

# Open browser AFTER server is confirmed up
if command -v open &> /dev/null; then
  open "http://${LOCAL_IP}:$PORT"
elif command -v xdg-open &> /dev/null; then
  xdg-open "http://${LOCAL_IP}:$PORT"
fi

# ── IP change watcher (background) ──
(
  CURRENT_IP="$LOCAL_IP"
  while kill -0 $SERVER_PID 2>/dev/null; do
    sleep 10
    NEW_IP=$(python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "127.0.0.1")
    if [ "$NEW_IP" != "$CURRENT_IP" ] && [ -n "$NEW_IP" ] && [ "$NEW_IP" != "127.0.0.1" ]; then
      CURRENT_IP="$NEW_IP"
      echo ""
      echo -e "  \033[0;34m→\033[0m  Network changed. New address: \033[0;32mhttp://${NEW_IP}:${PORT}\033[0m"
      if command -v open &> /dev/null; then
        open "http://${NEW_IP}:$PORT"
      elif command -v xdg-open &> /dev/null; then
        xdg-open "http://${NEW_IP}:$PORT"
      fi
    fi
  done
) &
IP_WATCHER_PID=$!

cleanup() {
  echo ""
  info "Winding down..."
  kill $SERVER_PID 2>/dev/null || true
  kill $IP_WATCHER_PID 2>/dev/null || true
  log "Vanished. Until next time."
  exit 0
}

trap cleanup SIGINT SIGTERM
wait $SERVER_PID 2>/dev/null
