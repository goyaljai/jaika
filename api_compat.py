"""API Compatibility Layer — OpenAI, Anthropic, and Gemini-native formats.

All three formats route to the same cloudcode-pa backend using the user's
OAuth token. The user_id acts as the API key in each format:

  OpenAI:    Authorization: Bearer <user_id>
  Anthropic: x-api-key: <user_id>
  Gemini:    ?key=<user_id>  OR  Authorization: Bearer <user_id>
"""

import json
import time
import uuid

from flask import Blueprint, Response, jsonify, request, stream_with_context

from auth import get_access_token
from gemini import generate, stream_generate
from prompt_engine import check_input_guardrails

compat_bp = Blueprint("compat", __name__)

SUPPORTED_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
]

# Map common OpenAI / Anthropic model names to Gemini equivalents
MODEL_MAP = {
    # OpenAI
    "gpt-4o":            "gemini-2.5-pro",
    "gpt-4o-mini":       "gemini-2.5-flash",
    "gpt-4":             "gemini-2.5-pro",
    "gpt-4-turbo":       "gemini-2.5-pro",
    "gpt-3.5-turbo":     "gemini-2.5-flash",
    # Anthropic
    "claude-3-opus":         "gemini-2.5-pro",
    "claude-3-sonnet":       "gemini-2.5-flash",
    "claude-3-haiku":        "gemini-2.5-flash",
    "claude-3-5-sonnet":     "gemini-2.5-pro",
    "claude-opus-4":         "gemini-2.5-pro",
    "claude-sonnet-4":       "gemini-2.5-flash",
}


# ── Auth helpers ─────────────────────────────────────────────────────────────

def _extract_user_id():
    """Extract user_id from whichever auth header is present."""
    # OpenAI / Gemini Bearer
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        uid = auth[7:].strip()
        if uid:
            return uid

    # Anthropic
    uid = request.headers.get("x-api-key", "").strip()
    if uid:
        return uid

    # Gemini ?key= query param
    uid = request.args.get("key", "").strip()
    if uid:
        return uid

    # Existing jaika header
    uid = request.headers.get("X-User-Id", "").strip()
    if uid:
        return uid

    return None


def _require_user():
    """Return (user_id, None) or (None, error_response)."""
    uid = _extract_user_id()
    if not uid:
        return None, (jsonify({"error": {"message": "Missing API key / user id", "type": "auth_error"}}), 401)
    if get_access_token(uid) is None:
        return None, (jsonify({"error": {"message": "Token expired — please log in again", "type": "auth_error"}}), 401)
    return uid, None


# ── Message converters ───────────────────────────────────────────────────────

def _openai_messages_to_gemini(messages):
    """Convert OpenAI messages list → (gemini_messages, system_instruction)."""
    system_parts = []
    gemini_msgs = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # content can be a string or a list of parts
        if isinstance(content, list):
            text = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        else:
            text = content

        if role == "system":
            system_parts.append(text)
        elif role == "assistant":
            gemini_msgs.append({"role": "model", "text": text})
        else:
            gemini_msgs.append({"role": "user", "text": text})

    system_instruction = "\n".join(system_parts) if system_parts else None
    return gemini_msgs, system_instruction


def _anthropic_messages_to_gemini(messages, system=None):
    """Convert Anthropic messages list → (gemini_messages, system_instruction)."""
    gemini_msgs = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, list):
            text = " ".join(
                b.get("text", "") for b in content if b.get("type") == "text"
            )
        else:
            text = content

        gemini_msgs.append({
            "role": "model" if role == "assistant" else "user",
            "text": text,
        })

    return gemini_msgs, system or None


def _resolve_model(requested):
    """Map requested model name to a Gemini model name."""
    if not requested:
        return None
    lower = requested.lower()
    return MODEL_MAP.get(lower, requested if any(lower.startswith(m) for m in ["gemini"]) else None)


# ── OpenAI format ─────────────────────────────────────────────────────────────

