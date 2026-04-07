"""Jaika v2 — Main Flask application."""

import base64
import datetime
import functools
import json
import logging
import os
import threading
import urllib.request
import urllib.error

from dotenv import load_dotenv
load_dotenv()

from flask import (
    Flask, Response, jsonify, request, send_file,
    send_from_directory, render_template,
)

from auth import auth_bp, login_required, _get_user_id, is_admin, is_pro, get_admin_emails, save_admin_emails, get_pro_emails, save_pro_emails, get_contacts, load_token
from api_compat import compat_bp
from gemini import generate, stream_generate, transcribe_audio, get_user_tier, generate_image as gemini_generate_image
import files as file_store
from sessions import (
    list_sessions, get_session, create_session, update_session,
    delete_session, add_message, clear_messages, get_conversation_history,
    session_count, enforce_session_limit, delete_all_sessions,
)
from skills import list_skills, get_skill, save_skill, delete_skill, build_system_instruction
from pdf import markdown_to_pdf

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

from werkzeug.middleware.proxy_fix import ProxyFix
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import ipaddress
from urllib.parse import urlparse
import socket

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload limit
app.config["PREFERRED_URL_SCHEME"] = "https"

DATA_DIR = os.environ.get("JAIKA_DATA_DIR", "./data")

# ── Rate Limiting ────────────────────────────────────────────────────────────
def _rate_limit_key():
    """Rate limit by user ID if authenticated, else by IP."""
    uid = _get_user_id()
    return uid if uid else get_remote_address()

limiter = Limiter(
    app=app,
    key_func=_rate_limit_key,
    default_limits=["200 per minute"],       # global default for all routes
    storage_uri="memory://",
)

# ── SSRF Protection ──────────────────────────────────────────────────────────
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("10.0.0.0/8"),         # private
    ipaddress.ip_network("172.16.0.0/12"),      # private
    ipaddress.ip_network("192.168.0.0/16"),     # private
    ipaddress.ip_network("169.254.0.0/16"),     # link-local / cloud metadata
    ipaddress.ip_network("0.0.0.0/8"),          # unspecified
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 private
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]

def is_safe_url(url):
    """Block SSRF: reject URLs that resolve to private/internal IPs."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False
    # Block common dangerous hostnames
    if hostname in ("localhost", "metadata.google.internal"):
        return False
    try:
        # Resolve hostname and check all IPs
        for info in socket.getaddrinfo(hostname, parsed.port or 80):
            ip = ipaddress.ip_address(info[4][0])
            for net in _BLOCKED_NETWORKS:
                if ip in net:
                    return False
    except (socket.gaierror, ValueError):
        return False  # can't resolve = block
    return True

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(compat_bp)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _user_id():
    return _get_user_id()


def _user_dir(sub=""):
    uid = _user_id()
    d = os.path.join(DATA_DIR, "users", uid, sub) if sub else os.path.join(DATA_DIR, "users", uid)
    os.makedirs(d, exist_ok=True)
    return d


def admin_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        uid = _user_id()
        if not uid or not is_admin(uid):
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return wrapper


# ── Pages ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(os.path.join(os.path.dirname(__file__), "static"), filename)


@app.route("/pro")
def pro_page():
    return render_template("pro.html")


@app.route("/slides")
def slides():
    return render_template("slides.html")


@app.route("/login")
def login_shortcut():
    """Shortcut: curl -sL https://server/login | bash"""
    from auth import _pending_logins, login_script as _script_fn
    import secrets as _secrets, time as _time
    token = request.args.get("token", "")
    if not token:
        token = _secrets.token_urlsafe(32)
    if token not in _pending_logins:
        _pending_logins[token] = {"status": "pending", "created": _time.time()}
    # Build script directly instead of redirect
    return _script_fn(override_token=token)


# ── User Info ───────────────────────────────────────────────────────────────

@app.route("/api/me")
@login_required
def me():
    uid = _user_id()
    user_meta_path = os.path.join(_user_dir(), "user.json")
    if os.path.exists(user_meta_path):
        try:
            with open(user_meta_path) as f:
                info = json.load(f)
        except (json.JSONDecodeError, IOError):
            info = {}
    else:
        info = {}

    admin = is_admin(uid)
    pro = is_pro(uid)
    tier = get_user_tier(uid)
    storage_used = _user_storage_used(uid)
    storage_cap = None if admin else (PRO_STORAGE_CAP if pro else USER_STORAGE_CAP)

    # Map internal tier names to Jaika branding
    raw_tier = tier.get("tier_name", "Unknown")
    if "code assist" in raw_tier.lower() or "gemini" in raw_tier.lower():
        display_tier = "Jaika (Powered by Gemini)"
    else:
        display_tier = raw_tier

    info.update({
        "user_id": uid,
        "is_admin": admin,
        "is_pro": pro or admin,
        "tier_id": tier.get("tier_id", "unknown"),
        "tier_name": display_tier,
        "storage_used_bytes": storage_used,
        "storage_cap_bytes": storage_cap,
        "session_limit": None if admin else (25 if pro else 10),
        "file_gen_limit": None if admin else (None if pro else 5),
    })
    return jsonify(info)


# ── Prompt ──────────────────────────────────────────────────────────────────

