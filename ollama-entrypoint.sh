#!/bin/sh
# Start Ollama, then pull the configured model once the server is ready.
# The model is cached in the ollama_data volume, so subsequent starts are a
# fast no-op. OLLAMA_MODEL defaults to llama3.
set -e

ollama serve &
SERVE_PID=$!

echo "[entrypoint] waiting for Ollama to accept connections..."
# The image has no curl/wget; the ollama CLI talks to the local server, so use
# it as the readiness probe.
until ollama list >/dev/null 2>&1; do
  sleep 1
done

MODEL="${OLLAMA_MODEL:-llama3}"
echo "[entrypoint] ensuring model present: $MODEL"
# Pull in the foreground so logs show progress; tolerate transient failures so
# the server stays up either way (it will retry on next container start).
ollama pull "$MODEL" || echo "[entrypoint] WARNING: pull of '$MODEL' failed; server still running"
echo "[entrypoint] model ready: $MODEL"

wait "$SERVE_PID"
