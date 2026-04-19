"""Gemini API — cloudcode-pa direct calls only (no CLI subprocess)."""

import base64
import json
import logging
import mimetypes
import os
import platform as _platform
import threading
import time

import requests as http_requests

from auth import get_access_token

log = logging.getLogger(__name__)

ENDPOINT = "https://cloudcode-pa.googleapis.com"
SERP_SEARCH_ENDPOINT = "https://serpapi.com/search.json"
GENAI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"

# Gemini API keys for features not supported by cloudcode-pa (TTS, Veo video).
# Keys are rotated round-robin on 429 / quota exhaustion.
_GEMINI_API_KEYS = [
    "YOUR_GEMINI_API_KEY_1",  # primary — chat + TTS + Veo
    "YOUR_GEMINI_API_KEY_2",  # fallback
    "YOUR_GEMINI_API_KEY_3",  # suspended — kept for rotation recovery
]
_key_index = 0
_key_lock = threading.Lock()


def _get_api_key():
    return _GEMINI_API_KEYS[_key_index % len(_GEMINI_API_KEYS)]


def _rotate_api_key():
    global _key_index
    with _key_lock:
        _key_index += 1
    log.warning("[GENAI KEY] Rotated to key index %d", _key_index % len(_GEMINI_API_KEYS))


