"""Skills management — .md files providing domain expertise."""

import os
import re
import time

# Cache for build_system_instruction() — 5 min TTL, invalidated on save/delete
_si_cache = {"text": None, "expires": 0}


def _skills_dir():
    d = os.path.join(os.environ.get("JAIKA_DATA_DIR", "./data"), "skills")
    os.makedirs(d, exist_ok=True)
    return d


def list_skills():
    """Return list of skill names (without .md extension)."""
    d = _skills_dir()
    return sorted(
        f[:-3] for f in os.listdir(d)
        if f.endswith(".md") and os.path.isfile(os.path.join(d, f))
    )


def _safe_name(name):
    """Validate skill name — only alphanumeric, hyphens, underscores."""
    name = os.path.basename(str(name)).replace("..", "").strip()
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return None
    return name


def get_skill(name):
    """Return skill content or None."""
    name = _safe_name(name)
    if not name:
        return None
    path = os.path.join(_skills_dir(), f"{name}.md")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def save_skill(name, content):
    """Save a skill .md file."""
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return False
    path = os.path.join(_skills_dir(), f"{name}.md")
    with open(path, "w") as f:
        f.write(content)
    _si_cache["expires"] = 0  # invalidate cache
    return True


def delete_skill(name):
    """Delete a skill file."""
    name = _safe_name(name)
    if not name:
        return False
    path = os.path.join(_skills_dir(), f"{name}.md")
    if os.path.exists(path):
        os.remove(path)
        _si_cache["expires"] = 0  # invalidate cache
        return True
    return False


def build_system_instruction():
    """Combine SYSTEM_PROMPT + all skills into a system instruction string.
    Cached for 5 minutes; invalidated on skill save/delete.
    """
    now = time.time()
    if _si_cache["text"] is not None and now < _si_cache["expires"]:
        return _si_cache["text"]

    from prompt_engine import SYSTEM_PROMPT

    # Always start with the core system prompt (identity, rules, etc.)
    sections = [SYSTEM_PROMPT.strip()]

    # Append skills
    d = _skills_dir()
    skill_parts = []
    for fname in sorted(os.listdir(d)):
        if not fname.endswith(".md") or not os.path.isfile(os.path.join(d, fname)):
            continue
        name = fname[:-3]
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
    _si_cache["text"] = result
    _si_cache["expires"] = now + 300  # 5 min TTL
    return result