@app.route("/api/prompt", methods=["POST"])
@limiter.limit("30 per minute")
@login_required
def prompt():
    """Chat with Gemini. Supports text + file attachments via file_ids."""
    uid = _user_id()
    data = request.get_json(force=True)
    prompt_text = data.get("prompt", "").strip()
    session_id = data.get("session_id")
    stream = data.get("stream", False)
    file_ids = data.get("file_ids", [])  # list of file_ids from /api/upload
    thinking = data.get("thinking", False)
    thinking_budget = int(data.get("thinking_budget", 8192))
    grounding = data.get("grounding", False)
    response_format = data.get("response_format")  # "json" or None

    if not prompt_text and not file_ids:
        return jsonify({"error": "Empty prompt"}), 400

    # Input guardrails — block injection attempts
    if prompt_text:
        from prompt_engine import check_input_guardrails
        is_safe, safety_msg = check_input_guardrails(prompt_text)
        if not is_safe:
            return jsonify({"error": safety_msg}), 400

    # Pro-only features
    if thinking and not is_admin(uid) and not is_pro(uid):
        return jsonify({"error": "Thinking mode is a Pro feature. Upgrade at /pro"}), 403
    if grounding and not is_admin(uid) and not is_pro(uid):
        return jsonify({"error": "Web grounding is a Pro feature. Upgrade at /pro"}), 403

    # Get or create session
    if session_id:
        sess = get_session(uid, session_id)
        if not sess:
            return jsonify({"error": "Session not found"}), 404
    else:
        sess = create_session(uid)
        session_id = sess["id"]

    # Load file inline data
    file_data = []
    file_metas = []
    for fid in file_ids:
        inline = file_store.load_file_inline(fid, uid)
        if inline:
            file_data.append(inline)
            file_metas.append({"name": inline["name"], "mime_type": inline["mime_type"],
                                "converted": inline.get("converted", False)})
        else:
            log.warning("File %s not found for user %s", fid, uid)

    # Store user message (file metadata only, not binary)
    add_message(uid, session_id, "user", prompt_text, files=file_metas if file_metas else None)

    # Build conversation history (text-only for previous turns; files only for current)
    history = get_conversation_history(uid, session_id)
    system_instruction = build_system_instruction()

    # Sliding window: keep only the last 20 turn-pairs (40 messages) to save tokens
    MAX_HISTORY_TURNS = 20
    history = history[-(MAX_HISTORY_TURNS * 2):]

    # Inject intent-based hints (code, math, creative writing, etc.)
    if prompt_text:
        from prompt_engine import detect_intent_hints
        hints = detect_intent_hints(prompt_text)
        if hints:
            system_instruction = (system_instruction + "\n\n" + hints).strip() if system_instruction else hints

    # Inject per-user memory as a pinned first exchange (not in system instruction)
    facts = _load_memory(uid)
    if facts:
        mem_msg = [
            {"role": "user", "text": "[Memory context]\n" + "\n".join(f"- {f}" for f in facts), "files": []},
            {"role": "model", "text": "Noted.", "files": []},
        ]
        history = mem_msg + history

    resp_mime = "application/json" if response_format == "json" else None

    if stream:
        def event_stream():
            # First event: session_id so client can continue the conversation
            yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
            full_text = []
            for chunk in stream_generate(uid, history, files=file_data,
                                         system_instruction=system_instruction,
                                         thinking=thinking, thinking_budget=thinking_budget,
                                         grounding=grounding):
                yield chunk
                if chunk.startswith("data: "):
                    try:
                        d = json.loads(chunk[6:])
                        if "text" in d:
                            full_text.append(d["text"])
                    except (json.JSONDecodeError, KeyError):
                        pass
            if full_text:
                add_message(uid, session_id, "model", "".join(full_text))

        return Response(event_stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    result = generate(uid, history, files=file_data, system_instruction=system_instruction,
                      thinking=thinking, thinking_budget=thinking_budget,
                      grounding=grounding, response_mime_type=resp_mime)
    if "error" in result:
        return jsonify({"error": result["error"], "session_id": session_id}), 502

    add_message(uid, session_id, "model", result["text"])

    resp = {
        "type": "text",
        "text": result["text"],
        "session_id": session_id,
    }
    if result.get("grounding"):
        resp["grounding"] = result["grounding"]
    return jsonify(resp)


# ── File Upload ─────────────────────────────────────────────────────────────

USER_STORAGE_CAP = 50 * 1024 * 1024    # 50MB per regular user
PRO_STORAGE_CAP = 500 * 1024 * 1024   # 500MB per pro user
FILE_TTL = 3600  # uploaded files expire after 1 hour

ALLOWED_EXTENSIONS = file_store.EXT_MIME.keys()

def _user_storage_used(uid):
    """Total bytes used by a user across uploads and outputs."""
    total = 0
    for sub in ("uploads", "outputs"):
        d = os.path.join(DATA_DIR, "users", uid, sub)
        if os.path.exists(d):
            for f in os.listdir(d):
                fp = os.path.join(d, f)
                if os.path.isfile(fp):
                    total += os.path.getsize(fp)
    return total


def _schedule_file_delete(file_id, uid, delay=FILE_TTL):
    def _cleanup():
        file_store.delete_file(file_id, uid)
        log.info("Auto-deleted file %s for user %s", file_id, uid)
    threading.Timer(delay, _cleanup).start()


@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    """Upload a file for use in prompts. Supports images, PDFs, DOCX, XLSX, PPTX, audio, video, code."""
    uid = _user_id()
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    # Storage cap check (skip for admin)
    if not is_admin(uid):
        used = _user_storage_used(uid)
        cap = PRO_STORAGE_CAP if is_pro(uid) else USER_STORAGE_CAP
        if used >= cap:
            cap_mb = cap // (1024 * 1024)
            return jsonify({"error": f"Storage limit reached ({cap_mb}MB). Delete old files or upgrade."}), 429

    try:
        meta = file_store.save_upload(uid, f, f.filename)
    except Exception as e:
        log.exception("Upload failed for user %s", uid)
        return jsonify({"error": f"Upload failed: {e}"}), 500

    _schedule_file_delete(meta["file_id"], uid, FILE_TTL)

    return jsonify({
        "file_id": meta["file_id"],
        "name": meta["name"],
        "mime_type": meta["mime_type"],
        "original_type": meta["original_type"],
        "converted": meta["converted"],
        "size": meta["size"],
    })


# ── Files ────────────────────────────────────────────────────────────────────

@app.route("/api/files", methods=["GET"])
@login_required
def list_files():
    """List uploaded files for the current user."""
    uid = _user_id()
    return jsonify({"files": file_store.list_user_files(uid)})


@app.route("/api/files/<file_id>", methods=["GET"])
@login_required
def get_file_meta(file_id):
    """Get metadata for a specific uploaded file."""
    uid = _user_id()
    meta = file_store.get_file_meta(file_id, uid)
    if not meta:
        return jsonify({"error": "File not found"}), 404
    return jsonify(meta)


@app.route("/api/files/<file_id>", methods=["DELETE"])
@login_required
def delete_file(file_id):
    """Delete an uploaded file."""
    uid = _user_id()
    if file_store.delete_file(file_id, uid):
        return jsonify({"ok": True})
    return jsonify({"error": "File not found"}), 404


@app.route("/api/files/<file_id>/download", methods=["GET"])
@login_required
def download_uploaded_file(file_id):
    """Download a raw uploaded file by file_id."""
    uid = _user_id()
    meta = file_store.get_file_meta(file_id, uid)
    if not meta:
        return jsonify({"error": "File not found"}), 404
    path = meta.get("path", "")
    if not path or not os.path.exists(path):
        return jsonify({"error": "File data not found"}), 404
    return send_file(path, as_attachment=True, download_name=meta["name"])


# ── STT ─────────────────────────────────────────────────────────────────────

@app.route("/api/stt", methods=["POST"])
@limiter.limit("20 per minute")
@login_required
def stt():
    """Speech-to-text. POST audio file as multipart 'file' field.
    Supports: mp3, wav, webm, ogg, m4a, flac, aac, aiff.
    Returns: {text}
    """
    uid = _user_id()
    if not is_admin(uid) and not is_pro(uid):
        return jsonify({"error": "Speech-to-text is a Pro feature. Upgrade at /pro"}), 403
    if "file" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "webm"
    mime_type = file_store.get_mime(f.filename)

    upload_dir = _user_dir("uploads")
    audio_path = os.path.join(upload_dir, f"stt_{os.urandom(4).hex()}.{ext}")
    f.save(audio_path)

    try:
        text, error = transcribe_audio(uid, audio_path, mime_type)
    finally:
        try:
            os.remove(audio_path)
        except Exception:
            pass

    if error:
        return jsonify({"error": error}), 502

    return jsonify({"text": text})


@app.route("/api/voice-prompt", methods=["POST"])
@limiter.limit("20 per minute")
@login_required
def voice_prompt():
    """Audio → transcript → Gemini → response.

    Upload audio as multipart 'file' field.
    Optional JSON fields (as form data or query params):
      session_id, stream

    Returns: {transcript, text, session_id}
    Or SSE stream with events: {type: transcript, text: ...} then {text: ...} chunks
    """
    uid = _user_id()
    if not is_admin(uid) and not is_pro(uid):
        return jsonify({"error": "Voice prompt is a Pro feature. Upgrade at /pro"}), 403
    if "file" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    session_id = request.form.get("session_id") or request.args.get("session_id")
    do_stream = request.form.get("stream", "false").lower() == "true"

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "webm"
    mime_type = file_store.get_mime(f.filename)

    upload_dir = _user_dir("uploads")
    audio_path = os.path.join(upload_dir, f"voice_{os.urandom(4).hex()}.{ext}")
    f.save(audio_path)

    try:
        transcript, error = transcribe_audio(uid, audio_path, mime_type)
    finally:
        try:
            os.remove(audio_path)
        except Exception:
            pass

    if error:
        return jsonify({"error": f"Transcription failed: {error}"}), 502

    transcript = transcript.strip()
    if not transcript:
        return jsonify({"error": "No speech detected"}), 400

    # Get or create session
    if session_id:
        sess = get_session(uid, session_id)
        if not sess:
            return jsonify({"error": "Session not found"}), 404
    else:
        sess = create_session(uid)
        session_id = sess["id"]

    add_message(uid, session_id, "user", transcript)
    history = get_conversation_history(uid, session_id)
    system_instruction = build_system_instruction()

    # Sliding window: keep only the last 20 turn-pairs
    MAX_HISTORY_TURNS = 20
    history = history[-(MAX_HISTORY_TURNS * 2):]

    # Inject per-user memory as a pinned first exchange
    facts = _load_memory(uid)
    if facts:
        mem_msg = [
            {"role": "user", "text": "[Memory context]\n" + "\n".join(f"- {f}" for f in facts), "files": []},
            {"role": "model", "text": "Noted.", "files": []},
        ]
        history = mem_msg + history

    if do_stream:
        def event_stream():
            # First event: the transcript
            yield f"data: {json.dumps({'type': 'transcript', 'text': transcript})}\n\n"
            full_text = []
            for chunk in stream_generate(uid, history, system_instruction=system_instruction):
                yield chunk
                if chunk.startswith("data: "):
                    try:
                        d = json.loads(chunk[6:])
                        if "text" in d:
                            full_text.append(d["text"])
                    except (json.JSONDecodeError, KeyError):
                        pass
            if full_text:
                add_message(uid, session_id, "model", "".join(full_text))

        return Response(event_stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    result = generate(uid, history, system_instruction=system_instruction)
    if "error" in result:
        return jsonify({"error": result["error"], "transcript": transcript, "session_id": session_id}), 502

    add_message(uid, session_id, "model", result["text"])

    return jsonify({
        "transcript": transcript,
        "text": result["text"],
        "session_id": session_id,
    })


# ── Output Download ──────────────────────────────────────────────────────────

@app.route("/api/download/<uid>/<path:filename>")
def download(uid, filename):
    """Download a generated output file. No auth header needed — uid is in the URL.
    Works directly in browsers and curl without X-User-Id header.
    """
    # Prevent path traversal
    filename = os.path.basename(filename)
    if not uid or not filename:
        return jsonify({"error": "Invalid"}), 400
    output_dir = os.path.join(DATA_DIR, "users", uid, "outputs")
    return send_from_directory(output_dir, filename, as_attachment=False)


# ── PDF Generation ──────────────────────────────────────────────────────────

@app.route("/api/pdf", methods=["POST"])
@login_required
def generate_pdf():
    uid = _user_id()
    if not is_admin(uid) and not is_pro(uid):
        return jsonify({"error": "PDF generation is a Pro feature. Upgrade at /pro"}), 403
    data = request.get_json(force=True)
    markdown_text = data.get("markdown", "")
    if not markdown_text:
        return jsonify({"error": "No markdown provided"}), 400

    output_dir = _user_dir("outputs")
    path, error = markdown_to_pdf(markdown_text, output_dir)
    if error:
        return jsonify({"error": error}), 500

    filename = os.path.basename(path)
    uid = _user_id()
    return jsonify({"path": f"/api/download/{uid}/{filename}", "filename": filename})


# ── Sessions ────────────────────────────────────────────────────────────────

@app.route("/api/sessions", methods=["GET"])
@login_required
def sessions_list():
    return jsonify(list_sessions(_user_id()))


@app.route("/api/sessions", methods=["POST"])
@login_required
def sessions_create():
    uid = _user_id()
    # Session limits: regular=10, pro=25 (FIFO), admin=unlimited
    if not is_admin(uid):
        if is_pro(uid):
            enforce_session_limit(uid, 24)  # make room for new one
        else:
            count = session_count(uid)
            if count >= 10:
                return jsonify({"error": "Session limit reached (10). Delete old sessions or upgrade to Pro."}), 429
    data = request.get_json(silent=True) or {}
    sess = create_session(uid, title=data.get("title"))
    return jsonify(sess), 201


@app.route("/api/sessions/<session_id>", methods=["GET"])
@login_required
def sessions_get(session_id):
    sess = get_session(_user_id(), session_id)
    if not sess:
        return jsonify({"error": "Not found"}), 404
    return jsonify(sess)


@app.route("/api/sessions/<session_id>", methods=["PUT"])
@login_required
def sessions_update(session_id):
    data = request.get_json(force=True)
    sess = update_session(_user_id(), session_id, title=data.get("title"))
    if not sess:
        return jsonify({"error": "Not found"}), 404
    return jsonify(sess)


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
@login_required
def sessions_delete(session_id):
    if delete_session(_user_id(), session_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404


@app.route("/api/sessions/<session_id>/messages", methods=["POST"])
@login_required
def sessions_add_message(session_id):
    data = request.get_json(force=True)
    msg = add_message(_user_id(), session_id, data.get("role", "user"), data.get("text", ""))
    if not msg:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(msg), 201


@app.route("/api/sessions/<session_id>/messages", methods=["DELETE"])
@login_required
def sessions_clear_messages(session_id):
    if clear_messages(_user_id(), session_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Session not found"}), 404


# ── Skills ──────────────────────────────────────────────────────────────────

@app.route("/api/skills", methods=["GET"])
@login_required
def skills_list():
    return jsonify(list_skills())


@app.route("/api/skills/<name>", methods=["GET"])
@login_required
def skills_get(name):
    content = get_skill(name)
    if content is None:
        return jsonify({"error": "Skill not found"}), 404
    return jsonify({"name": name, "content": content})


@app.route("/api/skills/upload", methods=["POST"])
@login_required
def skills_upload():
    uid = _user_id()
    if not is_admin(uid) and not is_pro(uid):
        return jsonify({"error": "Creating skills is a Pro feature. Upgrade at /pro"}), 403
    if "file" in request.files:
        f = request.files["file"]
        name = os.path.basename(os.path.splitext(f.filename)[0]).replace("..", "").strip()
        content = f.read().decode("utf-8")
    else:
        data = request.get_json(force=True)
        name = os.path.basename(data.get("name", "")).replace("..", "").strip()
        content = data.get("content", "")
    name = os.path.basename(name).replace('..', '').strip()
    if not name or not content:
        return jsonify({"error": "Name and content required"}), 400
    if not save_skill(name, content):
        return jsonify({"error": "Invalid skill name. Use only letters, numbers, hyphens, underscores."}), 400
    return jsonify({"name": name, "ok": True}), 201


@app.route("/api/skills/<name>", methods=["DELETE"])
@login_required
def skills_delete(name):
    uid = _user_id()
    if not is_admin(uid) and not is_pro(uid):
        return jsonify({"error": "Deleting skills is a Pro feature. Upgrade at /pro"}), 403
    if delete_skill(name):
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404


# ── Image & Video Generation ───────────────────────────────────────────────

# Rate limiting for file generation: 5 per user, unlimited for admin
# Structure: {uid: {"count": N, "date": "YYYY-MM-DD"}} — resets daily
_file_gen_counts = {}


def _get_file_gen_count(uid):
    """Return today's file generation count for a user, resetting if date changed."""
    today = datetime.date.today().isoformat()
    entry = _file_gen_counts.get(uid)
    if entry is None or entry.get("date") != today:
        _file_gen_counts[uid] = {"count": 0, "date": today}
        return 0
    return entry["count"]


def _inc_file_gen_count(uid):
    """Increment today's file generation count for a user."""
    today = datetime.date.today().isoformat()
    entry = _file_gen_counts.get(uid)
    if entry is None or entry.get("date") != today:
        _file_gen_counts[uid] = {"count": 1, "date": today}
    else:
        entry["count"] += 1

@app.route("/api/generate/file", methods=["POST"])
@limiter.limit("10 per minute")
@login_required
def generate_file():
    """Generate a file (HTML, SVG, CSV, JSON, Python) using Gemini API."""
    uid = _user_id()
    data = request.get_json(force=True)
    prompt_text = data.get("prompt", "").strip()
    file_type = data.get("type", "html").strip().lower()

    if not prompt_text:
        return jsonify({"error": "Prompt required"}), 400

    valid_types = ["html", "svg", "csv", "json", "py", "image", "video"]
    if file_type not in valid_types:
        return jsonify({"error": f"Invalid type. Use: {', '.join(valid_types)}"}), 400

    # Rate limit + storage cap
    if not is_admin(uid):
        if not is_pro(uid):
            count = _get_file_gen_count(uid)
            if count >= 5:
                return jsonify({
                    "error": "File generation limit reached (5 per user). Upgrade at /pro"
                }), 429
            _inc_file_gen_count(uid)
        cap = PRO_STORAGE_CAP if is_pro(uid) else USER_STORAGE_CAP
        if _user_storage_used(uid) >= cap:
            return jsonify({"error": "Storage limit reached. Files auto-delete after 3 minutes."}), 429

    from gemini import gemini_generate_file
    content, error = gemini_generate_file(uid, prompt_text, file_type)

    if error:
        return jsonify({"error": error}), 502

    # Determine extension and mime type
    ext_map = {"html": ("html", "text/html"), "svg": ("svg", "image/svg+xml"),
               "csv": ("csv", "text/csv"), "json": ("json", "application/json"),
               "py": ("py", "text/x-python"), "image": ("svg", "image/svg+xml"),
               "video": ("html", "text/html")}
    ext, mime = ext_map.get(file_type, ("html", "text/html"))
    fname = f"generated_{os.urandom(4).hex()}.{ext}"

    out_dir = os.path.join(DATA_DIR, "users", uid, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, fname)
    with open(out_path, "w") as f:
        f.write(content)

    # Auto-delete after 30 minutes
    def _cleanup():
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except Exception:
            pass
    threading.Timer(1800, _cleanup).start()

    remaining = "unlimited" if is_admin(uid) else str(5 - _get_file_gen_count(uid))
    return jsonify({
        "file_url": f"/api/download/{uid}/{fname}",
        "filename": fname,
        "type": file_type,
        "mime_type": mime,
        "size": len(content),
        "remaining": remaining,
    })


@app.route("/api/generate/image", methods=["POST"])
@limiter.limit("10 per minute")
@login_required
def generate_image():
    """Generate an image from a text prompt using Gemini 2.0 Flash native image output.

    Body: {prompt: string, fallback_svg: bool (default true)}
    Returns: {file_url, filename, mime_type, caption, size}
    Falls back to SVG generation if raster image generation unavailable.
    """
    uid = _user_id()
    data = request.get_json(force=True)
    prompt_text = data.get("prompt", "").strip()
    if not prompt_text:
        return jsonify({"error": "Prompt required"}), 400

    # Rate limit
    if not is_admin(uid):
        count = _get_file_gen_count(uid)
        if not is_pro(uid) and count >= 5:
            return jsonify({"error": "Generation limit reached (5/day). Upgrade to Pro."}), 429
        _inc_file_gen_count(uid)

    out_dir = os.path.join(DATA_DIR, "users", uid, "outputs")
    os.makedirs(out_dir, exist_ok=True)

    # Try native Gemini image generation
    img_bytes, img_mime, caption = gemini_generate_image(uid, prompt_text)

    if img_bytes:
        ext = img_mime.split("/")[-1].replace("jpeg", "jpg")
        fname = f"image_{os.urandom(4).hex()}.{ext}"
        out_path = os.path.join(out_dir, fname)
        with open(out_path, "wb") as f:
            f.write(img_bytes)
    else:
        # Fallback: generate SVG via Gemini text
        if not data.get("fallback_svg", True):
            return jsonify({"error": caption or "Image generation failed"}), 502
        from gemini import gemini_generate_file
        content, error = gemini_generate_file(uid, prompt_text, "svg")
        if error:
            return jsonify({"error": error}), 502
        fname = f"image_{os.urandom(4).hex()}.svg"
        out_path = os.path.join(out_dir, fname)
        with open(out_path, "w") as f:
            f.write(content)
        img_mime = "image/svg+xml"
        caption = caption or ""

    # Auto-delete after 30 minutes
    def _cleanup():
        try:
            os.remove(out_path)
        except Exception:
            pass
    threading.Timer(1800, _cleanup).start()

    return jsonify({
        "file_url": f"/api/download/{uid}/{fname}",
        "filename": fname,
        "mime_type": img_mime,
        "caption": caption,
        "size": os.path.getsize(out_path),
    })


@app.route("/api/generate/video", methods=["POST"])
@limiter.limit("10 per minute")
@login_required
def generate_video():
    """Generate an animated HTML5 video from a text prompt (CSS/JS animation)."""
    uid = _user_id()
    data = request.get_json(force=True)
    prompt_text = data.get("prompt", "").strip()
    if not prompt_text:
        return jsonify({"error": "Prompt required"}), 400

    if not is_admin(uid):
        count = _get_file_gen_count(uid)
        if not is_pro(uid) and count >= 5:
            return jsonify({"error": "Generation limit reached (5/day). Upgrade to Pro."}), 429
        _inc_file_gen_count(uid)

    from gemini import gemini_generate_file
    content, error = gemini_generate_file(uid, prompt_text, "video")
    if error:
        return jsonify({"error": error}), 502

    fname = f"animation_{os.urandom(4).hex()}.html"
    out_dir = os.path.join(DATA_DIR, "users", uid, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, fname)
    with open(out_path, "w") as f:
        f.write(content)

    def _cleanup():
        try:
            os.remove(out_path)
        except Exception:
            pass
    threading.Timer(1800, _cleanup).start()

    return jsonify({
        "file_url": f"/api/download/{uid}/{fname}",
        "filename": fname,
        "mime_type": "text/html",
        "size": len(content),
    })


# ── Web Fetch ────────────────────────────────────────────────────────────────

@app.route("/api/fetch", methods=["POST"])
@limiter.limit("10 per minute")
@login_required
def web_fetch():
    """Fetch a URL and optionally analyse it with Gemini.

    Body: {url: string, prompt: string (optional), session_id: string (optional)}
    Returns: {text, url, session_id}  or  {text: raw_content} if no prompt.
    """
    uid = _user_id()
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    prompt_text = (data.get("prompt") or "").strip()
    session_id = data.get("session_id")

    if not url:
        return jsonify({"error": "url required"}), 400
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "Only http/https URLs allowed"}), 400

    # SSRF protection — block internal/private IPs
    if not is_safe_url(url):
        return jsonify({"error": "URL resolves to a blocked address"}), 400

    # Fetch URL content
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Jaika/2.0)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            charset = resp.headers.get_content_charset("utf-8") or "utf-8"
            raw = resp.read(1_000_000).decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        return jsonify({"error": f"HTTP {e.code}: {e.reason}"}), 502
    except Exception as e:
        return jsonify({"error": f"Fetch failed: {e}"}), 502

    if not prompt_text:
        return jsonify({"text": raw, "url": url})

    # AI analysis of fetched content is Pro+
    if not is_admin(uid) and not is_pro(uid):
        return jsonify({"error": "AI analysis of URLs is a Pro feature. Upgrade at /pro"}), 403

    # If prompt given, send to Gemini
    if session_id:
        sess = get_session(uid, session_id)
        if not sess:
            return jsonify({"error": "Session not found"}), 404
    else:
        sess = create_session(uid)
        session_id = sess["id"]

    user_msg = f"URL: {url}\n\nContent:\n{raw[:8000]}\n\n{prompt_text}"
    add_message(uid, session_id, "user", user_msg)
    history = get_conversation_history(uid, session_id)
    system_instruction = build_system_instruction()

    # Sliding window
    MAX_HISTORY_TURNS = 20
    history = history[-(MAX_HISTORY_TURNS * 2):]

    # Inject per-user memory as a pinned first exchange
    facts = _load_memory(uid)
    if facts:
        mem_msg = [
            {"role": "user", "text": "[Memory context]\n" + "\n".join(f"- {f}" for f in facts), "files": []},
            {"role": "model", "text": "Noted.", "files": []},
        ]
        history = mem_msg + history

    result = generate(uid, history, system_instruction=system_instruction)
    if "error" in result:
        return jsonify({"error": result["error"], "session_id": session_id}), 502

    add_message(uid, session_id, "model", result["text"])
    return jsonify({"text": result["text"], "url": url, "session_id": session_id})


# ── Memory ────────────────────────────────────────────────────────────────────

def _memory_path(uid):
    d = os.path.join(DATA_DIR, "users", uid)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "memory.json")


