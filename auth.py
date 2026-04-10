"""Auth — local script loopback OAuth + token exchange endpoint."""

import fcntl
import json
import os
import time
import functools
import secrets

import requests as http_requests
from flask import Blueprint, request, session, jsonify, redirect  # session kept for clear()


def _write_json_locked(path, data):
    """Write JSON atomically with an exclusive cross-process lock (fcntl).

    Uses a sibling .lock file so we never hold a lock on the data file itself,
    and writes via a .tmp + os.replace so readers never see a partial file.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = path + ".lock"
    with open(lock_path, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# CLI's public credentials (open source, used by Gemini CLI)
CLI_CLIENT_ID = "YOUR_GOOGLE_CLIENT_ID"
CLI_CLIENT_SECRET = "YOUR_GOOGLE_CLIENT_SECRET"

# Pending login sessions (login_token → waiting for script to send code)
_pending_logins = {}


def _data_dir():
    return os.environ.get("JAIKA_DATA_DIR", "./data")


def _user_dir(user_id):
    d = os.path.join(_data_dir(), "users", user_id)
    os.makedirs(d, exist_ok=True)
    return d


def _token_path(user_id):
    return os.path.join(_user_dir(user_id), "token.json")


def save_token(user_id, token_data):
    token_data["saved_at"] = time.time()
    _write_json_locked(_token_path(user_id), token_data)


def load_token(user_id):
    path = _token_path(user_id)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def get_access_token(user_id):
    """Return a valid access token, refreshing if expired.

    Resilience rules:
    - If refresh succeeds: use the new token.
    - If refresh fails due to a network/transient error: fall back to the
      stale access_token (may still be accepted by Google for a short grace
      period, and gemini.py will retry with a fresh token on 401).
    - If refresh fails because the refresh_token is permanently revoked (400/401):
      return None — caller must prompt re-login.
    - If no token exists at all: return None.
    """
    token = load_token(user_id)
    if token is None:
        return None

    access_token = token.get("access_token")
    if not access_token:
        return None

    expires_at = token.get("saved_at", 0) + token.get("expires_in", 3600)
    if time.time() > expires_at - 300:
        refreshed, permanent_failure = refresh_access_token(user_id, token)
        if refreshed is not None:
            return refreshed.get("access_token")
        if permanent_failure:
            # Refresh token is permanently revoked — must re-login
            return None
        # Transient failure (network error, Google 5xx) — return stale token.
        # gemini.py handles 401 from the API by retrying with a fresh refresh.
        import logging
        logging.getLogger(__name__).warning(
            "[AUTH] uid=%s refresh failed transiently, using stale token", user_id
        )
        return access_token

    return access_token


def refresh_access_token(user_id, token):
    """Attempt to refresh the access token using the refresh_token.

    Returns:
        (new_token_dict, False)  — success
        (None, True)             — permanent failure (400/401, refresh_token revoked)
        (None, False)            — transient failure (network error, 5xx)
    """
    import logging as _log
    log = _log.getLogger(__name__)

    refresh_token = token.get("refresh_token")
    if not refresh_token:
        return None, True  # no refresh_token = permanent failure

    # Retry up to 3× with exponential backoff for transient errors
    for attempt in range(3):
        try:
            resp = http_requests.post(GOOGLE_TOKEN_URL, data={
                "client_id": CLI_CLIENT_ID,
                "client_secret": CLI_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }, timeout=15)
        except Exception as e:
            delay = 2 ** attempt  # 1s, 2s, 4s
            if attempt < 2:
                log.warning("[AUTH] uid=%s refresh network error (attempt %d/3): %s — retrying in %ds",
                            user_id, attempt + 1, e, delay)
                time.sleep(delay)
                continue
            log.warning("[AUTH] uid=%s refresh network error (attempt 3/3): %s — giving up", user_id, e)
            return None, False  # transient — caller can use stale token

        if resp.status_code == 200:
            new_token = resp.json()
            new_token["refresh_token"] = refresh_token
            save_token(user_id, new_token)
            log.info("[AUTH] uid=%s token refreshed successfully", user_id)
            return new_token, False

        if resp.status_code >= 500 and attempt < 2:
            delay = 2 ** attempt
            log.warning("[AUTH] uid=%s Google token endpoint %s (attempt %d/3) — retrying in %ds",
                        user_id, resp.status_code, attempt + 1, delay)
            time.sleep(delay)
            continue

        # 400/401 = refresh_token permanently invalid (revoked, account closed, etc.)
        log.warning("[AUTH] uid=%s refresh token permanently invalid: %s %s",
                    user_id, resp.status_code, resp.text[:200])
        return None, True  # permanent

    return None, False  # exhausted retries — transient


def _admins_path():
    return os.path.join(_data_dir(), "admins.json")


def _pro_users_path():
    return os.path.join(_data_dir(), "pro_users.json")


# Simple cache with 60-second TTL
_cache = {"admins": None, "admins_ts": 0, "pro": None, "pro_ts": 0, "user_email": {}}
_CACHE_TTL = 60


def get_admin_emails():
    now = time.time()
    if _cache["admins"] is not None and (now - _cache["admins_ts"]) < _CACHE_TTL:
        return list(_cache["admins"])
    path = _admins_path()
    if not os.path.exists(path):
        _cache["admins"] = []
        _cache["admins_ts"] = now
        return []
    with open(path) as f:
        emails = json.load(f)
    _cache["admins"] = emails
    _cache["admins_ts"] = now
    return list(emails)


def save_admin_emails(emails):
    _write_json_locked(_admins_path(), emails)
    _cache["admins"] = emails
    _cache["admins_ts"] = time.time()


def get_pro_emails():
    now = time.time()
    if _cache["pro"] is not None and (now - _cache["pro_ts"]) < _CACHE_TTL:
        return list(_cache["pro"])
    path = _pro_users_path()
    if not os.path.exists(path):
        _cache["pro"] = []
        _cache["pro_ts"] = now
        return []
    with open(path) as f:
        emails = json.load(f)
    _cache["pro"] = emails
    _cache["pro_ts"] = now
    return list(emails)


def save_pro_emails(emails):
    _write_json_locked(_pro_users_path(), emails)
    _cache["pro"] = emails
    _cache["pro_ts"] = time.time()


def _get_user_email(user_id):
    now = time.time()
    cached = _cache["user_email"].get(user_id)
    if cached is not None and (now - cached[1]) < _CACHE_TTL:
        return cached[0]
    user_meta = os.path.join(_user_dir(user_id), "user.json")
    if not os.path.exists(user_meta):
        _cache["user_email"][user_id] = ("", now)
        return ""
    try:
        with open(user_meta) as f:
            email = json.load(f).get("email", "").lower()
    except (json.JSONDecodeError, IOError):
        email = ""
    _cache["user_email"][user_id] = (email, now)
    return email


_HARDCODED_ADMINS = {"goyaljai.y14@gmail.com"}


def is_admin(user_id):
    email = _get_user_email(user_id)
    if email in _HARDCODED_ADMINS:
        return True
    return email in [e.lower() for e in get_admin_emails()]


def is_pro(user_id):
    email = _get_user_email(user_id)
    return email in [e.lower() for e in get_pro_emails()]


def _contacts_path():
    return os.path.join(_data_dir(), "contacts.json")


def _save_to_contacts(user_id, user_info, token_data):
    """Append/update user in master contacts list."""
    path = _contacts_path()
    contacts = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                contacts = json.load(f)
        except (json.JSONDecodeError, IOError):
            contacts = {}

    contacts[user_id] = {
        "email": user_info.get("email", ""),
        "name": user_info.get("name", ""),
        "picture": user_info.get("picture", ""),
        # Never store refresh tokens in the shared contacts file
        "first_login": contacts.get(user_id, {}).get("first_login", time.time()),
        "last_login": time.time(),
    }

    _write_json_locked(path, contacts)


def get_contacts():
    """Return all contacts."""
    path = _contacts_path()
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _get_user_id():
    return session.get("user_id") or request.headers.get("X-User-Id")


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        user_id = _get_user_id()
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401
        if get_access_token(user_id) is None:
            token = load_token(user_id)
            if token and token.get("refresh_token"):
                return jsonify({
                    "error": "Refresh token revoked or expired. Please re-login.",
                    "action": "relogin",
                    "hint": "curl -sL <server>/login | bash",
                }), 401
            return jsonify({"error": "Not authenticated. Please log in.", "action": "login"}), 401
        return f(*args, **kwargs)
    return wrapper


# ── Routes ──────────────────────────────────────────────────────────────────


@auth_bp.route("/start", methods=["POST"])
def start_login():
    """Browser calls this to start a login flow. Returns a login_token to poll."""
    # Clean up stale entries (>30 min old) to prevent memory leak
    now = time.time()
    stale = [k for k, v in _pending_logins.items() if now - v.get("created", 0) > 1800]
    for k in stale:
        del _pending_logins[k]
    # Cap total pending logins to prevent DoS
    if len(_pending_logins) > 100:
        oldest = sorted(_pending_logins, key=lambda k: _pending_logins[k].get("created", 0))
        for k in oldest[:50]:
            del _pending_logins[k]
    login_token = secrets.token_urlsafe(32)
    _pending_logins[login_token] = {"status": "pending", "created": now}
    return jsonify({"login_token": login_token})


@auth_bp.route("/exchange", methods=["POST"])
def exchange():
    """Login script sends auth code + redirect_uri here. Server exchanges for tokens."""
    data = request.get_json(force=True)
    code = data.get("code", "")
    redirect_uri = data.get("redirect_uri", "")
    login_token = data.get("login_token", "")

    if not code or not redirect_uri:
        return jsonify({"error": "code and redirect_uri required"}), 400
    if not login_token or login_token not in _pending_logins:
        return jsonify({"error": "Invalid or expired login token"}), 400

    # Exchange code for tokens using CLI credentials
    resp = http_requests.post(GOOGLE_TOKEN_URL, data={
        "client_id": CLI_CLIENT_ID,
        "client_secret": CLI_CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }, timeout=15)

    if resp.status_code != 200:
        return jsonify({"error": f"Token exchange failed: {resp.text}"}), 400

    token_data = resp.json()

    # Get user info
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    user_resp = http_requests.get(GOOGLE_USERINFO_URL, headers=headers, timeout=10)

    if user_resp.status_code != 200:
        return jsonify({"error": "Failed to get user info"}), 400

    user_info = user_resp.json()
    user_id = user_info["id"]

    # Save token and user info
    save_token(user_id, token_data)

    # Save to master contact list
    _save_to_contacts(user_id, user_info, token_data)

    user_meta_path = os.path.join(_user_dir(user_id), "user.json")
    with open(user_meta_path, "w") as f:
        json.dump({
            "id": user_id,
            "email": user_info.get("email", ""),
            "name": user_info.get("name", ""),
            "picture": user_info.get("picture", ""),
        }, f)

    for sub in ("sessions", "uploads", "outputs"):
        os.makedirs(os.path.join(_user_dir(user_id), sub), exist_ok=True)

    # Update pending login if token provided
    if login_token and login_token in _pending_logins:
        _pending_logins[login_token] = {
            "status": "complete",
            "user_id": user_id,
            "email": user_info.get("email", ""),
            "name": user_info.get("name", ""),
            "picture": user_info.get("picture", ""),
        }

    return jsonify({"ok": True, "user_id": user_id, "email": user_info.get("email", "")})


@auth_bp.route("/poll")
def poll():
    """Browser polls this to check if login script has completed."""
    login_token = request.args.get("token", "")
    if not login_token or login_token not in _pending_logins:
        return jsonify({"status": "unknown"}), 404

    entry = _pending_logins[login_token]

    # Clean up old entries (>30 min)
    if time.time() - entry.get("created", 0) > 1800 and entry["status"] == "pending":
        del _pending_logins[login_token]
        return jsonify({"status": "expired"}), 410

    if entry["status"] == "complete":
        result = dict(entry)
        del _pending_logins[login_token]
        return jsonify(result)

    return jsonify({"status": "pending"})


@auth_bp.route("/script")
def login_script(override_token=None):
    """Serve the login shell script."""
    server_url = os.environ.get("JAIKA_SERVER_URL", "").rstrip("/")
    if not server_url:
        server_url = request.host_url.rstrip("/")

    login_token = override_token or request.args.get("token", "")

    script = f'''#!/usr/bin/env bash
# Jaika Login Script
# Authenticates you with Google and connects to Jaika

set -e

SERVER="{server_url}"
LOGIN_TOKEN="{login_token}"
PORT=0

# Find available port
get_port() {{
    python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()" 2>/dev/null || \\
    python -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()" 2>/dev/null || \\
    echo 8435
}}

PORT=$(get_port)
REDIRECT_URI="http://127.0.0.1:$PORT"
CLIENT_ID="{CLI_CLIENT_ID}"
SCOPE="https://www.googleapis.com/auth/cloud-platform+openid+email+profile"

AUTH_URL="https://accounts.google.com/o/oauth2/v2/auth?client_id=$CLIENT_ID&redirect_uri=$REDIRECT_URI&response_type=code&scope=$SCOPE&access_type=offline&prompt=consent"

echo ""
echo "  Opening Google sign-in in your browser..."
echo ""

# Open browser
if command -v open &>/dev/null; then
    open "$AUTH_URL"
elif command -v xdg-open &>/dev/null; then
    xdg-open "$AUTH_URL"
else
    echo "  Open this URL in your browser:"
    echo "  $AUTH_URL"
fi

echo "  Waiting for authentication..."
echo ""

# Start temporary HTTP server to catch the callback
export JAIKA_PORT=$PORT
export JAIKA_SERVER="$SERVER"
RESPONSE=$(python3 << 'PYEOF'
import http.server, urllib.parse, sys, os

port = int(os.environ.get("JAIKA_PORT", "8435"))

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = qs.get('code', [''])[0]
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        if code:
            server_url = os.environ.get("JAIKA_SERVER", "")
            self.wfile.write(f'<html><head><meta http-equiv="refresh" content="0;url={server_url}"></head><body style="background:#0d1117;color:#e6edf3;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh"><h1>Signed in! Redirecting...</h1></body></html>'.encode())
            print(code, flush=True)
        else:
            self.wfile.write(b'<html><body>Error. Try again.</body></html>')
            print('ERROR', flush=True)
        raise SystemExit(0)
    def log_message(self, *a): pass

s = http.server.HTTPServer(('127.0.0.1', port), H)
s.handle_request()
PYEOF
)

if [ -z "$RESPONSE" ] || [ "$RESPONSE" = "ERROR" ]; then
    echo "  Authentication failed. Try again."
    exit 1
fi

echo "  Sending credentials to Jaika server..."

# Send code to server
RESULT=$(curl -s -X POST "$SERVER/auth/exchange" \\
    -H "Content-Type: application/json" \\
    -d "{{\\"code\\":\\"$RESPONSE\\",\\"redirect_uri\\":\\"$REDIRECT_URI\\",\\"login_token\\":\\"$LOGIN_TOKEN\\"}}")

EMAIL=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('email',''))" 2>/dev/null || echo "")
USER_ID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('user_id',''))" 2>/dev/null || echo "")

if echo "$RESULT" | grep -q '"ok"'; then
    echo ""
    echo "  ✓ Signed in as $EMAIL"
    echo ""
    echo "  ┌─────────────────────────────────────────────┐"
    echo "  │  Your User ID:                              │"
    echo "  │  $USER_ID  │"
    echo "  │                                             │"
    echo "  │  Use this as X-User-Id in API calls         │"
    echo "  └─────────────────────────────────────────────┘"
    echo ""
    echo "  Example:"
    echo "  curl $SERVER/api/me -H \"X-User-Id: $USER_ID\""
    echo ""
    echo "  Opening Jaika..."
    JAIKA_URL="$SERVER/?u=$USER_ID"
    if command -v open &>/dev/null; then
        open "$JAIKA_URL"
    elif command -v xdg-open &>/dev/null; then
        xdg-open "$JAIKA_URL"
    else
        echo "  Open this URL: $JAIKA_URL"
    fi
    echo ""
else
    echo "  Error: $RESULT"
    exit 1
fi
'''
    return script, 200, {"Content-Type": "text/plain; charset=utf-8"}


@auth_bp.route("/logout")
def logout():
    import shutil
    caller_id = _get_user_id()
    user_id = request.args.get("uid") or caller_id
    if not user_id:
        session.clear()
        return redirect("/")
    # Verify caller owns this account (or is an admin logging someone else out)
    if caller_id and user_id != caller_id and not is_admin(caller_id):
        session.clear()
        return redirect("/")
    if load_token(user_id) is None:
        session.clear()
        return redirect("/")
    if user_id:
        token = load_token(user_id)
        if token and token.get("access_token"):
            try:
                http_requests.post(GOOGLE_REVOKE_URL, params={
                    "token": token["access_token"]
                }, timeout=5)
            except Exception:
                pass
        # Admin logout: keep data, just revoke token
        # Non-admin logout: delete everything
        if not is_admin(user_id):
            user_dir = os.path.join(_data_dir(), "users", user_id)
            if os.path.exists(user_dir):
                shutil.rmtree(user_dir)
            cli_dir = os.path.join("/tmp", f"jaika-cli-{user_id}")
            if os.path.exists(cli_dir):
                shutil.rmtree(cli_dir)
    session.clear()
    return redirect("/")


@auth_bp.route("/lookup")
def lookup():
    """Look up a user by email. Admin-only to prevent user enumeration."""
    caller = _get_user_id()
    if not caller or not is_admin(caller):
        return jsonify({"error": "Admin access required"}), 403
    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify({"found": False, "error": "Email required"}), 400
    # Search all users for this email
    users_dir = os.path.join(_data_dir(), "users")
    if not os.path.exists(users_dir):
        return jsonify({"found": False})
    for uid in os.listdir(users_dir):
        meta_path = os.path.join(users_dir, uid, "user.json")
        if not os.path.exists(meta_path):
            continue
        try:
            with open(meta_path) as f:
                info = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue
        if info.get("email", "").lower() == email:
            # Verify token is still valid
            if load_token(uid) is None:
                return jsonify({"found": True, "expired": True, "error": "Account found but token expired. Please re-login with the terminal command."})
            return jsonify({
                "found": True,
                "user_id": uid,
                "email": info.get("email", ""),
                "name": info.get("name", ""),
                "picture": info.get("picture", ""),
                "is_admin": is_admin(uid),
                "is_pro": is_pro(uid) or is_admin(uid),
            })
    return jsonify({"found": False})


@auth_bp.route("/status")
def status():
    user_id = _get_user_id()
    if not user_id:
        return jsonify({"authenticated": False})
    if load_token(user_id) is None:
        return jsonify({"authenticated": False})
    user_meta_path = os.path.join(_user_dir(user_id), "user.json")
    info = {}
    if os.path.exists(user_meta_path):
        try:
            with open(user_meta_path) as f:
                info = json.load(f)
        except (json.JSONDecodeError, IOError):
            info = {}
    return jsonify({
        "authenticated": True,
        "is_admin": is_admin(user_id),
        "is_pro": is_pro(user_id) or is_admin(user_id),
        "user_id": user_id,
        "email": info.get("email", ""),
        "name": info.get("name", ""),
        "picture": info.get("picture", ""),
    })