def serp_search(query: str) -> dict:
    """Call SerpAPI Google AI Mode. Returns {markdown, sources} or empty dict on failure."""
    api_key = os.environ.get("SERP_API_KEY", "")
    if not api_key:
        log.warning("[SERP] SERP_API_KEY not set")
        return {}
    try:
        resp = http_requests.get(
            SERP_SEARCH_ENDPOINT,
            params={"q": query, "api_key": api_key, "engine": "google_ai_mode"},
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning("[SERP] search failed status=%s body=%s", resp.status_code, resp.text[:200])
            return {}
        data = resp.json()
        markdown = data.get("reconstructed_markdown", "")
        sources = [
            {"title": r.get("title", ""), "url": r.get("link", "")}
            for r in data.get("references", [])
        ]
        log.info("[SERP] query=%r markdown_len=%d sources=%d", query[:60], len(markdown), len(sources))
        return {"markdown": markdown, "sources": sources}
    except Exception as e:
        log.warning("[SERP] exception: %s", e)
        return {}


def _build_grounding_context(search_result: dict) -> str:
    """Format SerpAPI Google AI Mode result as context for the model."""
    if not search_result:
        return ""
    lines = ["Current web information (use this for an up-to-date answer):\n"]
    if search_result.get("markdown"):
        lines.append(search_result["markdown"])
    if search_result.get("sources"):
        lines.append("\nSources:")
        for s in search_result["sources"]:
            lines.append(f"- {s['title']}: {s['url']}")
    lines.append("\nUse the above to answer accurately. Cite sources where relevant.")
    return "\n".join(lines)
CLI_VERSION = "0.36.0"

# Defaults — used when no models.json exists yet
_DEFAULT_MODEL_CONFIG = {
    "fallback": [
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ],
    "thinking": "gemini-3-flash-preview",
    "tts": "gemini-2.5-flash",
    # GenAI API (generativelanguage.googleapis.com) model lists — tried in order
    "tts_models": ["gemini-2.5-flash-preview-tts"],
    "veo_models": ["veo-3.0-generate-preview", "veo-2.0-generate-001"],
}

# Keep module-level names for backward-compat (TTS handler in app.py imports MODEL_TTS)
MODEL_FALLBACK = _DEFAULT_MODEL_CONFIG["fallback"]
MODEL_THINKING  = _DEFAULT_MODEL_CONFIG["thinking"]
MODEL_TTS       = _DEFAULT_MODEL_CONFIG["tts"]  # gemini-2.5-flash supports audio modalities

# ── Dynamic model config (admin-editable) ────────────────────────────────────
_model_config_cache = None
_model_config_ts = 0.0
_MODEL_CONFIG_TTL = 60  # seconds
_model_config_lock = threading.Lock()


def _models_path():
    data_dir = os.environ.get("JAIKA_DATA_DIR", "./data")
    return os.path.join(data_dir, "models.json")


def get_model_config() -> dict:
    """Return current model config, reloading from disk if stale."""
    global _model_config_cache, _model_config_ts
    with _model_config_lock:
        now = time.time()
        if _model_config_cache is not None and (now - _model_config_ts) < _MODEL_CONFIG_TTL:
            return dict(_model_config_cache)
        path = _models_path()
        if os.path.exists(path):
            try:
                with open(path) as f:
                    loaded = json.load(f)
                # Merge with defaults so missing keys always have a value
                cfg = dict(_DEFAULT_MODEL_CONFIG)
                cfg.update(loaded)
                _model_config_cache = cfg
                _model_config_ts = now
                return dict(cfg)
            except (json.JSONDecodeError, IOError):
                pass
        _model_config_cache = dict(_DEFAULT_MODEL_CONFIG)
        _model_config_ts = now
        return dict(_DEFAULT_MODEL_CONFIG)


def save_model_config(config: dict):
    """Persist model config to disk and invalidate cache."""
    global _model_config_cache, _model_config_ts
    path = _models_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _model_config_lock:
        with open(path, "w") as f:
            json.dump(config, f, indent=2)
        _model_config_cache = dict(config)
        _model_config_ts = time.time()

# ── Retry config (ported from gemini-cli) ────────────────────────────────────
RETRY_MAX_ATTEMPTS = 3        # 1 initial + 2 retries (each retry = 1 API call against quota)
RETRY_INITIAL_DELAY = 5.0     # seconds (fallback if server doesn't specify)
MAX_RETRYABLE_DELAY = 300     # if server says wait > 5min, treat as terminal

# ── Per-user project ID + tier cache ─────────────────────────────────────────
# { user_id: {"project_id": str, "tier_id": str, "tier_name": str, "ts": float} }
_project_cache: dict = {}
_project_cache_lock = threading.Lock()
_PROJECT_CACHE_TTL = 3600  # 1 hour


def _get_client_metadata(project_id=None):
    return {
        "ideType": "IDE_UNSPECIFIED",
        "platform": _platform_str(),
        "pluginType": "GEMINI",
        "duetProject": project_id,
    }


def discover_project_and_tier(user_id) -> dict:
    """Call loadCodeAssist to get project_id + tier. Onboards if needed.
    Returns dict with keys: project_id, tier_id, tier_name.
    Raises on failure.
    """
    with _project_cache_lock:
        cached = _project_cache.get(user_id)
        if cached and (time.time() - cached["ts"]) < _PROJECT_CACHE_TTL:
            return cached

    token = get_access_token(user_id)
    if not token:
        raise PermissionError("No valid access token")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }

    # Step 1 — probe loadCodeAssist with no project to discover project_id
    # Retry once on 401: force-refresh the token and try again
    probe = {"metadata": _get_client_metadata()}
    resp = http_requests.post(
        f"{ENDPOINT}/v1internal:loadCodeAssist",
        headers=headers,
        data=json.dumps(probe),
        timeout=30,
    )
    if resp.status_code == 401:
        log.warning("[PROJECT] uid=%s 401 from loadCodeAssist — force-refreshing token", user_id)
        from auth import load_token, save_token
        _tok = load_token(user_id)
        if _tok:
            _tok["saved_at"] = 0
            save_token(user_id, _tok)
        token = get_access_token(user_id)
        if not token:
            raise PermissionError("Token refresh failed after loadCodeAssist 401")
        headers["Authorization"] = f"Bearer {token}"
        resp = http_requests.post(
            f"{ENDPOINT}/v1internal:loadCodeAssist",
            headers=headers,
            data=json.dumps(probe),
            timeout=30,
        )
    resp.raise_for_status()
    load_data = resp.json()

    project_id = load_data.get("cloudaicompanionProject")

    # Step 2 — pick tier
    tier = load_data.get("currentTier")
    if not tier:
        for t in load_data.get("allowedTiers", []):
            if t.get("isDefault"):
                tier = t
                break
    if not tier:
        tier = {"id": "legacy-tier", "name": "Legacy", "description": ""}

    tier_id = tier.get("id", "legacy-tier")
    tier_name = tier.get("name", tier_id)

    # Step 3 — onboard if not already onboarded (or no project yet)
    if not load_data.get("currentTier") or not project_id:
        onboard_payload = {
            "tierId": tier_id,
            "metadata": _get_client_metadata(project_id),
        }
        if project_id:
            onboard_payload["cloudaicompanionProject"] = project_id
        log.info("[ONBOARD] uid=%s tier=%s project=%s — calling onboardUser", user_id, tier_id, project_id or "auto-provision")
        ob_resp = http_requests.post(
            f"{ENDPOINT}/v1internal:onboardUser",
            headers=headers,
            data=json.dumps(onboard_payload),
            timeout=30,
        )
        if ob_resp.status_code == 200:
            for _ in range(6):
                ob_data = ob_resp.json()
                if ob_data.get("done"):
                    # Extract project from onboard response if we didn't have one
                    if not project_id:
                        project_id = ob_data.get("response", {}).get("cloudaicompanionProject") or ob_data.get("cloudaicompanionProject")
                    break
                time.sleep(5)
                ob_resp = http_requests.post(
                    f"{ENDPOINT}/v1internal:onboardUser",
                    headers=headers,
                    data=json.dumps(onboard_payload),
                    timeout=30,
                )
                ob_data = ob_resp.json()
                if ob_data.get("done"):
                    if not project_id:
                        project_id = ob_data.get("response", {}).get("cloudaicompanionProject") or ob_data.get("cloudaicompanionProject")
                    break

        # If onboarding succeeded but we still don't have a project, re-call loadCodeAssist
        if not project_id:
            log.info("[ONBOARD] uid=%s re-calling loadCodeAssist after onboard", user_id)
            resp2 = http_requests.post(
                f"{ENDPOINT}/v1internal:loadCodeAssist",
                headers=headers,
                data=json.dumps({"metadata": _get_client_metadata()}),
                timeout=30,
            )
            if resp2.status_code == 200:
                project_id = resp2.json().get("cloudaicompanionProject")

    if not project_id:
        raise ValueError(f"Failed to provision project for user {user_id}")

    # cloudaicompanionProject may be a string or an object {id, name, projectNumber}
    if isinstance(project_id, dict):
        project_id = project_id.get("id") or project_id.get("name")

    result = {"project_id": project_id, "tier_id": tier_id, "tier_name": tier_name, "ts": time.time()}
    with _project_cache_lock:
        _project_cache[user_id] = result

    log.info("User %s project=%s tier=%s", user_id, project_id, tier_name)
    return result


