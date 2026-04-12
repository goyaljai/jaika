"""Skills management — per-user .md files that extend (or replace) the system prompt.

Each user has their own skills directory: data/users/{uid}/skills/
Skills are .md files that get appended to the system instruction as domain expertise.

Special skill: _persona
  If a user uploads a skill named "_persona", it REPLACES the default Jaika system
  prompt entirely for that user instead of appending to it. This lets any user turn
  Jaika into a custom persona chatbot without affecting other users.
"""

import os
import re
import time

# Per-user cache: { uid: {"text": str, "expires": float, "bust_mtime": float} }
# Cache is invalidated when the per-user bust file is newer than the cache entry.
# This makes cache invalidation cross-process — all workers see the same bust file.
_si_cache: dict = {}
_CACHE_TTL = 300  # 5 minutes


def _skills_dir(uid: str) -> str:
    d = os.path.join(os.environ.get("JAIKA_DATA_DIR", "./data"), "users", uid, "skills")
    os.makedirs(d, exist_ok=True)
    return d


def _bust_file(uid: str) -> str:
    """Path to the per-user cache-bust marker file."""
    return os.path.join(_skills_dir(uid), ".cache_bust")


def _bust_cache(uid: str) -> None:
    """Touch the bust file and clear this worker's in-memory cache for uid.
    All other workers will detect the newer bust file on their next request."""
    _si_cache.pop(uid, None)
    try:
        path = _bust_file(uid)
        with open(path, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def _bust_mtime(uid: str) -> float:
    """Return mtime of the bust file, or 0 if it doesn't exist."""
    try:
        return os.path.getmtime(_bust_file(uid))
    except OSError:
        return 0.0


def _safe_name(name: str):
    """Validate skill name — alphanumeric, hyphens, underscores, leading underscore allowed."""
    name = os.path.basename(str(name)).replace("..", "").strip()
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return None
    return name


def list_skills(uid: str) -> list:
    """Return list of skill names (without .md) for this user."""
    d = _skills_dir(uid)
    return sorted(
        f[:-3] for f in os.listdir(d)
        if f.endswith(".md") and os.path.isfile(os.path.join(d, f))
    )


def get_skill(uid: str, name: str):
    """Return skill content for this user, or None."""
    name = _safe_name(name)
    if not name:
        return None
    path = os.path.join(_skills_dir(uid), f"{name}.md")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def save_skill(uid: str, name: str, content: str) -> bool:
    """Save a skill .md file for this user."""
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return False
    path = os.path.join(_skills_dir(uid), f"{name}.md")
    with open(path, "w") as f:
        f.write(content)
    _bust_cache(uid)  # invalidate across all workers via bust file
    return True


def delete_skill(uid: str, name: str) -> bool:
    """Delete a skill file for this user."""
    name = _safe_name(name)
    if not name:
        return False
    path = os.path.join(_skills_dir(uid), f"{name}.md")
    if os.path.exists(path):
        os.remove(path)
        _bust_cache(uid)  # invalidate across all workers via bust file
        return True
    return False


def build_system_instruction(uid: str) -> str:
    """Build system instruction for this user.

    - If user has a '_persona' skill: use it as the entire system instruction
      (replaces the default Jaika identity — good for custom chatbot personas).
    - Otherwise: combine SYSTEM_PROMPT + all the user's skills as domain expertise.

    Result is cached per-user for 5 minutes.
    """
    now = time.time()
    cached = _si_cache.get(uid)
    # Cache is valid only if it's not expired AND the bust file hasn't been touched since
    # this worker built its cache (cross-worker invalidation via shared filesystem).
    if cached and now < cached["expires"] and _bust_mtime(uid) <= cached["bust_mtime"]:
        return cached["text"]

    from prompt_engine import SYSTEM_PROMPT

    d = _skills_dir(uid)

    # Check for _persona skill first — it overrides everything
    persona_path = os.path.join(d, "_persona.md")
    if os.path.exists(persona_path):
        try:
            with open(persona_path) as f:
                persona_content = f.read().strip()
            # Use the persona content as-is — the author defines all rules and tone.
            _si_cache[uid] = {"text": persona_content, "expires": now + _CACHE_TTL, "bust_mtime": _bust_mtime(uid)}
            return persona_content
        except IOError:
            pass

    # Normal path: Jaika system prompt + user's skill files
    sections = [SYSTEM_PROMPT.strip()]
    skill_parts = []
    for fname in sorted(os.listdir(d)):
        if not fname.endswith(".md") or not os.path.isfile(os.path.join(d, fname)):
            continue
        name = fname[:-3]
        if name == "_persona":  # already handled above
            continue
        try:
            with open(os.path.join(d, fname)) as f:
                content = f.read()
            if content:
                skill_parts.append(f"## Skill: {name}\n{content}")
        except IOError:
            continue

    if skill_parts:
        sections.append("You have the following domain expertise:\n\n" + "\n\n".join(skill_parts))

    result = "\n\n".join(sections)
    _si_cache[uid] = {"text": result, "expires": now + _CACHE_TTL, "bust_mtime": _bust_mtime(uid)}
    return result