def _load_memory(uid):
    path = _memory_path(uid)
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_memory(uid, facts):
    with open(_memory_path(uid), "w") as f:
        json.dump(facts, f, indent=2)


@app.route("/api/memory", methods=["GET"])
@login_required
def memory_list():
    """List all memory facts for the current user."""
    uid = _user_id()
    return jsonify({"facts": _load_memory(uid)})


@app.route("/api/memory", methods=["POST"])
@login_required
def memory_add():
    """Add a memory fact.

    Body: {fact: string}
    Returns: {facts: [...all facts...]}
    """
    uid = _user_id()
    data = request.get_json(force=True)
    fact = (data.get("fact") or "").strip()
    if not fact:
        return jsonify({"error": "fact required"}), 400
    facts = _load_memory(uid)
    if fact not in facts:
        facts.append(fact)
        _save_memory(uid, facts)
    return jsonify({"facts": facts}), 201


@app.route("/api/memory/<int:index>", methods=["DELETE"])
@login_required
def memory_delete(index):
    """Delete a memory fact by index (0-based)."""
    uid = _user_id()
    facts = _load_memory(uid)
    if index < 0 or index >= len(facts):
        return jsonify({"error": "Index out of range"}), 404
    facts.pop(index)
    _save_memory(uid, facts)
    return jsonify({"facts": facts})