@compat_bp.route("/v1/models", methods=["GET"])
def openai_list_models():
    uid, err = _require_user()
    if err:
        return err
    models = [
        {"id": m, "object": "model", "created": 1700000000, "owned_by": "google"}
        for m in SUPPORTED_MODELS
    ]
    return jsonify({"object": "list", "data": models})


@compat_bp.route("/v1/chat/completions", methods=["POST"])
def openai_chat_completions():
    uid, err = _require_user()
    if err:
        return err

    data = request.get_json(force=True)
    messages = data.get("messages", [])
    do_stream = data.get("stream", False)
    model = _resolve_model(data.get("model", "")) or "gemini-2.5-flash"

    gemini_msgs, system_instruction = _openai_messages_to_gemini(messages)

    if not gemini_msgs:
        return jsonify({"error": {"message": "No messages provided", "type": "invalid_request_error"}}), 400

    # Input guardrails
    last_text = gemini_msgs[-1].get("text", "") if gemini_msgs else ""
    if last_text:
        is_safe, safety_msg = check_input_guardrails(last_text)
        if not is_safe:
            return jsonify({"error": {"message": safety_msg, "type": "invalid_request_error"}}), 400

    if do_stream:
        def _gen():
            chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            ts = int(time.time())
            # opening chunk with role
            opening = {
                "id": chunk_id, "object": "chat.completion.chunk",
                "created": ts, "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(opening)}\n\n"

            for raw in stream_generate(uid, gemini_msgs, system_instruction=system_instruction):
                if not raw.startswith("data: "):
                    continue
                try:
                    d = json.loads(raw[6:])
                except json.JSONDecodeError:
                    continue
                if "text" in d:
                    chunk = {
                        "id": chunk_id, "object": "chat.completion.chunk",
                        "created": ts, "model": model,
                        "choices": [{"index": 0, "delta": {"content": d["text"]}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                elif d.get("type") == "done":
                    final = {
                        "id": chunk_id, "object": "chat.completion.chunk",
                        "created": ts, "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
                    yield f"data: {json.dumps(final)}\n\n"
                    yield "data: [DONE]\n\n"

        return Response(
            stream_with_context(_gen()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming
    result = generate(uid, gemini_msgs, system_instruction=system_instruction)
    if "error" in result:
        return jsonify({"error": {"message": result["error"], "type": "api_error"}}), 502

    return jsonify({
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result["text"]},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


# ── Anthropic format ──────────────────────────────────────────────────────────

@compat_bp.route("/v1/messages", methods=["POST"])
def anthropic_messages():
    uid, err = _require_user()
    if err:
        return err

    data = request.get_json(force=True)
    messages = data.get("messages", [])
    system = data.get("system", None)
    do_stream = data.get("stream", False)
    model = _resolve_model(data.get("model", "")) or "gemini-2.5-flash"

    gemini_msgs, system_instruction = _anthropic_messages_to_gemini(messages, system)

    if not gemini_msgs:
        return jsonify({"error": {"type": "invalid_request_error", "message": "No messages provided"}}), 400

    # Input guardrails
    last_text = gemini_msgs[-1].get("text", "") if gemini_msgs else ""
    if last_text:
        is_safe, safety_msg = check_input_guardrails(last_text)
        if not is_safe:
            return jsonify({"type": "error", "error": {"type": "invalid_request_error", "message": safety_msg}}), 400

    msg_id = f"msg_{uuid.uuid4().hex[:12]}"

    if do_stream:
        def _gen():
            ts = int(time.time())
            # message_start
            yield f"event: message_start\ndata: {json.dumps({'type':'message_start','message':{'id':msg_id,'type':'message','role':'assistant','content':[],'model':model,'stop_reason':None,'usage':{'input_tokens':0,'output_tokens':0}}})}\n\n"
            # content_block_start
            yield f"event: content_block_start\ndata: {json.dumps({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})}\n\n"

            for raw in stream_generate(uid, gemini_msgs, system_instruction=system_instruction):
                if not raw.startswith("data: "):
                    continue
                try:
                    d = json.loads(raw[6:])
                except json.JSONDecodeError:
                    continue
                if "text" in d:
                    delta = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": d["text"]}}
                    yield f"event: content_block_delta\ndata: {json.dumps(delta)}\n\n"
                elif d.get("type") == "done":
                    yield f"event: content_block_stop\ndata: {json.dumps({'type':'content_block_stop','index':0})}\n\n"
                    yield f"event: message_delta\ndata: {json.dumps({'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':0}})}\n\n"
                    yield f"event: message_stop\ndata: {json.dumps({'type':'message_stop'})}\n\n"

        return Response(
            stream_with_context(_gen()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    result = generate(uid, gemini_msgs, system_instruction=system_instruction)
    if "error" in result:
        return jsonify({"type": "error", "error": {"type": "api_error", "message": result["error"]}}), 502

    return jsonify({
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": result["text"]}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    })


# ── Gemini native format ──────────────────────────────────────────────────────

@compat_bp.route("/v1beta/models", methods=["GET"])
@compat_bp.route("/v1/models/gemini", methods=["GET"])
def gemini_list_models():
    uid, err = _require_user()
    if err:
        return err
    models = [
        {
            "name": f"models/{m}",
            "version": m.split("-")[-1],
            "displayName": m,
            "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
        }
        for m in SUPPORTED_MODELS
    ]
    return jsonify({"models": models})


@compat_bp.route("/v1beta/models/<path:model_action>", methods=["POST"])
def gemini_generate(model_action):
    """Handle /v1beta/models/{model}:generateContent and :streamGenerateContent"""
    uid, err = _require_user()
    if err:
        return err

    # parse model and action from path e.g. "gemini-2.5-flash:generateContent"
    if ":" in model_action:
        model_name, action = model_action.rsplit(":", 1)
    else:
        model_name, action = model_action, "generateContent"

    do_stream = "stream" in action.lower()

    data = request.get_json(force=True)
    contents = data.get("contents", [])
    system_instruction = None
    si = data.get("systemInstruction", {})
    if si:
        parts = si.get("parts", [])
        system_instruction = " ".join(p.get("text", "") for p in parts)

    # Convert Gemini native contents format to internal format
    gemini_msgs = []
    for c in contents:
        role = c.get("role", "user")
        parts = c.get("parts", [])
        text = " ".join(p.get("text", "") for p in parts if "text" in p)
        gemini_msgs.append({"role": role, "text": text})

    if not gemini_msgs:
        return jsonify({"error": {"message": "No contents provided"}}), 400

    # Input guardrails
    last_text = gemini_msgs[-1].get("text", "") if gemini_msgs else ""
    if last_text:
        is_safe, safety_msg = check_input_guardrails(last_text)
        if not is_safe:
            return jsonify({"error": {"message": safety_msg}}), 400

    if do_stream:
        def _gen():
            for raw in stream_generate(uid, gemini_msgs, system_instruction=system_instruction):
                if not raw.startswith("data: "):
                    continue
                try:
                    d = json.loads(raw[6:])
                except json.JSONDecodeError:
                    continue
                if "text" in d:
                    chunk = {
                        "candidates": [{
                            "content": {"role": "model", "parts": [{"text": d["text"]}]},
                            "finishReason": None,
                            "index": 0,
                        }]
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                elif d.get("type") == "done":
                    yield f"data: {json.dumps({'candidates':[{'finishReason':'STOP','index':0}]})}\n\n"

        return Response(
            stream_with_context(_gen()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    result = generate(uid, gemini_msgs, system_instruction=system_instruction)
    if "error" in result:
        return jsonify({"error": {"message": result["error"]}}), 502

    return jsonify({
        "candidates": [{
            "content": {"role": "model", "parts": [{"text": result["text"]}]},
            "finishReason": "STOP",
            "index": 0,
        }],
        "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0},
    })
