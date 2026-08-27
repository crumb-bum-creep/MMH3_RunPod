#!/usr/bin/env bash
set -Eeuo pipefail

PASS=0
WARN=0
FAIL=0

ok(){ echo "PASS  $*"; PASS=$((PASS+1)); }
warn(){ echo "WARN  $*"; WARN=$((WARN+1)); }
bad(){ echo "FAIL  $*"; FAIL=$((FAIL+1)); }

echo "=== MMH3 FIRST-BOOT CHECK ==="
echo

echo "--- services ---"
for spec in "7860:Phone UI" "8188:ComfyUI" "8888:Jupyter"; do
  port="${spec%%:*}"; name="${spec#*:}"
  if curl -fsS --max-time 3 "http://127.0.0.1:${port}" >/dev/null 2>&1; then ok "$name responding on $port"; else warn "$name not responding on $port yet"; fi
done

echo
echo "--- hardware ---"
GPU="$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | head -1 || true)"
if [[ -n "$GPU" ]]; then ok "GPU visible: $GPU"; else bad "nvidia-smi did not return a GPU"; fi

LIMIT=""
if [[ -f /sys/fs/cgroup/memory.max ]]; then LIMIT="$(cat /sys/fs/cgroup/memory.max)"; fi
if [[ -z "$LIMIT" && -f /sys/fs/cgroup/memory/memory.limit_in_bytes ]]; then LIMIT="$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes)"; fi
if [[ -n "$LIMIT" && "$LIMIT" != "max" ]]; then
  if python - "$LIMIT" <<'PY'
import sys
n=int(sys.argv[1])
print(f"Container RAM limit: {n/2**30:.1f} GiB")
raise SystemExit(0 if n >= 100*2**30 else 2)
PY
  then
    ok "container RAM is >= 100 GiB"
  else
    warn "container RAM is below the intended 100 GiB target"
  fi
else
  warn "finite cgroup RAM limit not detected"
fi

echo
echo "--- secrets ---"
for v in OPENROUTER_API_KEY HF_TOKEN CIVITAI_TOKEN JUPYTER_TOKEN; do
  if [[ -n "${!v:-}" ]]; then ok "$v is configured"; else warn "$v is not configured"; fi
done

echo
echo "--- image/runtime ---"
if [[ -f /ComfyUI/main.py ]]; then ok "/ComfyUI/main.py exists"; else bad "/ComfyUI/main.py missing"; fi
if [[ -x /opt/mmh3/runtime/entrypoint.sh ]]; then ok "MMH3 entrypoint installed"; else bad "MMH3 entrypoint missing"; fi
if command -v mmh3 >/dev/null 2>&1; then ok "mmh3 helper command installed"; else bad "mmh3 helper missing"; fi

COMFY_SHA="$(git -C /ComfyUI rev-parse HEAD 2>/dev/null || true)"
if [[ "$COMFY_SHA" == "c2bcbecd82ec5ae66594340b395c24ef0217b238" ]]; then ok "ComfyUI commit pinned correctly"; else warn "ComfyUI commit is $COMFY_SHA"; fi

WF_COUNT="$(find /opt/mmh3/workflows/api -maxdepth 1 -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
if [[ "$WF_COUNT" == "6" ]]; then ok "six canonical workflows bundled"; else bad "expected 6 canonical workflows, found $WF_COUNT"; fi

echo
echo "--- provisioning ---"
STATE=/workspace/mmh3/state/provisioning.json
if [[ -f "$STATE" ]]; then
  python - "$STATE" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print("status:",d.get("status"))
print("stage:",d.get("stage"))
print("core_ready:",d.get("core_ready"))
print("message:",d.get("message"))
PY
  if grep -q '"core_ready": true' "$STATE"; then ok "core H3 models report ready"; else warn "core H3 models are still provisioning or degraded"; fi
else
  warn "provisioning state file not created yet"
fi

echo
echo "--- Comfy node classes ---"
if curl -fsS --max-time 8 http://127.0.0.1:8188/object_info >/tmp/mmh3_object_info.json 2>/dev/null; then
  if python - <<'PY'
import json
d=json.load(open("/tmp/mmh3_object_info.json"))
required=[
 "MiniMaxH3ImageToVideo","MiniMaxH3ReferenceToVideo","MiniMaxH3ReferencePack",
 "Power Lora Loader (rgthree)","VHS_VideoCombine","ModelPreviewOverrideKJ",
 "OpenRouterNode"
]
missing=[x for x in required if x not in d]
print("Required classes present:", len(required)-len(missing), "/", len(required))
if missing: print("Missing:", ", ".join(missing))
raise SystemExit(1 if missing else 0)
PY
  then
    ok "required H3/custom-node classes loaded"
  else
    bad "one or more required node classes are missing"
  fi
else
  warn "Comfy object_info unavailable; try again after Comfy finishes starting"
fi
rm -f /tmp/mmh3_object_info.json

echo
echo "=== RESULT ==="
echo "PASS=$PASS WARN=$WARN FAIL=$FAIL"
if [[ $FAIL -gt 0 ]]; then
  echo "First boot has blocking failures."
  exit 1
elif [[ $WARN -gt 0 ]]; then
  echo "No blocking failures; warnings may simply mean first-boot provisioning is still in progress."
  exit 0
else
  echo "MMH3 first boot looks healthy."
fi
