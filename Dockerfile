# MMH3 Studio — MiniMax H3 on RunPod with a phone-first UI.
#
# Base: the exact image Hearmeman's v9 template builds on (ComfyUI 3dd559d, just
# after 0.36.0; CUDA 13.0; torch 2.11). ComfyUI >= 0.34 sizes its RAM cache
# against the container's cgroup limit instead of the host's RAM, which is the
# root cause of the RAM creep on the previous 0.32 image.
ARG BASE_IMAGE=hearmeman/comfyui-base:cu130-comfy0.36.0-3dd559d8-torch2.11.0
FROM ${BASE_IMAGE}

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libtcmalloc-minimal4 tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Custom nodes, pinned. Validated against this ComfyUI commit with
# scripts/validate_graphs.py (every mode x recipe graph passes ComfyUI's own
# prompt validation). Only what the Studio graphs use:
#   KJNodes              ModelPreviewOverrideKJ (live previews + per-step timing)
#   VideoHelperSuite     VHS_VideoCombine (mp4 with the generated soundtrack)
#   MiniMaxRefPack       R2V reference loading, and the R2V prompt writer
#   Spectrum-MiniMax-H3  optional speed node, not used by default (kept for ComfyUI users)
RUN set -eux; \
    install_node() { \
      name="$1"; url="$2"; rev="$3"; dst="/ComfyUI/custom_nodes/$name"; \
      rm -rf "$dst"; \
      git clone --filter=blob:none "$url" "$dst"; \
      git -C "$dst" checkout --detach "$rev"; \
      if [[ -f "$dst/requirements.txt" ]]; then pip install --no-cache-dir -r "$dst/requirements.txt"; fi; \
      rm -rf "$dst/.git"; \
    }; \
    install_node ComfyUI-KJNodes          https://github.com/kijai/ComfyUI-KJNodes.git                3f20054214fec9f9234fd3841ae6f1e4287948f6; \
    install_node ComfyUI-VideoHelperSuite https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git 4ee72c065db22c9d96c2427954dc69e7b908444b; \
    install_node ComfyUI-MiniMaxRefPack   https://github.com/Hearmeman24/ComfyUI-MiniMaxRefPack.git   7012734eabf6f98063d6eaf8ce1f9264ee803664; \
    install_node ComfyUI-Spectrum-MiniMax-H3 https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3.git ac247efcc2c9b6324fa106b3bd8e148a583db4a9

# SageAttention: the base ships wheels but does not install them. Install at
# build time; the supervisor runs a real kernel probe at boot and only passes
# --use-sage-attention when it works on the pod's GPU.
RUN set -eux; \
    whl="$(ls /opt/sage/cu130/sageattention-*.whl 2>/dev/null | head -n1 || true)"; \
    if [[ -n "$whl" ]]; then pip install --no-cache-dir --no-deps --force-reinstall "$whl"; \
    else echo "no baked SageAttention wheel; ComfyUI will run without it"; fi

# Studio's own dependencies (most already ship with ComfyUI; this only fills gaps).
RUN python - <<'EOF'
import importlib.util, subprocess, sys
need = {"yaml": "PyYAML", "requests": "requests", "aiohttp": "aiohttp", "PIL": "pillow",
        "psutil": "psutil", "huggingface_hub": "huggingface_hub", "hf_xet": "hf_xet"}
missing = [pkg for mod, pkg in need.items() if importlib.util.find_spec(mod) is None]
if missing:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *missing])
EOF

COPY studio  /opt/mmh3/studio
COPY config  /opt/mmh3/config
COPY scripts /opt/mmh3/scripts

ENV MMH3_IMAGE_ROOT=/opt/mmh3 \
    MMH3_COMFY_CODE=/ComfyUI \
    PYTHONPATH=/opt/mmh3 \
    PYTHONUNBUFFERED=1 \
    HF_XET_HIGH_PERFORMANCE=1

# Fail the build, not the pod, if anything is wired wrong.
RUN python -c "import studio.server, studio.supervisor, studio.provision, studio.jobs; print('studio imports ok')" \
 && python -c "import sys; sys.path.insert(0, '/ComfyUI/custom_nodes/ComfyUI-MiniMaxRefPack'); from minimax_refpack import prompt, refs; print('refpack prompt writer ok')" \
 && ffmpeg -version | head -n1 && test -x "$(command -v tini)"

EXPOSE 7860 8188 8888
WORKDIR /workspace
ENTRYPOINT ["tini", "-g", "--"]
CMD ["python", "-m", "studio.supervisor"]