def get_user_tier(user_id) -> dict:
    """Public helper — returns tier info for a user. Used by /api/me etc."""
    try:
        info = discover_project_and_tier(user_id)
        return {"tier_id": info["tier_id"], "tier_name": info["tier_name"]}
    except Exception as e:
        log.warning("Could not get tier for %s: %s", user_id, e)
        return {"tier_id": "unknown", "tier_name": "Unknown"}


def _platform_str():
    sys = _platform.system().upper()
    machine = _platform.machine().lower()
    if sys == "DARWIN":
        return f"DARWIN_{'ARM64' if machine == 'arm64' else 'X86_64'}"
    if sys == "WINDOWS":
        return "WINDOWS_AMD64"
    return f"LINUX_{'ARM64' if machine in ('arm64', 'aarch64') else 'AMD64'}"


_USER_AGENT = f"gemini-cli/{CLI_VERSION} {_platform_str()}"


# ── Error classification (ported from gemini-cli googleQuotaErrors.ts) ───────

import random
import re


def _classify_error(resp):
    """Classify a 429/503 response into retryable vs terminal.
    Returns: ("retryable", delay_seconds) or ("terminal", reason)
    """
    if resp.status_code == 503:
        return ("retryable", RETRY_INITIAL_DELAY)

    try:
        data = resp.json()
    except Exception:
        return ("retryable", RETRY_INITIAL_DELAY)

    error = data.get("error", {})
    message = error.get("message", "")
    details = error.get("details", [])

    # Check for QUOTA_EXHAUSTED (terminal — daily limit)
    for d in details:
        reason = d.get("reason", "")
        if reason == "QUOTA_EXHAUSTED":
            return ("terminal", "Daily quota exhausted")
        # Check QuotaFailure for PerDay
        for v in d.get("violations", []):
            if "PerDay" in v.get("subject", ""):
                return ("terminal", "Daily quota exhausted")

    # Parse "retry after Xs" from message
    m = re.search(r"reset after (\d+)s", message)
    if m:
        delay = int(m.group(1))
        if delay > MAX_RETRYABLE_DELAY:
            return ("terminal", f"Retry delay too long ({delay}s)")
        return ("retryable", delay)

    # Parse RetryInfo from details
    for d in details:
        retry_delay = d.get("retryDelay", "")
        if retry_delay:
            secs = int(re.sub(r"[^\d]", "", retry_delay) or "0")
            if secs > MAX_RETRYABLE_DELAY:
                return ("terminal", f"Retry delay too long ({secs}s)")
            return ("retryable", max(secs, 1))

    # Default: retryable with initial delay
    return ("retryable", RETRY_INITIAL_DELAY)


def _retry_delay(attempt, server_delay):
    """Use exact server-provided delay + small buffer to avoid wasted retries.
    Each retry is a real API call that counts against quota, so we want
    to wait long enough that the next call succeeds on first try.
    """
    buffer = random.uniform(1.0, 3.0)  # 1-3s buffer after server's reset window
    return server_delay + buffer


def _headers(user_id):
    token = get_access_token(user_id)
    if not token:
        raise PermissionError("No valid access token")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }


def _refresh_headers(user_id):
    """Force a token refresh and return new headers. Used on 401 from the API.

    On 401, the access_token was stale/invalid. We:
    1. Reset saved_at=0 to force get_access_token to call Google's refresh endpoint
    2. Clear the per-user project ID cache (it may have been fetched with the bad token)
    3. Return new headers with the freshly issued access_token
    """
    from auth import load_token, save_token
    token = load_token(user_id)
    if not token:
        raise PermissionError("No token for user")
    # Reset saved_at to 0 — forces get_access_token to refresh unconditionally
    token["saved_at"] = 0
    save_token(user_id, token)
    # Clear project cache so _get_project_id re-fetches with the new token
    with _project_cache_lock:
        _project_cache.pop(user_id, None)
    # Refresh the access token
    new_access = get_access_token(user_id)
    if not new_access:
        raise PermissionError("Token refresh failed after 401")
    log.info("[AUTH] uid=%s token force-refreshed after 401", user_id)
    return {
        "Authorization": f"Bearer {new_access}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }


def _build_contents(messages, files=None):
    contents = []
    for msg in messages:
        parts = []
        if msg.get("text"):
            parts.append({"text": msg["text"]})
        for f in msg.get("files", []):
            if f.get("base64") and f.get("mime_type"):
                parts.append({
                    "inline_data": {
                        "mime_type": f["mime_type"],
                        "data": f["base64"],
                    }
                })
        if parts:
            contents.append({"role": msg["role"], "parts": parts})

    if files and contents:
        last = contents[-1]
        for f in files:
            if f.get("base64") and f.get("mime_type"):
                last["parts"].append({
                    "inline_data": {
                        "mime_type": f["mime_type"],
                        "data": f["base64"],
                    }
                })
    return contents


def _get_quota_donor_next(original_uid, tried_set):
    """Find the next user with a valid access token, skipping already-tried ones."""
    data_dir = os.environ.get("JAIKA_DATA_DIR", "./data")
    users_dir = os.path.join(data_dir, "users")
    if not os.path.isdir(users_dir):
        return None
    for uid in os.listdir(users_dir):
        if uid.startswith(".") or uid.startswith("bot_") or uid in ("test", "fake"):
            continue
        if uid in tried_set:
            continue
        if get_access_token(uid):
            return uid
    return None


def _get_project_id(user_id):
    try:
        return discover_project_and_tier(user_id)["project_id"]
    except Exception as e:
        log.warning("Could not discover project for %s: %s", user_id, e)
        return None


def generate(user_id, messages, files=None, system_instruction=None,
             thinking=False, thinking_budget=8192,
             grounding=False,
             response_mime_type=None, response_schema=None):
    from prompt_engine import check_output_guardrails

    headers = _headers(user_id)
    contents = _build_contents(messages, files)
    project_id = _get_project_id(user_id)

    # If grounding requested, fetch web results and inject as context
    grounding_results = []
    if grounding:
        last_user_text = next(
            (m["text"] for m in reversed(messages) if m.get("role") == "user" and m.get("text")),
            None,
        )
        if last_user_text:
            grounding_results = serp_search(last_user_text)
        if grounding_results:
            web_ctx = _build_grounding_context(grounding_results)
            existing_si = system_instruction or ""
            combined_si = (existing_si + "\n\n" + web_ctx).strip() if existing_si else web_ctx
            system_instruction = combined_si

    request_body = {"contents": contents}
    if system_instruction:
        request_body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    # Base gen_config (no thinkingConfig) — used by fallback models
    base_gen_config = {}
    if response_mime_type:
        base_gen_config["responseMimeType"] = response_mime_type
    if response_schema:
        base_gen_config["responseSchema"] = response_schema

    # Thinking gen_config — only for the designated thinking model
    thinking_gen_config = dict(base_gen_config)
    if thinking:
        thinking_gen_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

    cfg = get_model_config()
    if grounding:
        # Use regular fallback models with Brave Search context injected above
        model_plan = [(m, False) for m in cfg["fallback"]]
    elif thinking:
        # Try thinking model first; fall back to regular chain without thinking
        seen = set()
        model_plan = []
        for m, use_t in [(cfg["thinking"], True)] + [(m, False) for m in cfg["fallback"]]:
            if m not in seen:
                seen.add(m)
                model_plan.append((m, use_t))
    else:
        model_plan = [(m, False) for m in cfg["fallback"]]

    url = f"{ENDPOINT}/v1internal:generateContent"
    _t0 = time.time()
    _models_tried = []
    log.info("[GENERATE] uid=%s thinking=%s grounding=%s models=%s",
             user_id, thinking, grounding, [m for m, _ in model_plan])

    for model_idx, (model, use_thinking) in enumerate(model_plan):
        is_last_model = model_idx == len(model_plan) - 1
        gc = thinking_gen_config if use_thinking else base_gen_config
        current_request = dict(request_body)
        if gc:
            current_request["generationConfig"] = gc
        elif "generationConfig" in current_request:
            del current_request["generationConfig"]
        body = {"model": model, "project": project_id, "request": current_request}
        _models_tried.append(model)

        for attempt in range(RETRY_MAX_ATTEMPTS):
            try:
                resp = http_requests.post(url, headers=headers, json=body, timeout=180)
            except Exception as e:
                return {"error": f"Request failed: {e}"}

            log.info("[GEMINI] model=%s attempt=%d status=%s", model, attempt + 1, resp.status_code)

            if resp.status_code == 200:
                data = resp.json()
                try:
                    parts = data["response"]["candidates"][0]["content"]["parts"]
                    text = next((p["text"] for p in parts if "text" in p and not p.get("thought")), None)
                    if text is None:
                        text = json.dumps(data, indent=2)
                    grounding_meta = data["response"]["candidates"][0].get("groundingMetadata")
                    usage = data["response"].get("usageMetadata", {})
                except (KeyError, IndexError):
                    text = json.dumps(data, indent=2)
                    grounding_meta = None
                    usage = {}
                latency_ms = int((time.time() - _t0) * 1000)
                fallback_note = f" (fallback from {_models_tried[0]})" if len(_models_tried) > 1 else ""
                log.info("[GENERATE] uid=%s model=%s%s latency=%dms in_tokens=%s out_tokens=%s",
                         user_id, model, fallback_note, latency_ms,
                         usage.get("promptTokenCount", "?"),
                         usage.get("candidatesTokenCount", "?"))
                result = {"text": check_output_guardrails(text)}
                if grounding_meta:
                    result["grounding"] = grounding_meta
                elif grounding_results:
                    result["grounding"] = {"sources": grounding_results.get("sources", [])}
                return result

            if resp.status_code == 401:
                # Stale/invalid access token — force refresh, re-fetch project, and retry
                log.warning("[GENERATE] uid=%s 401 on attempt %d — refreshing token", user_id, attempt + 1)
                try:
                    headers = _refresh_headers(user_id)
                    # project_id may be None if it was fetched with the invalid token;
                    # re-fetch it now that we have a valid token
                    new_project_id = _get_project_id(user_id)
                    if new_project_id:
                        project_id = new_project_id
                        body["project"] = project_id
                    continue  # retry with new token + project
                except PermissionError as e:
                    return {"error": f"Authentication failed: {e}"}

            if resp.status_code in (404, 500):
                log.warning("[GENERATE] uid=%s model=%s %s, trying next", user_id, model, resp.status_code)
                break  # try next model

            if resp.status_code in (429, 503):
                kind, value = _classify_error(resp)
                if kind == "terminal":
                    log.warning("[GENERATE] uid=%s model=%s terminal quota, trying next", user_id, model)
                    break  # try next model
                if not is_last_model:
                    log.warning("[GENERATE] uid=%s model=%s 429 retryable, skipping to next model", user_id, model)
                    break  # fall through to next model immediately
                wait = _retry_delay(attempt, value)
                log.info("Model %s: retryable, waiting %.1fs (attempt %d/%d)",
                         model, wait, attempt + 1, RETRY_MAX_ATTEMPTS)
                time.sleep(wait)
                continue

            log.warning("[GEMINI] error body: %s", resp.text[:300])
            return {"error": f"API error ({resp.status_code}): {resp.text}"}

    # ── Quota sharing: borrow other users' tokens if all models failed ──
    _tried_donors = {user_id}
    while True:
        donor = _get_quota_donor_next(user_id, _tried_donors)
        if not donor:
            break
        _tried_donors.add(donor)
        log.info("[QUOTA-SHARE] uid=%s borrowing quota from uid=%s", user_id, donor)
        try:
            donor_headers = _headers(donor)
            donor_project = _get_project_id(donor)
        except Exception:
            continue
        donor_exhausted = False
        for model_idx, (model, use_thinking) in enumerate(model_plan):
            gc = thinking_gen_config if use_thinking else base_gen_config
            current_request = dict(request_body)
            if gc:
                current_request["generationConfig"] = gc
            elif "generationConfig" in current_request:
                del current_request["generationConfig"]
            body = {"model": model, "project": donor_project, "request": current_request}
            try:
                resp = http_requests.post(url, headers=donor_headers, json=body, timeout=180)
                log.info("[QUOTA-SHARE] model=%s donor=%s status=%s", model, donor, resp.status_code)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates") or data.get("response", {}).get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
                        latency_ms = int((time.time() - _t0) * 1000)
                        log.info("[GENERATE] uid=%s model=%s (quota-share via %s) latency=%dms", user_id, model, donor, latency_ms)
                        return {"text": text, "session_id": None, "grounding_results": grounding_results}
                if resp.status_code in (429, 503):
                    donor_exhausted = True
                    break  # this donor is also exhausted, try next donor
            except Exception:
                donor_exhausted = True
                break
        if not donor_exhausted:
            break  # non-quota error, stop trying

    latency_ms = int((time.time() - _t0) * 1000)
    log.error("[GENERATE] uid=%s all models exhausted tried=%s latency=%dms", user_id, _models_tried, latency_ms)
    return {"error": "Service temporarily busy. Please retry in a few seconds."}


def stream_generate(user_id, messages, files=None, system_instruction=None,
                    thinking=False, thinking_budget=8192,
                    grounding=False,
                    response_mime_type=None, response_schema=None):
    from prompt_engine import check_output_guardrails
    headers = _headers(user_id)
    contents = _build_contents(messages, files)
    project_id = _get_project_id(user_id)

    # If grounding requested, fetch web results and inject as context
    grounding_results = []
    if grounding:
        last_user_text = next(
            (m["text"] for m in reversed(messages) if m.get("role") == "user" and m.get("text")),
            None,
        )
        if last_user_text:
            grounding_results = serp_search(last_user_text)
        if grounding_results:
            web_ctx = _build_grounding_context(grounding_results)
            existing_si = system_instruction or ""
            system_instruction = (existing_si + "\n\n" + web_ctx).strip() if existing_si else web_ctx

    request_body = {"contents": contents}
    if system_instruction:
        request_body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    base_gen_config = {}
    if response_mime_type:
        base_gen_config["responseMimeType"] = response_mime_type
    if response_schema:
        base_gen_config["responseSchema"] = response_schema

    thinking_gen_config = dict(base_gen_config)
    if thinking:
        thinking_gen_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

    cfg = get_model_config()
    if grounding:
        # Brave Search results already injected as context above; use regular models
        model_plan = [(m, False) for m in cfg["fallback"]]
    elif thinking:
        seen = set()
        model_plan = []
        for m, use_t in [(cfg["thinking"], True)] + [(m, False) for m in cfg["fallback"]]:
            if m not in seen:
                seen.add(m)
                model_plan.append((m, use_t))
    else:
        model_plan = [(m, False) for m in cfg["fallback"]]

    url = f"{ENDPOINT}/v1internal:streamGenerateContent?alt=sse"
    _t0 = time.time()
    _models_tried = []
    log.info("[STREAM] uid=%s thinking=%s grounding=%s models=%s",
             user_id, thinking, grounding, [m for m, _ in model_plan])

    for model_idx, (model, use_thinking) in enumerate(model_plan):
        is_last_model = model_idx == len(model_plan) - 1
        gc = thinking_gen_config if use_thinking else base_gen_config
        current_request = dict(request_body)
        if gc:
            current_request["generationConfig"] = gc
        elif "generationConfig" in current_request:
            del current_request["generationConfig"]
        body = {"model": model, "project": project_id, "request": current_request}
        _models_tried.append(model)

        # Streaming uses fewer retries (ported from gemini-cli: 4 max for mid-stream)
        max_attempts = 4
        for attempt in range(max_attempts):
            try:
                resp = http_requests.post(url, headers=headers, json=body, stream=True, timeout=120)
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                return

            log.info("[GEMINI-STREAM] model=%s attempt=%d status=%s", model, attempt + 1, resp.status_code)

            if resp.status_code == 401:
                log.warning("[STREAM] uid=%s 401 on attempt %d — refreshing token", user_id, attempt + 1)
                try:
                    headers = _refresh_headers(user_id)
                    new_project_id = _get_project_id(user_id)
                    if new_project_id:
                        project_id = new_project_id
                        body["project"] = project_id
                    continue  # retry with new token + project
                except PermissionError as e:
                    yield f"data: {json.dumps({'error': f'Authentication failed: {e}'})}\n\n"
                    return

            if resp.status_code in (404, 500):
                log.warning("[STREAM] uid=%s model=%s %s, trying next", user_id, model, resp.status_code)
                break  # try next model

            if resp.status_code in (429, 503):
                kind, value = _classify_error(resp)
                if kind == "terminal":
                    log.warning("[STREAM] uid=%s model=%s terminal quota, trying next", user_id, model)
                    break  # try next model
                if not is_last_model:
                    log.warning("[STREAM] uid=%s model=%s 429 retryable, skipping to next model", user_id, model)
                    break  # fall through to next model immediately
                wait = _retry_delay(attempt, min(value, 10))  # shorter waits for streaming
                log.info("Model %s: retryable, waiting %.1fs (attempt %d/%d)",
                         model, wait, attempt + 1, max_attempts)
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                yield f"data: {json.dumps({'error': resp.text})}\n\n"
                return

            # Success — stream the response
            fallback_note = f" (fallback from {_models_tried[0]})" if len(_models_tried) > 1 else ""
            ttfb_ms = int((time.time() - _t0) * 1000)
            log.info("[STREAM] uid=%s model=%s%s ttfb=%dms", user_id, model, fallback_note, ttfb_ms)
            yield f"data: {json.dumps({'model': model, 'type': 'start'})}\n\n"

            grounding_meta = None
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data: "):
                    raw = line[6:]
                    try:
                        chunk = json.loads(raw)
                        candidate = chunk["response"]["candidates"][0]
                        parts = candidate["content"]["parts"]
                        text = next((p["text"] for p in parts if "text" in p and not p.get("thought")), None)
                        if text:
                            text = check_output_guardrails(text)
                            yield f"data: {json.dumps({'text': text})}\n\n"
                        # Capture grounding metadata if present (may appear in any chunk)
                        gm = candidate.get("groundingMetadata")
                        if gm:
                            grounding_meta = gm
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass

            total_ms = int((time.time() - _t0) * 1000)
            log.info("[STREAM] uid=%s model=%s done total=%dms", user_id, model, total_ms)
            done_payload = {'type': 'done'}
            if grounding_meta:
                done_payload['grounding'] = grounding_meta
            elif grounding_results:
                done_payload['grounding'] = {'sources': grounding_results.get('sources', [])}
            yield f"data: {json.dumps(done_payload)}\n\n"
            return

    # ── Quota sharing for streaming ──
    _tried_donors = {user_id}
    while True:
        donor = _get_quota_donor_next(user_id, _tried_donors)
        if not donor:
            break
        _tried_donors.add(donor)
        log.info("[QUOTA-SHARE-STREAM] uid=%s borrowing from uid=%s", user_id, donor)
        try:
            donor_headers = _headers(donor)
            donor_project = _get_project_id(donor)
        except Exception:
            continue
        # Try first model with donor (non-streaming fallback — simpler, still works)
        for model, use_thinking in model_plan:
            gc = thinking_gen_config if use_thinking else base_gen_config
            current_request = dict(request_body)
            if gc:
                current_request["generationConfig"] = gc
            elif "generationConfig" in current_request:
                del current_request["generationConfig"]
            body = {"model": model, "project": donor_project, "request": current_request}
            try:
                resp = http_requests.post(
                    f"{ENDPOINT}/v1internal:generateContent",
                    headers=donor_headers, json=body, timeout=180)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates") or data.get("response", {}).get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
                        log.info("[STREAM] uid=%s model=%s (quota-share via %s) OK", user_id, model, donor)
                        yield f"data: {json.dumps({'text': text})}\n\n"
                        yield f"data: {json.dumps({'type': 'done'})}\n\n"
                        return
                if resp.status_code in (429, 503):
                    break  # donor exhausted, try next
            except Exception:
                break

    log.error("[STREAM] uid=%s all models + donors exhausted tried=%s", user_id, _models_tried)
    yield f"data: {json.dumps({'error': 'Service temporarily busy. Please retry in a few seconds.'})}\n\n"


def generate_image(user_id, prompt):
    """Generate an image using Gemini 2.0 Flash native image generation.

    Returns: (image_bytes, mime_type, caption) or (None, None, error_str)
    """
    headers = _headers(user_id)
    project_id = _get_project_id(user_id)

    request_body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }

    image_models = ["gemini-2.0-flash-exp", "gemini-2.0-flash"]
    url = f"{ENDPOINT}/v1internal:generateContent"

    for model in image_models:
        body = {"model": model, "project": project_id, "request": request_body}

        for attempt in range(RETRY_MAX_ATTEMPTS):
            try:
                resp = http_requests.post(url, headers=headers, json=body, timeout=120)
            except Exception as e:
                return None, None, f"Request failed: {e}"

            if resp.status_code == 200:
                data = resp.json()
                try:
                    parts = data["response"]["candidates"][0]["content"]["parts"]
                except (KeyError, IndexError):
                    break  # try next model

                image_b64 = None
                image_mime = "image/png"
                caption = ""
                for part in parts:
                    if "inline_data" in part:
                        image_b64 = part["inline_data"]["data"]
                        image_mime = part["inline_data"].get("mimeType", "image/png")
                    elif "text" in part:
                        caption = part["text"]

                if image_b64:
                    return base64.b64decode(image_b64), image_mime, caption
                break  # no image in response, try next model

            if resp.status_code == 401:
                log.warning("[IMAGE] uid=%s 401 on attempt %d — refreshing token", user_id, attempt + 1)
                try:
                    headers = _refresh_headers(user_id)
                    new_pid = _get_project_id(user_id)
                    if new_pid:
                        project_id = new_pid
                        body["project"] = project_id
                    continue  # retry with new token
                except PermissionError as e:
                    return None, None, f"Authentication failed: {e}"

            if resp.status_code in (429, 503):
                kind, value = _classify_error(resp)
                if kind == "terminal":
                    break  # try next model
                wait = _retry_delay(attempt, value)
                log.info("Image model %s: retryable, waiting %.1fs", model, wait)
                time.sleep(wait)
                continue

            if resp.status_code == 404:
                break  # try next model

            log.warning("Image model %s returned %s: %s", model, resp.status_code, resp.text[:200])
            break  # try next model

    # ── Quota sharing for image generation ──
    _tried_donors = {user_id}
    while True:
        donor = _get_quota_donor_next(user_id, _tried_donors)
        if not donor:
            break
        _tried_donors.add(donor)
        log.info("[QUOTA-SHARE-IMAGE] uid=%s borrowing from uid=%s", user_id, donor)
        try:
            donor_headers = _headers(donor)
            donor_project = _get_project_id(donor)
        except Exception:
            continue
        for model in image_models:
            body = {"model": model, "project": donor_project, "request": request_body}
            try:
                resp = http_requests.post(url, headers=donor_headers, json=body, timeout=120)
                if resp.status_code == 200:
                    data = resp.json()
                    try:
                        parts = data["response"]["candidates"][0]["content"]["parts"]
                    except (KeyError, IndexError):
                        break
                    image_b64 = None
                    image_mime = "image/png"
                    caption = ""
                    for part in parts:
                        if "inline_data" in part:
                            image_b64 = part["inline_data"]["data"]
                            image_mime = part["inline_data"].get("mimeType", "image/png")
                        elif "text" in part:
                            caption = part["text"]
                    if image_b64:
                        log.info("[QUOTA-SHARE-IMAGE] uid=%s model=%s via donor=%s OK", user_id, model, donor)
                        return base64.b64decode(image_b64), image_mime, caption
                if resp.status_code in (429, 503):
                    break  # donor exhausted
            except Exception:
                break

    return None, None, "Image generation not available — try /api/generate/file with type=svg"


