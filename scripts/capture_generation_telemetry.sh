#!/usr/bin/env bash
set -Eeuo pipefail

TS="$(date +%Y%m%d_%H%M%S)"
OUT="${1:-/workspace/mmh3_telemetry_${TS}}"
mkdir -p "$OUT"
CSV="$OUT/telemetry.csv"
EVENTS="$OUT/events.txt"

queue_counts() {
python3 - <<'PY'
import json,urllib.request
try:
    q=json.load(urllib.request.urlopen("http://127.0.0.1:8188/queue",timeout=2))
    print(len(q.get("queue_running",[])),len(q.get("queue_pending",[])))
except Exception:
    print("0 0")
PY
}

cgfile() {
  local v2="/sys/fs/cgroup/$1"
  local v1="/sys/fs/cgroup/memory/$2"
  if [ -f "$v2" ]; then cat "$v2"
  elif [ -f "$v1" ]; then cat "$v1"
  else echo ""
  fi
}

echo 'timestamp,elapsed_s,queue_running,queue_pending,gpu_mem_used_mib,gpu_mem_total_mib,gpu_util_pct,gpu_power_w,cgroup_current_bytes,cgroup_peak_bytes,comfy_rss_kib,comfy_vmsize_kib,mem_available_kib' > "$CSV"

echo "$(date -Is) telemetry monitor started" >> "$EVENTS"
echo "Waiting for a ComfyUI generation to start. Queue a normal MMH3 job now."
echo "Preferably use a representative/heavy job you actually care about; 15s R2V at your normal MP setting is ideal."

START="$(date +%s)"
SEEN=0
EMPTY_AFTER=0
MAX_WAIT=1800

while true; do
  NOW="$(date +%s)"; ELAPSED=$((NOW-START))
  read -r QR QP <<< "$(queue_counts)"

  if [ "${QR:-0}" -gt 0 ]; then
    if [ "$SEEN" -eq 0 ]; then SEEN=1; echo "$(date -Is) generation detected" >> "$EVENTS"; fi
    EMPTY_AFTER=0
  elif [ "$SEEN" -eq 1 ]; then EMPTY_AFTER=$((EMPTY_AFTER+1)); fi

  GPU="$(nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu,power.draw --format=csv,noheader,nounits 2>/dev/null | head -1 || echo ',,,')"
  IFS=',' read -r GMU GMT GU GP <<< "$GPU"
  GMU="$(echo "$GMU"|xargs)"; GMT="$(echo "$GMT"|xargs)"; GU="$(echo "$GU"|xargs)"; GP="$(echo "$GP"|xargs)"

  CGCUR="$(cgfile memory.current memory.usage_in_bytes)"
  CGPEAK="$(cgfile memory.peak memory.max_usage_in_bytes)"

  PID="$(pgrep -f '[p]ython.*[/ ]ComfyUI/main.py|[p]ython.*main.py.*--listen' | head -1 || true)"
  RSS=""; VSZ=""
  if [ -n "${PID:-}" ] && [ -r "/proc/$PID/status" ]; then
    RSS="$(awk '/^VmRSS:/{print $2}' /proc/$PID/status)"
    VSZ="$(awk '/^VmSize:/{print $2}' /proc/$PID/status)"
  fi
  MAV="$(awk '/^MemAvailable:/{print $2}' /proc/meminfo 2>/dev/null || true)"

  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n'     "$(date -Is)" "$ELAPSED" "${QR:-0}" "${QP:-0}" "${GMU:-}" "${GMT:-}" "${GU:-}" "${GP:-}"     "${CGCUR:-}" "${CGPEAK:-}" "${RSS:-}" "${VSZ:-}" "${MAV:-}" >> "$CSV"

  if [ "$SEEN" -eq 1 ] && [ "$EMPTY_AFTER" -ge 30 ]; then echo "$(date -Is) queue remained empty for 30s; capture complete" >> "$EVENTS"; break; fi
  if [ "$ELAPSED" -ge "$MAX_WAIT" ]; then echo "$(date -Is) timeout reached" >> "$EVENTS"; break; fi
  sleep 1
done

python3 - "$CSV" > "$OUT/summary.txt" <<'PY'
import csv,sys
rows=list(csv.DictReader(open(sys.argv[1])))
def nums(k):
    out=[]
    for r in rows:
        try: out.append(float(r[k]))
        except: pass
    return out
def report(k,label,unit=""):
    x=nums(k)
    if x: print(f"{label}: min={min(x):.2f}{unit} max={max(x):.2f}{unit}")
print("MMH3 generation telemetry summary")
report("gpu_mem_used_mib","GPU memory used"," MiB")
report("gpu_util_pct","GPU utilization","%")
report("gpu_power_w","GPU power"," W")
report("cgroup_current_bytes","Container memory current"," bytes")
report("cgroup_peak_bytes","Container memory peak"," bytes")
report("comfy_rss_kib","Comfy RSS"," KiB")
print("samples:",len(rows))
PY

tar -C "$(dirname "$OUT")" -czf "${OUT}.tar.gz" "$(basename "$OUT")"
echo
echo "=== TELEMETRY COMPLETE ==="
cat "$OUT/summary.txt"
echo
echo "${OUT}.tar.gz"
echo "Upload that .tar.gz to ChatGPT."