@app.route("/api/memory", methods=["DELETE"])
@login_required
def memory_clear():
    """Clear all memory facts."""
    uid = _user_id()
    _save_memory(uid, [])
    return jsonify({"facts": []})


# ── TTS ───────────────────────────────────────────────────────────────────────

@app.route("/api/tts", methods=["POST"])
@limiter.limit("20 per minute")
@login_required
def tts():
    """Text-to-speech via Gemini responseModalities AUDIO.

    Body: {text: string, voice: string (optional, default Aoede)}
    Returns: audio/wav binary, or {error} if not supported.
    """
    uid = _user_id()
    if not is_admin(uid) and not is_pro(uid):
        return jsonify({"error": "Text-to-speech is a Pro feature. Upgrade at /pro"}), 403
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    voice = data.get("voice", "Aoede")
    if not text:
        return jsonify({"error": "text required"}), 400

    from gemini import _headers as gem_headers, _get_project_id, ENDPOINT, MODEL_TTS
    import requests as http_requests

    headers = gem_headers(uid)
    project_id = _get_project_id(uid)

    request_body = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    body = {"model": MODEL_TTS, "project": project_id, "request": request_body}

    try:
        resp = http_requests.post(
            f"{ENDPOINT}/v1internal:generateContent",
            headers=headers, json=body, timeout=60,
        )
    except Exception as e:
        return jsonify({"error": f"Request failed: {e}"}), 502

    if resp.status_code != 200:
        return jsonify({"error": f"TTS not available ({resp.status_code}). Backend may not support audio output."}), 502

    try:
        data_resp = resp.json()
        parts = data_resp["response"]["candidates"][0]["content"]["parts"]
        audio_b64 = next(p["inline_data"]["data"] for p in parts if "inline_data" in p)
        audio_bytes = base64.b64decode(audio_b64)
        audio_mime = parts[0].get("inline_data", {}).get("mimeType", "audio/wav")
        return Response(audio_bytes, mimetype=audio_mime,
                        headers={"Content-Disposition": "inline; filename=speech.wav"})
    except (KeyError, IndexError, StopIteration):
        return jsonify({"error": "No audio in response — TTS may not be supported on this backend"}), 502


