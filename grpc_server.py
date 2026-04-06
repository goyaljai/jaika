"""Jaika gRPC server — bidirectional streaming chat for admin users.

Runs alongside Flask on port 5245. Admin-only access.
Uses Gemini CLI subprocess for AI responses.
"""

import grpc
from concurrent import futures
import logging
import os
import sys
import time

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(__file__))

import chat_pb2
import chat_pb2_grpc
from auth import load_token, is_admin
from gemini import _setup_cli_creds
from sessions import create_session, add_message, get_conversation_history
import subprocess

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Load env
from dotenv import load_dotenv
load_dotenv()


class JaikaChatServicer(chat_pb2_grpc.JaikaChatServicer):
    """Bidirectional streaming chat service.

    Client sends ChatMessage stream, server responds with ChatResponse stream.
    Each message from the client triggers an AI response from the server.
    Admin-only: non-admin users get an error response.
    """

    def Chat(self, request_iterator, context):
        """Handle bidirectional streaming chat.

        For each incoming ChatMessage:
        1. Verify user is admin
        2. Get or create session
        3. Run Gemini CLI with conversation history
        4. Stream back the response
        """
        session_id = None
        user_id = None

        for message in request_iterator:
            # First message sets the user context
            if user_id is None:
                user_id = message.user_id
                if not user_id or not load_token(user_id):
                    yield chat_pb2.ChatResponse(
                        text="Authentication failed. Invalid user_id.",
                        status="error"
                    )
                    return

                if not is_admin(user_id):
                    yield chat_pb2.ChatResponse(
                        text="gRPC chat is admin-only.",
                        status="error"
                    )
                    return

                log.info("gRPC chat started for admin user %s", user_id)

            # Use provided session_id or create one
            if message.session_id:
                session_id = message.session_id
            elif session_id is None:
                sess = create_session(user_id, title="gRPC Chat")
                session_id = sess["id"]

            # Save user message
            add_message(user_id, session_id, "user", message.text)

            # Signal start
            yield chat_pb2.ChatResponse(
                session_id=session_id,
                status="start"
            )

            # Get conversation history and build prompt
            history = get_conversation_history(user_id, session_id)
            parts = []
            for msg in history:
                if msg.get("text"):
                    role = "User" if msg["role"] == "user" else "Assistant"
                    parts.append(f"{role}: {msg['text']}")
            prompt = "\n".join(parts)

            # Run Gemini CLI
            home_dir, env = _setup_cli_creds(user_id)
            if not home_dir:
                yield chat_pb2.ChatResponse(
                    text="CLI credentials not found.",
                    status="error"
                )
                continue

            try:
                result = subprocess.run(
                    ["gemini", "--prompt", prompt],
                    capture_output=True, text=True, timeout=120,
                    env=env, cwd=home_dir,
                )
                output = result.stdout.strip()
                lines = [l for l in output.split("\n")
                         if not l.startswith("Keychain")
                         and not l.startswith("Using FileKeychain")]
                text = "\n".join(lines).strip()

                if text:
                    # Save AI response
                    add_message(user_id, session_id, "model", text)

                    # Stream response in chunks (simulate streaming)
                    words = text.split()
                    chunk = []
                    for i, word in enumerate(words):
                        chunk.append(word)
                        if len(chunk) >= 10 or i == len(words) - 1:
                            yield chat_pb2.ChatResponse(
                                text=" ".join(chunk),
                                session_id=session_id,
                                status="chunk"
                            )
                            chunk = []
                            time.sleep(0.05)  # Small delay for streaming feel

                    yield chat_pb2.ChatResponse(
                        session_id=session_id,
                        status="done"
                    )
                else:
                    yield chat_pb2.ChatResponse(
                        text="Empty response from AI.",
                        session_id=session_id,
                        status="error"
                    )

            except subprocess.TimeoutExpired:
                yield chat_pb2.ChatResponse(
                    text="Request timed out.",
                    session_id=session_id,
                    status="error"
                )
            except Exception as e:
                yield chat_pb2.ChatResponse(
                    text=f"Error: {str(e)}",
                    session_id=session_id,
                    status="error"
                )


def serve():
    """Start the gRPC server on port 5245 with TLS."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=5))
    chat_pb2_grpc.add_JaikaChatServicer_to_server(JaikaChatServicer(), server)

    # Insecure port — Tailscale Funnel handles TLS termination
    server.add_insecure_port("[::]:5245")
    log.info("Jaika gRPC server started on port 5245 (Tailscale handles TLS)")

    server.start()
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == "__main__":
    serve()