def gemini_generate_file(user_id, prompt, file_type="html"):
    """Generate a file (HTML, SVG, CSV, JSON, Python) using Gemini API."""
    from prompt_engine import get_file_meta_prompt

    full_prompt = get_file_meta_prompt(file_type, prompt)
    result = generate(user_id, [{"role": "user", "text": full_prompt}])
    if "error" in result:
        return None, result["error"]

    content = result["text"].strip()

    # Strip markdown code fences if present
    if content.startswith("```"):
        first_newline = content.index("\n") if "\n" in content else len(content)
        content = content[first_newline + 1:]
    if content.endswith("```"):
        content = content[:-3].rstrip()

    if len(content.encode("utf-8")) > 5 * 1024 * 1024:
        return None, "Generated file exceeds 5MB limit"

    return content, None


def _pcm_to_wav(pcm_bytes, sample_rate=24000, channels=1, bit_depth=16):
    """Wrap raw PCM bytes in a WAV container header."""
    import struct
    byte_rate = sample_rate * channels * bit_depth // 8
    block_align = channels * bit_depth // 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(pcm_bytes), b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate,
        byte_rate, block_align, bit_depth,
        b"data", len(pcm_bytes),
    )
    return header + pcm_bytes


def generate_tts(text, voice="Aoede"):
    """TTS via Gemini API key (generativelanguage.googleapis.com).

    Models tried in order from config tts_models list.
    Input capped at 200 chars (~10s of speech). Returns: (wav_bytes, None) or (None, error_str)
    """
    text = text[:200]  # ~10s of speech at average speaking rate
    tts_models = get_model_config().get("tts_models", _DEFAULT_MODEL_CONFIG["tts_models"])
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    for _ in range(len(_GEMINI_API_KEYS)):
        key = _get_api_key()
        for model in tts_models:
            try:
                resp = http_requests.post(
                    f"{GENAI_ENDPOINT}/models/{model}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json=body,
                    timeout=60,
                )
            except Exception as e:
                return None, f"TTS request failed: {e}"

            if resp.status_code == 429:
                log.warning("[TTS] 429 on key index %d model=%s — rotating key", _key_index % len(_GEMINI_API_KEYS), model)
                _rotate_api_key()
                break  # retry outer key-rotation loop

            if resp.status_code == 404:
                log.info("[TTS] model %s not found, trying next", model)
                continue

            if resp.status_code == 200:
                try:
                    parts = resp.json()["candidates"][0]["content"]["parts"]
                    audio_b64 = next(p["inlineData"]["data"] for p in parts if "inlineData" in p)
                    pcm = base64.b64decode(audio_b64)
                    log.info("[TTS] success model=%s", model)
                    return _pcm_to_wav(pcm), None
                except (KeyError, IndexError, StopIteration) as e:
                    return None, f"TTS response parse error: {e}"

            return None, f"TTS error {resp.status_code}: {resp.text[:200]}"

    return None, "All Gemini API keys / TTS models exhausted"


