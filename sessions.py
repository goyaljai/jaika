"""Session CRUD — JSON file storage, scoped per user."""

import fcntl
import json
import os
import time
import uuid


def _locked_update(path, updater_fn):
    """Exclusive read-modify-write on a JSON file, safe across threads and gunicorn workers.

    Uses a sibling .lock file so the lock is held for the entire read-modify-write cycle.
    Writes atomically via a .tmp file + os.replace so readers never see a partial write.
    """
    lock_path = path + ".lock"
    with open(lock_path, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            with open(path) as f:
                data = json.load(f)
            data = updater_fn(data)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
            return data
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _data_dir():
    return os.environ.get("JAIKA_DATA_DIR", "./data")


def _sessions_dir(user_id):
    d = os.path.join(_data_dir(), "users", user_id, "sessions")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_id(val):
    """Sanitize an ID to prevent path traversal."""
    return os.path.basename(val).replace("..", "").strip()


def _session_path(user_id, session_id):
    session_id = _safe_id(session_id)
    return os.path.join(_sessions_dir(user_id), f"{session_id}.json")


def list_sessions(user_id):
    """Return list of session summaries (id, title, created, message_count)."""
    d = _sessions_dir(user_id)
    sessions = []
    for fname in os.listdir(d):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(d, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            sessions.append({
                "id": data["id"],
                "title": data.get("title", "Untitled"),
                "created": data.get("created", 0),
                "updated": data.get("updated", 0),
                "message_count": len(data.get("messages", [])),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    sessions.sort(key=lambda s: s.get("updated", 0), reverse=True)
    return sessions


def get_session(user_id, session_id):
    """Return full session data or None."""
    path = _session_path(user_id, session_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def session_count(user_id):
    """Return number of sessions for a user."""
    d = _sessions_dir(user_id)
    return len([f for f in os.listdir(d) if f.endswith(".json")])


def enforce_session_limit(user_id, max_sessions):
    """Delete oldest sessions to stay within limit (FIFO)."""
    sessions = list_sessions(user_id)
    if len(sessions) <= max_sessions:
        return
    # Sessions are sorted by updated desc — delete from the end (oldest)
    to_delete = sessions[max_sessions:]
    for s in to_delete:
        delete_session(user_id, s["id"])


def delete_all_sessions(user_id):
    """Delete all sessions for a user."""
    d = _sessions_dir(user_id)
    count = 0
    for fname in os.listdir(d):
        if fname.endswith(".json"):
            try:
                os.remove(os.path.join(d, fname))
                count += 1
            except OSError:
                pass
    return count


def create_session(user_id, title=None):
    """Create a new session, return its data."""
    session_id = str(uuid.uuid4())[:8]
    now = time.time()
    data = {
        "id": session_id,
        "title": title or "New Chat",
        "created": now,
        "updated": now,
        "messages": [],
    }
    path = _session_path(user_id, session_id)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    return data


def update_session(user_id, session_id, title=None):
    """Rename a session."""
    path = _session_path(user_id, session_id)
    if not os.path.exists(path):
        return None

    def _update(data):
        if title is not None:
            data["title"] = title
        data["updated"] = time.time()
        return data

    try:
        return _locked_update(path, _update)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def delete_session(user_id, session_id):
    """Delete a session file."""
    path = _session_path(user_id, session_id)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def add_message(user_id, session_id, role, text, files=None):
    """Append a message to a session (locked read-modify-write)."""
    path = _session_path(user_id, session_id)
    if not os.path.exists(path):
        return None

    msg = {
        "role": role,
        "text": text,
        "timestamp": time.time(),
    }
    if files:
        msg["files"] = [{"name": f.get("name", "file"), "mime_type": f.get("mime_type", "")} for f in files]

    def _update(data):
        data["messages"].append(msg)
        data["updated"] = time.time()
        if data["title"] == "New Chat" and role == "user" and text:
            data["title"] = text[:60].strip()
        return data

    try:
        _locked_update(path, _update)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return msg


def clear_messages(user_id, session_id):
    """Clear all messages from a session (locked)."""
    path = _session_path(user_id, session_id)
    if not os.path.exists(path):
        return False

    def _update(data):
        data["messages"] = []
        data["updated"] = time.time()
        return data

    try:
        _locked_update(path, _update)
        return True
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def get_conversation_history(user_id, session_id):
    """Return messages formatted for the Gemini API contents array."""
    data = get_session(user_id, session_id)
    if data is None:
        return []
    return [
        {"role": m["role"], "text": m.get("text", ""), "files": m.get("files", [])}
        for m in data.get("messages", [])
    ]