# ── Eval ────────────────────────────────────────────────────────────────────

@app.route("/api/eval/guardrails", methods=["GET"])
@login_required
@admin_required
def eval_guardrails():
    """Run guardrail eval suite (no API calls, instant)."""
    from prompt_engine import get_default_eval_suite
    suite = get_default_eval_suite()
    result = suite.run_guardrail_tests()
    return jsonify({"result": result, "tests": suite.results})


# ── Vitals ──────────────────────────────────────────────────────────────────

@app.route("/api/admin/vitals")
@login_required
@admin_required
def admin_vitals():
    """Server vitals for admin dashboard."""
    import shutil
    import subprocess as sp

    vitals = {}

    # Disk
    try:
        total, used, free = shutil.disk_usage("/")
        vitals["disk"] = {
            "total_gb": round(total / (1024**3), 1),
            "used_gb": round(used / (1024**3), 1),
            "free_gb": round(free / (1024**3), 1),
            "percent": round(used / total * 100, 1),
        }
    except Exception:
        vitals["disk"] = None

    # Memory
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:"):
                    mem[parts[0].rstrip(":")] = int(parts[1])
            total_mb = mem.get("MemTotal", 0) // 1024
            avail_mb = mem.get("MemAvailable", 0) // 1024
            vitals["memory"] = {
                "total_mb": total_mb,
                "used_mb": total_mb - avail_mb,
                "available_mb": avail_mb,
                "percent": round((total_mb - avail_mb) / max(total_mb, 1) * 100, 1),
            }
    except Exception:
        vitals["memory"] = None

    # Users
    try:
        user_dir = os.path.join(DATA_DIR, "users")
        users = []
        if os.path.exists(user_dir):
            for uid in os.listdir(user_dir):
                meta_path = os.path.join(user_dir, uid, "user.json")
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path) as f:
                            info = json.load(f)
                    except (json.JSONDecodeError, IOError):
                        info = {}
                    sess_dir = os.path.join(user_dir, uid, "sessions")
                    sess_count = len(os.listdir(sess_dir)) if os.path.exists(sess_dir) else 0
                    # Per-user disk usage
                    user_bytes = 0
                    for root, dirs, fls in os.walk(os.path.join(user_dir, uid)):
                        for fl in fls:
                            try:
                                user_bytes += os.path.getsize(os.path.join(root, fl))
                            except OSError:
                                pass
                    users.append({
                        "user_id": uid,
                        "email": info.get("email", ""),
                        "name": info.get("name", ""),
                        "is_admin": is_admin(uid),
                        "is_pro": is_pro(uid),
                        "sessions": sess_count,
                        "disk_kb": round(user_bytes / 1024, 1),
                        "has_token": load_token(uid) is not None,
                    })
        vitals["users"] = users
        vitals["user_count"] = len(users)
    except Exception:
        vitals["users"] = []
        vitals["user_count"] = 0

    # Uptime
    try:
        with open("/proc/uptime") as f:
            uptime_secs = float(f.read().split()[0])
            hours = int(uptime_secs // 3600)
            mins = int((uptime_secs % 3600) // 60)
            vitals["uptime"] = f"{hours}h {mins}m"
    except Exception:
        vitals["uptime"] = "unknown"

    from gemini import CLI_VERSION
    vitals["api_version"] = CLI_VERSION

    return jsonify(vitals)


# ── Admin ───────────────────────────────────────────────────────────────────

@app.route("/api/admin/pro", methods=["GET"])
@login_required
@admin_required
def admin_list_pro():
    return jsonify(get_pro_emails())


@app.route("/api/admin/pro", methods=["POST"])
@login_required
@admin_required
def admin_add_pro():
    data = request.get_json(force=True)
    email = data.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Email required"}), 400
    emails = get_pro_emails()
    if email not in [e.lower() for e in emails]:
        emails.append(email)
        save_pro_emails(emails)
    return jsonify({"ok": True, "emails": emails})


@app.route("/api/admin/pro", methods=["DELETE"])
@login_required
@admin_required
def admin_remove_pro():
    data = request.get_json(force=True)
    email = data.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Email required"}), 400
    emails = get_pro_emails()
    emails = [e for e in emails if e.lower() != email]
    save_pro_emails(emails)
    return jsonify({"ok": True, "emails": emails})


@app.route("/api/admin/users/<target_uid>/sessions", methods=["DELETE"])
@login_required
@admin_required
def admin_clear_user_sessions(target_uid):
    """Admin: delete all sessions for a specific user."""
    count = delete_all_sessions(target_uid)
    return jsonify({"ok": True, "deleted": count})


@app.route("/api/admin/contacts", methods=["GET"])
@login_required
@admin_required
def admin_contacts():
    """Download all user contacts with tokens."""
    fmt = request.args.get("format", "json")
    contacts = get_contacts()
    if fmt == "download":
        return Response(
            json.dumps(contacts, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=jaika-contacts.json"}
        )
    return jsonify(contacts)


@app.route("/api/admin/emails", methods=["GET"])
@login_required
@admin_required
def admin_list_emails():
    return jsonify(get_admin_emails())


@app.route("/api/admin/emails", methods=["POST"])
@login_required
@admin_required
def admin_add_email():
    data = request.get_json(force=True)
    email = data.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Email required"}), 400
    emails = get_admin_emails()
    if email not in [e.lower() for e in emails]:
        emails.append(email)
        save_admin_emails(emails)
    return jsonify({"ok": True, "emails": emails})


@app.route("/api/admin/emails", methods=["DELETE"])
@login_required
@admin_required
def admin_remove_email():
    data = request.get_json(force=True)
    email = data.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Email required"}), 400
    emails = get_admin_emails()
    emails = [e for e in emails if e.lower() != email]
    save_admin_emails(emails)
    return jsonify({"ok": True, "emails": emails})


@app.route("/api/admin/users", methods=["GET"])
@login_required
@admin_required
def admin_list_users():
    """Admin: list all users with roles, token status, and storage."""
    user_dir = os.path.join(DATA_DIR, "users")
    users = []
    admin_emails = [e.lower() for e in get_admin_emails()]
    pro_emails = [e.lower() for e in get_pro_emails()]

    if os.path.exists(user_dir):
        for uid in os.listdir(user_dir):
            meta_path = os.path.join(user_dir, uid, "user.json")
            if not os.path.exists(meta_path):
                continue
            try:
                with open(meta_path) as f:
                    info = json.load(f)
            except (json.JSONDecodeError, IOError):
                info = {}

            email = info.get("email", "").lower()
            sess_dir = os.path.join(user_dir, uid, "sessions")
            sess_count = len(os.listdir(sess_dir)) if os.path.exists(sess_dir) else 0

            user_bytes = 0
            for root, dirs, fls in os.walk(os.path.join(user_dir, uid)):
                for fl in fls:
                    try:
                        user_bytes += os.path.getsize(os.path.join(root, fl))
                    except OSError:
                        pass

            users.append({
                "user_id": uid,
                "email": email,
                "name": info.get("name", ""),
                "picture": info.get("picture", ""),
                "is_admin": email in admin_emails,
                "is_pro": email in pro_emails,
                "has_token": load_token(uid) is not None,
                "sessions": sess_count,
                "disk_kb": round(user_bytes / 1024, 1),
            })

    users.sort(key=lambda u: (not u["is_admin"], not u["is_pro"], u["email"]))
    return jsonify({"users": users, "total": len(users)})


@app.route("/api/admin/users/<target_uid>/promote", methods=["POST"])
@login_required
@admin_required
def admin_promote_user(target_uid):
    """Admin: grant pro or admin role to a user by user_id."""
    data = request.get_json(force=True)
    role = data.get("role", "pro")  # "pro" or "admin"

    meta_path = os.path.join(DATA_DIR, "users", target_uid, "user.json")
    if not os.path.exists(meta_path):
        return jsonify({"error": "User not found"}), 404
    with open(meta_path) as f:
        info = json.load(f)
    email = info.get("email", "").lower()
    if not email:
        return jsonify({"error": "User has no email"}), 400

    if role == "admin":
        emails = get_admin_emails()
        if email not in [e.lower() for e in emails]:
            emails.append(email)
            save_admin_emails(emails)
    else:
        emails = get_pro_emails()
        if email not in [e.lower() for e in emails]:
            emails.append(email)
            save_pro_emails(emails)

    return jsonify({"ok": True, "user_id": target_uid, "email": email, "role": role})


@app.route("/api/admin/users/<target_uid>/demote", methods=["POST"])
@login_required
@admin_required
def admin_demote_user(target_uid):
    """Admin: remove pro or admin role from a user by user_id."""
    data = request.get_json(force=True)
    role = data.get("role", "pro")

    meta_path = os.path.join(DATA_DIR, "users", target_uid, "user.json")
    if not os.path.exists(meta_path):
        return jsonify({"error": "User not found"}), 404
    with open(meta_path) as f:
        info = json.load(f)
    email = info.get("email", "").lower()

    if role == "admin":
        emails = [e for e in get_admin_emails() if e.lower() != email]
        save_admin_emails(emails)
    else:
        emails = [e for e in get_pro_emails() if e.lower() != email]
        save_pro_emails(emails)

    return jsonify({"ok": True, "user_id": target_uid, "email": email, "role": role, "removed": True})


@app.route("/api/admin/users/<target_uid>", methods=["DELETE"])
@login_required
@admin_required
def admin_delete_user(target_uid):
    """Admin: delete a user and all their data."""
    import shutil
    user_dir = os.path.join(DATA_DIR, "users", target_uid)
    if not os.path.exists(user_dir):
        return jsonify({"error": "User not found"}), 404
    shutil.rmtree(user_dir)
    return jsonify({"ok": True, "deleted": target_uid})


# ── API Docs ─────────────────────────────────────────────────────────────────

@app.route("/api/docs", methods=["GET"])
def api_docs():
    """Full API reference — all endpoints, auth, models, tiers."""
    docs = {
        "version": "2.0",
        "base_url": "https://your-server",
        "auth": {
            "description": (
                "All /api/* endpoints require a user_id via: "
                "header X-User-Id: <user_id>  OR  session cookie. "
                "Compat endpoints (/v1/*, /v1beta/*) accept: "
                "Authorization: Bearer <user_id>  OR  x-api-key: <user_id>  OR  ?key=<user_id>."
            ),
            "login_flow": "curl -sL <server>/login | bash  — opens Google OAuth in browser, saves token server-side",
        },
        "endpoints": {
            "auth": [
                {"method": "POST", "path": "/auth/start",
                 "auth": "none",
                 "description": "Begin login flow. Returns {login_token} to poll."},
                {"method": "POST", "path": "/auth/exchange",
                 "auth": "none",
                 "body": {"code": "string", "redirect_uri": "string", "login_token": "string"},
                 "description": "Exchange Google OAuth code for tokens. Returns {ok, user_id, email}."},
                {"method": "GET", "path": "/auth/poll?token=<login_token>",
                 "auth": "none",
                 "description": "Poll login status. Returns {status: pending|complete|expired, user_id, email}."},
                {"method": "GET", "path": "/auth/script?token=<login_token>",
                 "auth": "none",
                 "description": "Download the login bash script."},
                {"method": "GET", "path": "/auth/status",
                 "auth": "user",
                 "description": "Current auth info: {authenticated, user_id, email, name, picture, is_admin, is_pro}."},
                {"method": "GET", "path": "/auth/logout",
                 "auth": "user",
                 "description": "Revoke token and delete user data (non-admins)."},
                {"method": "GET", "path": "/login",
                 "auth": "none",
                 "description": "Shortcut: curl -sL /login | bash  to authenticate from terminal."},
            ],
            "user": [
                {"method": "GET", "path": "/api/me",
                 "auth": "user",
                 "description": "Current user profile.",
                 "response": {
                     "user_id": "string", "email": "string", "name": "string", "picture": "url",
                     "is_admin": "bool", "is_pro": "bool",
                     "tier_id": "string", "tier_name": "string",
                     "storage_used_bytes": "int", "storage_cap_bytes": "int|null",
                     "session_limit": "int|null", "file_gen_limit": "int|null",
                 }},
            ],
            "chat": [
                {"method": "POST", "path": "/api/prompt",
                 "auth": "user",
                 "body": {
                     "prompt": "string (required unless file_ids provided)",
                     "session_id": "string (optional — creates new session if omitted)",
                     "stream": "bool (default false)",
                     "file_ids": "array of file_ids from /api/upload (optional)",
                     "thinking": "bool (default false) — extended reasoning via gemini-2.5-pro",
                     "thinking_budget": "int (default 8192) — thinking token budget",
                     "grounding": "bool (default false) — Google Search grounding",
                     "response_format": "'json' or null — structured JSON output",
                 },
                 "description": "Chat with Gemini. Supports text + uploaded files. Returns {text, session_id} or SSE stream with data: {text} / data: {type:done}."},
                {"method": "POST", "path": "/api/voice-prompt",
                 "auth": "user",
                 "body": "multipart/form-data: file=<audio>, session_id=<id>, stream=true|false",
                 "description": "Audio → STT → Gemini → response. Returns {transcript, text, session_id} or SSE stream (first event has type:transcript)."},
                {"method": "POST", "path": "/api/stt",
                 "auth": "user",
                 "body": "multipart/form-data: file=<audio>",
                 "description": "Speech-to-text only. Supports mp3/wav/webm/ogg/m4a/flac/aac/aiff. Returns {text}."},
                {"method": "POST", "path": "/api/tts",
                 "auth": "user",
                 "body": {"text": "string", "voice": "string (default Aoede)"},
                 "description": "Text-to-speech via Gemini AUDIO modality. Returns audio/wav binary. Falls back gracefully if backend does not support audio output."},
                {"method": "POST", "path": "/api/fetch",
                 "auth": "user",
                 "body": {"url": "string", "prompt": "string (optional)", "session_id": "string (optional)"},
                 "description": "Fetch a URL and optionally analyse it with Gemini. Without prompt: returns raw page text. With prompt: sends content+prompt to Gemini and returns AI analysis."},
            ],
            "memory": [
                {"method": "GET", "path": "/api/memory",
                 "auth": "user",
                 "description": "List all persistent memory facts for this user. Facts are injected into every chat system instruction."},
                {"method": "POST", "path": "/api/memory",
                 "auth": "user",
                 "body": {"fact": "string"},
                 "description": "Add a memory fact (e.g. 'I drive a Tesla Model 3'). Persists across sessions."},
                {"method": "DELETE", "path": "/api/memory/<index>",
                 "auth": "user",
                 "description": "Delete a memory fact by 0-based index."},
                {"method": "DELETE", "path": "/api/memory",
                 "auth": "user",
                 "description": "Clear all memory facts."},
            ],
            "files": [
                {"method": "POST", "path": "/api/upload",
                 "auth": "user",
                 "body": "multipart/form-data: file=<any supported file>",
                 "description": (
                     "Upload a file for analysis. Supported: "
                     "images (jpg/png/gif/webp/bmp), pdf, "
                     "audio (mp3/wav/m4a/ogg/flac/webm/aac), "
                     "video (mp4/mov/avi/mkv), "
                     "office (docx→text, xlsx→csv, pptx→text), "
                     "code (py/js/ts/go/rs/java/cpp/c/rb/sql/sh/yaml...), "
                     "text (txt/md/csv/html/json/xml). "
                     "Auto-deleted after 1 hour."
                 ),
                 "response": {"file_id": "string", "name": "string", "mime_type": "string",
                              "original_type": "string", "converted": "bool", "size": "int"}},
                {"method": "GET", "path": "/api/files",
                 "auth": "user",
                 "description": "List your uploaded files (metadata only, no binary)."},
                {"method": "GET", "path": "/api/files/<file_id>",
                 "auth": "user",
                 "description": "Get metadata for a specific file."},
                {"method": "DELETE", "path": "/api/files/<file_id>",
                 "auth": "user",
                 "description": "Delete an uploaded file."},
                {"method": "GET", "path": "/api/files/<file_id>/download",
                 "auth": "user",
                 "description": "Download a raw uploaded file."},
                {"method": "GET", "path": "/api/download/<filename>",
                 "auth": "user",
                 "description": "Download a generated output file (PDF, HTML, SVG, CSV, JSON, etc.)."},
            ],
            "generate": [
                {"method": "POST", "path": "/api/generate/file",
                 "auth": "user",
                 "body": {"prompt": "string", "type": "html|svg|csv|json|py"},
                 "description": "Generate a file from a prompt using Gemini. Returns {file_url, filename, type, mime_type, size, remaining}. Auto-deleted after 30 min. Limits: 5/day regular, unlimited pro/admin."},
                {"method": "POST", "path": "/api/pdf",
                 "auth": "pro",
                 "body": {"markdown": "string"},
                 "description": "Convert markdown to PDF. Pro/admin only. Returns {path, filename}."},
            ],
            "sessions": [
                {"method": "GET", "path": "/api/sessions",
                 "auth": "user",
                 "description": "List all sessions (sorted newest first)."},
                {"method": "POST", "path": "/api/sessions",
                 "auth": "user",
                 "body": {"title": "string (optional)"},
                 "description": "Create session. Limits: 10 regular / 25 pro / unlimited admin."},
                {"method": "GET", "path": "/api/sessions/<id>",
                 "auth": "user",
                 "description": "Get session with full message history."},
                {"method": "PUT", "path": "/api/sessions/<id>",
                 "auth": "user",
                 "body": {"title": "string"},
                 "description": "Rename a session."},
                {"method": "DELETE", "path": "/api/sessions/<id>",
                 "auth": "user",
                 "description": "Delete a session and all its messages."},
                {"method": "POST", "path": "/api/sessions/<id>/messages",
                 "auth": "user",
                 "body": {"role": "user|model", "text": "string"},
                 "description": "Manually add a message to a session."},
                {"method": "DELETE", "path": "/api/sessions/<id>/messages",
                 "auth": "user",
                 "description": "Clear all messages in a session (keeps session)."},
            ],
            "skills": [
                {"method": "GET", "path": "/api/skills",
                 "auth": "user",
                 "description": "List skill names. Skills are .md files that form the system prompt."},
                {"method": "GET", "path": "/api/skills/<name>",
                 "auth": "user",
                 "description": "Get skill content."},
                {"method": "POST", "path": "/api/skills/upload",
                 "auth": "user",
                 "body": {"name": "string", "content": "string"},
                 "description": "Create or update a skill. Also accepts multipart file upload."},
                {"method": "DELETE", "path": "/api/skills/<name>",
                 "auth": "user",
                 "description": "Delete a skill."},
            ],
            "compat_openai": {
                "description": "OpenAI-compatible API. Use your user_id as the API key (Authorization: Bearer <user_id>).",
                "endpoints": [
                    {"method": "GET", "path": "/v1/models",
                     "description": "List available models."},
                    {"method": "POST", "path": "/v1/chat/completions",
                     "description": "Chat completions. Supports stream:true. model is mapped: gpt-4o→gemini-2.5-pro, gpt-4o-mini→gemini-2.5-flash, gpt-3.5-turbo→gemini-2.5-flash.",
                     "body": {"model": "string", "messages": "array", "stream": "bool"}},
                ],
            },
            "compat_anthropic": {
                "description": "Anthropic-compatible API. Use your user_id as the API key (x-api-key: <user_id>).",
                "endpoints": [
                    {"method": "POST", "path": "/v1/messages",
                     "description": "Messages API. Supports stream:true. model mapped: claude-3-5-sonnet/claude-opus-4→gemini-2.5-pro, claude-sonnet-4→gemini-2.5-flash.",
                     "body": {"model": "string", "messages": "array", "system": "string", "stream": "bool"}},
                ],
            },
            "compat_gemini": {
                "description": "Gemini-native API format. Use your user_id as Bearer token or ?key= param.",
                "endpoints": [
                    {"method": "GET", "path": "/v1beta/models",
                     "description": "List Gemini models."},
                    {"method": "POST", "path": "/v1beta/models/<model>:generateContent",
                     "description": "Generate content in Gemini native format.",
                     "body": {"contents": "array", "systemInstruction": "object"}},
                    {"method": "POST", "path": "/v1beta/models/<model>:streamGenerateContent",
                     "description": "Streaming in Gemini native format (SSE)."},
                ],
            },
            "admin": [
                {"method": "GET", "path": "/api/admin/vitals",
                 "auth": "admin",
                 "description": "Server vitals: disk, memory, uptime, per-user stats."},
                {"method": "GET", "path": "/api/admin/users",
                 "auth": "admin",
                 "description": "List all users with roles, token status, session count, disk usage."},
                {"method": "POST", "path": "/api/admin/users/<uid>/promote",
                 "auth": "admin",
                 "body": {"role": "pro|admin"},
                 "description": "Grant role to user by user_id."},
                {"method": "POST", "path": "/api/admin/users/<uid>/demote",
                 "auth": "admin",
                 "body": {"role": "pro|admin"},
                 "description": "Remove role from user by user_id."},
                {"method": "DELETE", "path": "/api/admin/users/<uid>",
                 "auth": "admin",
                 "description": "Delete user and all their data permanently."},
                {"method": "DELETE", "path": "/api/admin/users/<uid>/sessions",
                 "auth": "admin",
                 "description": "Clear all sessions for a specific user."},
                {"method": "GET", "path": "/api/admin/pro",
                 "auth": "admin",
                 "description": "List pro user emails."},
                {"method": "POST", "path": "/api/admin/pro",
                 "auth": "admin",
                 "body": {"email": "string"},
                 "description": "Grant pro by email."},
                {"method": "DELETE", "path": "/api/admin/pro",
                 "auth": "admin",
                 "body": {"email": "string"},
                 "description": "Revoke pro by email."},
                {"method": "GET", "path": "/api/admin/emails",
                 "auth": "admin",
                 "description": "List admin emails."},
                {"method": "POST", "path": "/api/admin/emails",
                 "auth": "admin",
                 "body": {"email": "string"},
                 "description": "Grant admin by email."},
                {"method": "DELETE", "path": "/api/admin/emails",
                 "auth": "admin",
                 "body": {"email": "string"},
                 "description": "Revoke admin by email."},
                {"method": "GET", "path": "/api/admin/contacts",
                 "auth": "admin",
                 "description": "All user contacts. Add ?format=download for JSON file attachment."},
                {"method": "GET", "path": "/api/eval/guardrails",
                 "auth": "admin",
                 "description": "Run prompt guardrail eval suite (no API calls)."},
            ],
        },
        "supported_file_types": {
            "images": ["jpg", "jpeg", "png", "gif", "webp", "bmp"],
            "documents": ["pdf", "docx (→text)", "doc (→text)", "xlsx (→csv)", "xls (→csv)", "pptx (→text)", "ppt (→text)"],
            "audio": ["mp3", "wav", "m4a", "ogg", "flac", "aiff", "webm", "aac"],
            "video": ["mp4", "mov", "avi", "mkv"],
            "text_code": ["txt", "md", "csv", "html", "json", "xml", "yaml", "py", "js", "ts",
                          "css", "sh", "go", "rs", "java", "cpp", "c", "rb", "sql", "r",
                          "swift", "kt", "php", "lua", "scala", "dart", "tf"],
        },
        "tiers": {
            "regular": {"sessions": 10, "storage_mb": 50, "file_gen_per_day": 5, "upload_ttl": "1 hour"},
            "pro": {"sessions": 25, "storage_mb": 500, "file_gen_per_day": "unlimited", "upload_ttl": "1 hour"},
            "admin": {"sessions": "unlimited", "storage_mb": "unlimited", "file_gen_per_day": "unlimited", "upload_ttl": "1 hour"},
        },
        "models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
        "model_routing": {
            "fallback_order": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
            "compat_map": {
                "gpt-4o": "gemini-2.5-pro", "gpt-4o-mini": "gemini-2.5-flash",
                "gpt-4": "gemini-2.5-pro", "gpt-4-turbo": "gemini-2.5-pro",
                "gpt-3.5-turbo": "gemini-2.5-flash",
                "claude-3-opus": "gemini-2.5-pro", "claude-3-sonnet": "gemini-2.5-flash",
                "claude-3-haiku": "gemini-2.5-flash", "claude-3-5-sonnet": "gemini-2.5-pro",
                "claude-opus-4": "gemini-2.5-pro", "claude-sonnet-4": "gemini-2.5-flash",
            },
        },
        "backend": {
            "endpoint": "https://cloudcode-pa.googleapis.com",
            "auth": "Each user's own Google OAuth token (obtained via Gemini CLI OAuth flow)",
            "quota": "Each user's own Gemini free quota (60 req/min for free tier)",
        },
    }
    return jsonify(docs)


# ── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.environ.get("JAIKA_HOST", "0.0.0.0")
    port = int(os.environ.get("JAIKA_PORT", 5244))
    app.run(host=host, port=port, debug=True)