def generate_video_veo(prompt):
    """Generate a real MP4 video via Veo (generativelanguage.googleapis.com).

    Polls until done (up to 10 min). Returns: (mp4_bytes, None) or (None, error_str)
    """
    veo_models = get_model_config().get("veo_models", _DEFAULT_MODEL_CONFIG["veo_models"])

    for _ in range(len(_GEMINI_API_KEYS)):
        key = _get_api_key()
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

        started = False
        for model in veo_models:
            try:
                resp = http_requests.post(
                    f"{GENAI_ENDPOINT}/models/{model}:predictLongRunning",
                    headers=headers,
                    json={"instances": [{"prompt": prompt}], "parameters": {"durationSeconds": 8}},
                    timeout=30,
                )
            except Exception as e:
                return None, f"Veo request failed: {e}"

            if resp.status_code == 429:
                log.warning("[VEO] 429 on key index %d — rotating", _key_index % len(_GEMINI_API_KEYS))
                _rotate_api_key()
                break  # retry outer loop with new key

            if resp.status_code == 404:
                log.info("[VEO] model %s not found, trying next", model)
                continue

            if resp.status_code != 200:
                return None, f"Veo start error {resp.status_code}: {resp.text[:200]}"

            operation_name = resp.json().get("name")
            if not operation_name:
                return None, "Veo returned no operation name"

            log.info("[VEO] operation started: %s", operation_name)
            started = True

            # Poll until done (max 10 min = 60 × 10s)
            for tick in range(60):
                time.sleep(10)
                try:
                    poll = http_requests.get(
                        f"{GENAI_ENDPOINT}/{operation_name}",
                        headers=headers,
                        timeout=30,
                    )
                except Exception as e:
                    log.warning("[VEO] poll error: %s", e)
                    continue

                if poll.status_code != 200:
                    continue

                status = poll.json()
                if not status.get("done"):
                    log.info("[VEO] tick %d — not done yet", tick + 1)
                    continue

                # Done — extract video URI
                try:
                    video_uri = (
                        status["response"]["generateVideoResponse"]
                        ["generatedSamples"][0]["video"]["uri"]
                    )
                except (KeyError, IndexError):
                    return None, "Veo done but no video URI in response"

                log.info("[VEO] downloading video from %s", video_uri[:60])
                try:
                    dl = http_requests.get(
                        video_uri,
                        headers={"x-goog-api-key": key},
                        timeout=120,
                        allow_redirects=True,
                    )
                except Exception as e:
                    return None, f"Video download failed: {e}"

                if dl.status_code == 200:
                    return dl.content, None
                return None, f"Video download error {dl.status_code}"

            return None, "Veo generation timed out (10 min)"

        if started:
            break  # started but timed out — no point rotating key

    return None, "All Gemini API keys exhausted for Veo"


def transcribe_audio(user_id, audio_path, mime_type=None):
    """Transcribe audio file to text using Gemini."""
    if mime_type is None:
        mime_type, _ = mimetypes.guess_type(audio_path)
        if mime_type is None:
            mime_type = "audio/webm"

    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("ascii")

    messages = [{
        "role": "user",
        "text": "Transcribe this audio exactly as spoken. Return only the transcribed text, nothing else.",
        "files": [{"base64": audio_b64, "mime_type": mime_type}],
    }]

    result = generate(user_id, messages)
    if "error" in result:
        return None, result["error"]

    return result["text"].strip(), None


def file_to_inline(filepath, mime_type=None):
    if mime_type is None:
        mime_type, _ = mimetypes.guess_type(filepath)
        if mime_type is None:
            mime_type = "application/octet-stream"

    with open(filepath, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")

    return {"mime_type": mime_type, "base64": data}
