#!/usr/bin/env bash
# Run the HTTP parity suite against the Python/Flask server (server/python).
#
#   bash tests/parity/run_parity.sh [python]
#
# It starts two instances of the server against stripe-mock, both with a dummy
# test key: one without STRIPE_WEBHOOK_SECRET (BASE_URL) and one with a known test
# secret (SIGNED_BASE_URL). It then runs `pytest tests/parity` and stops them.
# To test a server you started yourself, run pytest directly (see README.md).
#
# stripe-mock: if STRIPE_MOCK_URL is set, that instance is used. Otherwise a local
# `stripe-mock` binary on PATH is started on free ports; without one, a stripe-mock
# already listening on http://localhost:12111 is assumed (e.g. a CI service container).
#
# Optional environment:
#   STRIPE_MOCK_URL        existing stripe-mock (default: start one / localhost:12111)
#   PARITY_PORT            port of the unsigned server (default 4243, or a free one)
#   PARITY_SIGNED_PORT     port of the signed-webhook server (default 4244, or a free one)
#   PARITY_PYTEST_ARGS     extra pytest arguments, e.g. "-k webhook -v"
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

TARGET="${1:-python}"
if [[ "$TARGET" != "python" || $# -gt 1 ]]; then
  echo "usage: $0 [python]" >&2
  exit 2
fi

# Dummy credentials: stripe-mock accepts any well-formed test key. Not real secrets.
SECRET_KEY="sk_test_paritysuite0000"
PARITY_WEBHOOK_SECRET="whsec_paritysuitetestsecret"

if [[ -x server/python/.venv/bin/python ]]; then
  PY="server/python/.venv/bin/python"
else
  PY="python3"
fi
# Absolute, because the server is started from its own directory.
if [[ "$PY" == */* ]]; then
  PY_ABS="$(cd "$(dirname "$PY")" && pwd)/$(basename "$PY")"
else
  PY_ABS="$(command -v "$PY" || true)"
fi
if [[ -z "$PY_ABS" ]]; then
  echo "error: $PY not found" >&2
  exit 1
fi
if ! "$PY_ABS" -c "import pytest, requests" 2>/dev/null; then
  echo "error: $PY lacks pytest/requests; run: pip install -r server/python/requirements-dev.txt" >&2
  exit 1
fi

LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/parity.XXXXXX")"
PIDS=()

cleanup() {
  local pid
  for pid in "${PIDS[@]:-}"; do
    [[ -n "$pid" ]] || continue
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  PIDS=()
  rm -rf "$LOG_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# Print $1 if nothing listens on it and it can be bound, else a free port.
pick_port() {
  "$PY_ABS" - "$1" <<'PYEOF'
import socket
import sys

preferred = int(sys.argv[1])


def bindable(port: int) -> bool:
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    try:
        with socket.socket() as sock:
            # Servers set SO_REUSEADDR, so TIME_WAIT sockets do not make a port busy.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", port))
    except OSError:
        return False
    return True


if bindable(preferred):
    print(preferred)
else:
    with socket.socket() as sock:
        sock.bind(("", 0))
        print(sock.getsockname()[1])
PYEOF
}

# wait_for_url URL PID LOG [CURL_ARGS...]: poll until URL answers 2xx or PID exits.
wait_for_url() {
  local url="$1" pid="$2" log="$3"
  shift 3
  local i
  for ((i = 0; i < 120; i++)); do
    if curl -fsS -o /dev/null "$@" "$url" 2>/dev/null; then
      return 0
    fi
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
      echo "error: process for $url exited early; log:" >&2
      cat "$log" >&2 || true
      return 1
    fi
    sleep 0.25
  done
  echo "error: timed out waiting for $url; log:" >&2
  cat "$log" >&2 || true
  return 1
}

# --- stripe-mock ----------------------------------------------------------------
if [[ -n "${STRIPE_MOCK_URL:-}" ]]; then
  echo "Using stripe-mock at $STRIPE_MOCK_URL"
elif command -v stripe-mock >/dev/null 2>&1; then
  MOCK_HTTP_PORT="$(pick_port 12111)"
  MOCK_HTTPS_PORT="$(pick_port 12112)"
  [[ "$MOCK_HTTPS_PORT" != "$MOCK_HTTP_PORT" ]] || MOCK_HTTPS_PORT="$(pick_port 0)"
  STRIPE_MOCK_URL="http://localhost:$MOCK_HTTP_PORT"
  echo "Starting stripe-mock on $STRIPE_MOCK_URL"
  stripe-mock -http-port "$MOCK_HTTP_PORT" -https-port "$MOCK_HTTPS_PORT" \
    >"$LOG_DIR/stripe-mock.log" 2>&1 &
  MOCK_PID=$!
  PIDS+=("$MOCK_PID")
  wait_for_url "$STRIPE_MOCK_URL/v1/balance" "$MOCK_PID" "$LOG_DIR/stripe-mock.log" \
    -u "$SECRET_KEY:"
else
  STRIPE_MOCK_URL="http://localhost:12111"
  echo "No stripe-mock binary on PATH; assuming one listens at $STRIPE_MOCK_URL"
fi
STRIPE_MOCK_URL="${STRIPE_MOCK_URL%/}"
if ! curl -fsS -o /dev/null -u "$SECRET_KEY:" "$STRIPE_MOCK_URL/v1/balance"; then
  echo "error: stripe-mock is not reachable at $STRIPE_MOCK_URL" >&2
  exit 1
fi
export STRIPE_MOCK_URL

# --- servers --------------------------------------------------------------------
# start_server PORT WEBHOOK_SECRET LOG: start one server instance in the background.
# Empty values are exported on purpose: dotenv never overrides variables that are
# already set, so a developer's server/python/.env cannot change the tested config.
start_server() {
  local port="$1" webhook_secret="$2" log="$3"
  local -a env_vars=(
    "STRIPE_SECRET_KEY=$SECRET_KEY"
    "STRIPE_API_BASE=$STRIPE_MOCK_URL"
    "STRIPE_WEBHOOK_SECRET=$webhook_secret"
    "PORT=$port"
    "DOMAIN="
    "STATIC_DIR="
  )
  (cd server/python && exec env "${env_vars[@]}" PYTHONUNBUFFERED=1 \
    "$PY_ABS" server.py) >"$log" 2>&1 &
  LAST_PID=$!
  PIDS+=("$LAST_PID")
}

port="$(pick_port "${PARITY_PORT:-4243}")"
signed_port="$(pick_port "${PARITY_SIGNED_PORT:-4244}")"
[[ "$signed_port" != "$port" ]] || signed_port="$(pick_port 0)"

echo
echo "=== python: unsigned on :$port, signed on :$signed_port (stripe-mock $STRIPE_MOCK_URL)"
start_server "$port" "" "$LOG_DIR/python.log"
pid=$LAST_PID
start_server "$signed_port" "$PARITY_WEBHOOK_SECRET" "$LOG_DIR/python-signed.log"
signed_pid=$LAST_PID

wait_for_url "http://localhost:$port/health" "$pid" "$LOG_DIR/python.log"
wait_for_url "http://localhost:$signed_port/health" "$signed_pid" "$LOG_DIR/python-signed.log"

STATUS=0
# shellcheck disable=SC2086 # PARITY_PYTEST_ARGS is intentionally word-split
BASE_URL="http://localhost:$port" \
  SIGNED_BASE_URL="http://localhost:$signed_port" \
  PARITY_WEBHOOK_SECRET="$PARITY_WEBHOOK_SECRET" \
  PARITY_REQUIRE_SIGNED=1 \
  "$PY_ABS" -m pytest tests/parity ${PARITY_PYTEST_ARGS:-} || STATUS=$?

if [[ "$STATUS" -ne 0 ]]; then
  echo "--- server logs" >&2
  cat "$LOG_DIR/python.log" "$LOG_DIR/python-signed.log" >&2 || true
fi
exit "$STATUS"
