#!/usr/bin/env bash
set -Eeuo pipefail

export PATH="/opt/venv/bin:$PATH"
export PYTHONPATH="/opt/mmh3/runtime${PYTHONPATH:+:$PYTHONPATH}"
export MMH3_CONTAINER_START_EPOCH="${MMH3_CONTAINER_START_EPOCH:-$(date +%s.%N)}"

mkdir -p /workspace/mmh3/{config,data,state,logs} /workspace/ComfyUI/{models,user,input,output}

# Keep the d2103 production phone server intact as server_legacy.py and layer the
# new library/memory behavior around it. This makes the rollback point explicit
# while allowing development to continue from that exact known-good server.
PHONE_ROOT="/opt/mmh3/services/phone-ui"
if [[ -f "$PHONE_ROOT/server_v2.py" && ! -f "$PHONE_ROOT/server_legacy.py" ]]; then
  mv "$PHONE_ROOT/server.py" "$PHONE_ROOT/server_legacy.py"
  cp "$PHONE_ROOT/server_v2.py" "$PHONE_ROOT/server.py"
fi

TCMALLOC="$(ldconfig -p | grep -Po 'libtcmalloc.so.\d' | head -n1 || true)"
if [[ -n "$TCMALLOC" ]]; then export LD_PRELOAD="$TCMALLOC"; fi

python -m mmh3.bootstrap || {
  echo "[mmh3] bootstrap reported an error; keeping pod alive in degraded mode" >&2
}

exec python -m mmh3.supervisor
