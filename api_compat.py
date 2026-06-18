"""API Compatibility Layer — OpenAI, Anthropic, and Gemini-native formats.

All three formats route to the same cloudcode-pa backend using the user's
OAuth token. The user_id acts as the API key in each format:

  OpenAI:    Authorization: Bearer <user_id>
  Anthropic: x-api-key: <user_id>
  Gemini:    ?key=<user_id>  OR  Authorization: Bearer <user_id>
"""

import json
import os
import time
import uuid

from flask import Blueprint, Response, jsonify, request, stream_with_context

from auth import get_access_token
from gemini import generate, stream_generate
from prompt_engine import check_input_guardrails

compat_bp = Blueprint("compat", __name__)

SUPPORTED_MODELS = [
    "gemini-3.5-flash-low",
    "gemini-3-flash-agent",
    "gemini-3.5-flash-extra-low",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-low",
    "gemini-3.1-pro-high",
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
]

# Map common OpenAI / Anthropic model names to Gemini equivalents
MODEL_MAP = {
    # OpenAI — heavy → best model, mini/turbo → lite
    "gpt-4o":            "gemini-3.5-flash-low",
    "gpt-4o-mini":       "gemini-3.5-flash-extra-low",
    "gpt-4":             "gemini-3.1-pro-high",
    "gpt-4-turbo":       "gemini-3.5-flash-low",
    "gpt-3.5-turbo":     "gemini-3.1-flash-lite",
    "gpt-oss-120b":      "gpt-oss-120b-medium",
    # Anthropic — opus → best, sonnet/haiku → lite
    "claude-3-opus":         "claude-opus-4-6-thinking",
    "claude-3-sonnet":       "claude-sonnet-4-6",
    "claude-3-haiku":        "gemini-3.1-flash-lite",
    "claude-3-5-sonnet":     "claude-sonnet-4-6",
    "claude-opus-4":         "claude-opus-4-6-thinking",
    "claude-opus-4.6":       "claude-opus-4-6-thinking",
    "claude-sonnet-4":       "claude-sonnet-4-6",
    "claude-sonnet-4.6":     "claude-sonnet-4-6",
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
    """Map requested model name to an available Antigravity model."""
    if not requested:
        return "gemini-3.5-flash-low"
    lower = requested.lower()
    mapped = MODEL_MAP.get(lower)
    if mapped:
        return mapped
    # Accept known Gemini models as-is
    if lower in [m.lower() for m in SUPPORTED_MODELS]:
        return lower
    # Unknown model → fallback
    return "gemini-3.5-flash-low"


# ── OpenAI Responses format helpers ──────────────────────────────────────────

def _responses_dir(uid):
    path = os.path.join(os.environ.get("JAIKA_DATA_DIR", "./data"), "responses", uid)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _response_path(uid, response_id):
    if not response_id or "/" in response_id or "\\" in response_id or ".." in response_id:
        return None
    return os.path.join(_responses_dir(uid), f"{response_id}.json")


def _save_response_state(uid, response_id, state):
    path = _response_path(uid, response_id)
    if not path:
        return
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def _load_response_state(uid, response_id):
    path = _response_path(uid, response_id)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _content_to_text(content):
    """Extract text from OpenAI-compatible message/item content shapes."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if "text" in part:
                    parts.append(str(part.get("text") or ""))
                elif part.get("type") in ("input_text", "output_text"):
                    parts.append(str(part.get("text") or ""))
                elif part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
        return "\n".join(p for p in parts if p)
    return str(content)


def _responses_input_to_gemini(input_value):
    """Convert Responses API `input` into internal messages + system text."""
    if isinstance(input_value, str):
        return [{"role": "user", "text": input_value}], None

    if not isinstance(input_value, list):
        return [], None

    system_parts = []
    gemini_msgs = []
    for item in input_value:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        role = item.get("role", "user")
        text = _content_to_text(item.get("content"))

        # Responses output messages can be replayed as input items.
        if item_type == "message" and role == "assistant":
            role = "assistant"
        elif item_type == "function_call_output":
            role = "user"
            text = _content_to_text(item.get("output") or item.get("content"))
        elif item_type == "function_call":
            # Jaika does not execute function calls in the compat layer.
            continue

        if role in ("system", "developer"):
            if text:
                system_parts.append(text)
            continue

        if text:
            gemini_msgs.append({
                "role": "model" if role == "assistant" else "user",
                "text": text,
            })

    return gemini_msgs, "\n".join(system_parts) if system_parts else None


def _responses_system_instruction(data, input_system):
    parts = []
    if data.get("instructions"):
        parts.append(str(data["instructions"]))
    if input_system:
        parts.append(input_system)

    text_format = (data.get("text") or {}).get("format") if isinstance(data.get("text"), dict) else None
    if isinstance(text_format, dict) and text_format.get("type") == "json_schema":
        schema = text_format.get("schema")
        name = text_format.get("name") or "response"
        parts.append(
            "Return only valid JSON matching the requested Structured Outputs schema "
            f"named {name}. Schema: {json.dumps(schema, separators=(',', ':'))}"
        )

    return "\n\n".join(p for p in parts if p) or None


def _responses_grounding_enabled(data):
    if data.get("grounding") is True:
        return True
    tools = data.get("tools") or []
    return any(isinstance(t, dict) and str(t.get("type", "")).startswith("web_search") for t in tools)


def _response_message_item(text):
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _response_object(response_id, model, output_text, output=None, status="completed"):
    output = output if output is not None else [_response_message_item(output_text)]
    now = int(time.time())
    return {
        "id": response_id,
        "object": "response",
        "created_at": now,
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": model,
        "output": output,
        "output_text": output_text,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": None,
        "store": True,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "truncation": "disabled",
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }


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
    model = _resolve_model(data.get("model", "")) or "gemini-3.5-flash-low"

    gemini_msgs, system_instruction = _openai_messages_to_gemini(messages)

    if not gemini_msgs:
        return jsonify({"error": {"message": "No messages provided", "type": "invalid_request_error"}}), 400

    # Input guardrails
    last_text = gemini_msgs[-1].get("text", "") if gemini_msgs else ""
    if last_text:
        is_safe, safety_msg = check_input_guardrails(last_text)
        if not is_safe:
            return jsonify({"error": {"message": safety_msg, "type": "invalid_request_error"}}), 400

    grounding = bool(data.get("grounding", False))

    if do_stream:
        def _gen():
            chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            ts = int(time.time())
            opening = {
                "id": chunk_id, "object": "chat.completion.chunk",
                "created": ts, "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(opening)}\n\n"

            for raw in stream_generate(uid, gemini_msgs, system_instruction=system_instruction,
                                       grounding=grounding, requested_model=model):
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
    result = generate(uid, gemini_msgs, system_instruction=system_instruction,
                      grounding=grounding, requested_model=model)
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


@compat_bp.route("/v1/responses", methods=["POST"])
def openai_responses():
    """OpenAI Responses-compatible text endpoint.

    Supports text/message-item input, instructions, previous_response_id,
    store=false, streaming text deltas, and web_search → Jaika grounding.
    """
    uid, err = _require_user()
    if err:
        return err

    data = request.get_json(force=True)
    model = _resolve_model(data.get("model", "")) or "gemini-3.5-flash-low"
    do_stream = bool(data.get("stream", False))
    response_id = f"resp_{uuid.uuid4().hex[:24]}"

    gemini_msgs, input_system = _responses_input_to_gemini(data.get("input", ""))
    previous_response_id = data.get("previous_response_id")
    previous_state = _load_response_state(uid, previous_response_id) if previous_response_id else None

    if previous_state:
        previous_msgs = previous_state.get("gemini_msgs") or []
        if isinstance(previous_msgs, list):
            gemini_msgs = previous_msgs + gemini_msgs

    system_instruction = _responses_system_instruction(data, input_system)

    if not gemini_msgs:
        return jsonify({"error": {"message": "No input provided", "type": "invalid_request_error"}}), 400

    last_text = gemini_msgs[-1].get("text", "") if gemini_msgs else ""
    if last_text:
        is_safe, safety_msg = check_input_guardrails(last_text)
        if not is_safe:
            return jsonify({"error": {"message": safety_msg, "type": "invalid_request_error"}}), 400

    grounding = _responses_grounding_enabled(data)
    store = data.get("store", True) is not False

    if do_stream:
        def _gen():
            created = _response_object(response_id, model, "", output=[])
            created["previous_response_id"] = previous_response_id
            created["store"] = store
            yield f"event: response.created\ndata: {json.dumps(created)}\n\n"

            output_index = 0
            item_id = f"msg_{uuid.uuid4().hex[:24]}"
            full_text = []
            item = {
                "id": item_id,
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            }
            yield f"event: response.output_item.added\ndata: {json.dumps({'type':'response.output_item.added','output_index':output_index,'item':item})}\n\n"
            yield f"event: response.content_part.added\ndata: {json.dumps({'type':'response.content_part.added','item_id':item_id,'output_index':output_index,'content_index':0,'part':{'type':'output_text','text':'','annotations':[]}})}\n\n"

            for raw in stream_generate(uid, gemini_msgs, system_instruction=system_instruction,
                                       grounding=grounding, requested_model=model):
                if not raw.startswith("data: "):
                    continue
                try:
                    d = json.loads(raw[6:])
                except json.JSONDecodeError:
                    continue
                if "text" in d:
                    delta_text = d["text"]
                    full_text.append(delta_text)
                    event = {
                        "type": "response.output_text.delta",
                        "item_id": item_id,
                        "output_index": output_index,
                        "content_index": 0,
                        "delta": delta_text,
                    }
                    yield f"event: response.output_text.delta\ndata: {json.dumps(event)}\n\n"
                elif d.get("type") == "done":
                    output_text = "".join(full_text)
                    final_output = [_response_message_item(output_text)]
                    completed = _response_object(response_id, model, output_text, output=final_output)
                    completed["previous_response_id"] = previous_response_id
                    completed["store"] = store
                    completed["tools"] = data.get("tools") or []
                    if store:
                        saved_msgs = gemini_msgs + [{"role": "model", "text": output_text}]
                        _save_response_state(uid, response_id, {
                            "response_id": response_id,
                            "model": model,
                            "created_at": int(time.time()),
                            "gemini_msgs": saved_msgs,
                            "output_text": output_text,
                        })
                    done_text = {
                        "type": "response.output_text.done",
                        "item_id": final_output[0]["id"],
                        "output_index": output_index,
                        "content_index": 0,
                        "text": output_text,
                    }
                    yield f"event: response.output_text.done\ndata: {json.dumps(done_text)}\n\n"
                    yield f"event: response.completed\ndata: {json.dumps(completed)}\n\n"
                    yield "data: [DONE]\n\n"

        return Response(
            stream_with_context(_gen()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    result = generate(uid, gemini_msgs, system_instruction=system_instruction,
                      grounding=grounding, requested_model=model)
    if "error" in result:
        return jsonify({"error": {"message": result["error"], "type": "api_error"}}), 502

    output_text = result.get("text", "")
    output = [_response_message_item(output_text)]
    response_obj = _response_object(response_id, model, output_text, output=output)
    response_obj["previous_response_id"] = previous_response_id
    response_obj["store"] = store
    response_obj["tools"] = data.get("tools") or []
    if isinstance(data.get("text"), dict):
        response_obj["text"] = data["text"]

    if store:
        saved_msgs = gemini_msgs + [{"role": "model", "text": output_text}]
        _save_response_state(uid, response_id, {
            "response_id": response_id,
            "model": model,
            "created_at": int(time.time()),
            "gemini_msgs": saved_msgs,
            "output_text": output_text,
        })

    return jsonify(response_obj)


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
    model = _resolve_model(data.get("model", "")) or "gemini-3.5-flash-low"

    gemini_msgs, system_instruction = _anthropic_messages_to_gemini(messages, system)

    if not gemini_msgs:
        return jsonify({"error": {"type": "invalid_request_error", "message": "No messages provided"}}), 400

    # Input guardrails
    last_text = gemini_msgs[-1].get("text", "") if gemini_msgs else ""
    if last_text:
        is_safe, safety_msg = check_input_guardrails(last_text)
        if not is_safe:
            return jsonify({"type": "error", "error": {"type": "invalid_request_error", "message": safety_msg}}), 400

    grounding = bool(data.get("grounding", False))
    msg_id = f"msg_{uuid.uuid4().hex[:12]}"

    if do_stream:
        def _gen():
            ts = int(time.time())
            yield f"event: message_start\ndata: {json.dumps({'type':'message_start','message':{'id':msg_id,'type':'message','role':'assistant','content':[],'model':model,'stop_reason':None,'usage':{'input_tokens':0,'output_tokens':0}}})}\n\n"
            yield f"event: content_block_start\ndata: {json.dumps({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})}\n\n"

            for raw in stream_generate(uid, gemini_msgs, system_instruction=system_instruction,
                                       grounding=grounding, requested_model=model):
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

    result = generate(uid, gemini_msgs, system_instruction=system_instruction,
                      grounding=grounding, requested_model=model)
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

    # parse model and action from path e.g. "gemini-3.5-flash-low:generateContent"
    if ":" in model_action:
        model_name, action = model_action.rsplit(":", 1)
    else:
        model_name, action = model_action, "generateContent"
    model = _resolve_model(model_name)

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

    grounding = bool(data.get("grounding", False))

    if do_stream:
        def _gen():
            for raw in stream_generate(uid, gemini_msgs, system_instruction=system_instruction,
                                       grounding=grounding, requested_model=model):
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

    result = generate(uid, gemini_msgs, system_instruction=system_instruction,
                      grounding=grounding, requested_model=model)
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
