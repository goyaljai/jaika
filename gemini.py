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
CLI_VERSION = "0.36.0"
MODEL_FALLBACK = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]
MODEL_THINKING  = "gemini-2.5-pro"   # best model for extended thinking
MODEL_TTS       = "gemini-2.5-flash"  # TTS via responseModalities AUDIO

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
    probe = {"metadata": _get_client_metadata()}
    resp = http_requests.post(
        f"{ENDPOINT}/v1internal:loadCodeAssist",
        headers=headers,
        data=json.dumps(probe),
        timeout=30,
    )
    resp.raise_for_status()
    load_data = resp.json()

    project_id = load_data.get("cloudaicompanionProject")
    if not project_id:
        raise ValueError(f"loadCodeAssist returned no project: {load_data}")

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

    # Step 3 — onboard if not already onboarded
    if not load_data.get("currentTier"):
        onboard_payload = {
            "tierId": tier_id,
            "cloudaicompanionProject": project_id,
            "metadata": _get_client_metadata(project_id),
        }
        ob_resp = http_requests.post(
            f"{ENDPOINT}/v1internal:onboardUser",
            headers=headers,
            data=json.dumps(onboard_payload),
            timeout=30,
        )
        if ob_resp.status_code == 200:
            # Poll until done (max 30s)
            for _ in range(6):
                ob_data = ob_resp.json()
                if ob_data.get("done"):
                    break
                time.sleep(5)
                ob_resp = http_requests.post(
                    f"{ENDPOINT}/v1internal:onboardUser",
                    headers=headers,
                    data=json.dumps(onboard_payload),
                    timeout=30,
                )

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


def _headers(user_id):
    token = get_access_token(user_id)
    if not token:
        raise PermissionError("No valid access token")
    return {
        "Authorization": f"Bearer {token}",
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

    request_body = {"contents": contents}
    if system_instruction:
        request_body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    gen_config = {}
    if thinking:
        gen_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}
    if grounding:
        request_body["tools"] = [{"googleSearch": {}}]
    if response_mime_type:
        gen_config["responseMimeType"] = response_mime_type
    if response_schema:
        gen_config["responseSchema"] = response_schema
    if gen_config:
        request_body["generationConfig"] = gen_config

    models = [MODEL_THINKING] if thinking else MODEL_FALLBACK
    for model in models:
        body = {"model": model, "project": project_id, "request": request_body}
        url = f"{ENDPOINT}/v1internal:generateContent"
        try:
            resp = http_requests.post(url, headers=headers, json=body, timeout=180)
        except Exception as e:
            return {"error": f"Request failed: {e}"}

        log.info("[GEMINI] model=%s status=%s len=%s", model, resp.status_code, len(resp.text))
        if resp.status_code != 200:
            log.warning("[GEMINI] error body: %s", resp.text[:300])

        if resp.status_code == 200:
            data = resp.json()
            try:
                parts = data["response"]["candidates"][0]["content"]["parts"]
                # Skip thought parts, get the text part
                text = next((p["text"] for p in parts if "text" in p and not p.get("thought")), None)
                if text is None:
                    text = json.dumps(data, indent=2)
                # Extract grounding metadata if present
                grounding_meta = data["response"]["candidates"][0].get("groundingMetadata")
            except (KeyError, IndexError):
                text = json.dumps(data, indent=2)
                grounding_meta = None
            result = {"text": check_output_guardrails(text)}
            if grounding_meta:
                result["grounding"] = grounding_meta
            return result

        if resp.status_code in (429, 503):
            log.warning("Model %s rate-limited (%s), not retrying (same quota)", model, resp.status_code)
            return {"error": "Service temporarily busy. Please retry in a few seconds."}
        if resp.status_code == 404:
            log.warning("Model %s not found, trying next", model)
            continue

        return {"error": f"API error ({resp.status_code}): {resp.text}"}

    return {"error": "Service temporarily busy. Please retry in a few seconds."}


def stream_generate(user_id, messages, files=None, system_instruction=None,
                    thinking=False, thinking_budget=8192,
                    grounding=False,
                    response_mime_type=None, response_schema=None):
    from prompt_engine import check_output_guardrails
    headers = _headers(user_id)
    contents = _build_contents(messages, files)
    project_id = _get_project_id(user_id)

    request_body = {"contents": contents}
    if system_instruction:
        request_body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    gen_config = {}
    if thinking:
        gen_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}
    if grounding:
        request_body["tools"] = [{"googleSearch": {}}]
    if response_mime_type:
        gen_config["responseMimeType"] = response_mime_type
    if response_schema:
        gen_config["responseSchema"] = response_schema
    if gen_config:
        request_body["generationConfig"] = gen_config

    models = [MODEL_THINKING] if thinking else MODEL_FALLBACK
    for model in models:
        body = {"model": model, "project": project_id, "request": request_body}
        url = f"{ENDPOINT}/v1internal:streamGenerateContent?alt=sse"
        try:
            resp = http_requests.post(url, headers=headers, json=body, stream=True, timeout=120)
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        if resp.status_code in (429, 503):
            log.warning("Model %s rate-limited (%s), not retrying", model, resp.status_code)
            yield f"data: {json.dumps({'error': 'Service temporarily busy. Please retry in a few seconds.'})}\n\n"
            return
        if resp.status_code == 404:
            log.warning("Model %s not found, trying next", model)
            continue

        if resp.status_code != 200:
            yield f"data: {json.dumps({'error': resp.text})}\n\n"
            return

        yield f"data: {json.dumps({'model': model, 'type': 'start'})}\n\n"

        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("data: "):
                raw = line[6:]
                try:
                    chunk = json.loads(raw)
                    parts = chunk["response"]["candidates"][0]["content"]["parts"]
                    text = next((p["text"] for p in parts if "text" in p and not p.get("thought")), None)
                    if text:
                        text = check_output_guardrails(text)
                        yield f"data: {json.dumps({'text': text})}\n\n"
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass

        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

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

    # Image generation only works on gemini-2.0-flash-exp or gemini-2.0-flash
    image_models = ["gemini-2.0-flash-exp", "gemini-2.0-flash"]
    for model in image_models:
        body = {"model": model, "project": project_id, "request": request_body}
        url = f"{ENDPOINT}/v1internal:generateContent"
        try:
            resp = http_requests.post(url, headers=headers, json=body, timeout=120)
        except Exception as e:
            return None, None, f"Request failed: {e}"

        if resp.status_code in (429, 503):
            log.warning("Image model %s rate-limited (%s), not retrying", model, resp.status_code)
            return None, None, "Service temporarily busy. Please retry in a few seconds."

        if resp.status_code != 200:
            log.warning("Image model %s returned %s: %s", model, resp.status_code, resp.text[:200])
            continue

        data = resp.json()
        try:
            parts = data["response"]["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            continue

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
