# MMH3 v1 intentionally inherits the exact software family captured from the
# known-good community pod. Everything above CUDA/PyTorch/Comfy is owned here.
FROM hearmeman/comfyui-base:cu130-comfy0.32.0-torch2.11.0

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG DEBIAN_FRONTEND=noninteractive
ARG COMFYUI_COMMIT=c2bcbecd82ec5ae66594340b395c24ef0217b238

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl ffmpeg git jq libtcmalloc-minimal4 procps tini \
    && rm -rf /var/lib/apt/lists/*

RUN git -C /ComfyUI fetch --depth=1 origin "${COMFYUI_COMMIT}" \
    && git -C /ComfyUI reset --hard "${COMFYUI_COMMIT}"

RUN set -eux; \
    install_node() { \
      name="$1"; url="$2"; rev="$3"; dst="/ComfyUI/custom_nodes/$name"; \
      rm -rf "$dst"; \
      git clone --filter=blob:none "$url" "$dst"; \
      git -C "$dst" fetch --depth=1 origin "$rev"; \
      git -C "$dst" checkout --detach "$rev"; \
      if [[ -f "$dst/requirements.txt" ]]; then /opt/venv/bin/pip install --no-cache-dir -r "$dst/requirements.txt"; fi; \
    }; \
    install_node ComfyUI-KJNodes https://github.com/kijai/ComfyUI-KJNodes.git 3f20054214fec9f9234fd3841ae6f1e4287948f6; \
    install_node ComfyUI-MiniMaxRefPack https://github.com/Hearmeman24/ComfyUI-MiniMaxRefPack.git 7012734eabf6f98063d6eaf8ce1f9264ee803664; \
    install_node ComfyUI-OpenRouter-Simple https://github.com/Hearmeman24/ComfyUI-OpenRouter-Simple.git 404b67229dd0f88373d35824ba624cb563e734b3; \
    install_node ComfyUI-Openrouter_node https://github.com/gabe-init/ComfyUI-Openrouter_node.git 45c67f94e335b978577773f05752e17ffe63a09e; \
    install_node ComfyUI-Spectrum-MiniMax-H3 https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3.git ac247efcc2c9b6324fa106b3bd8e148a583db4a9; \
    install_node ComfyUI-VideoHelperSuite https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git 4ee72c065db22c9d96c2427954dc69e7b908444b; \
    install_node rgthree-comfy https://github.com/rgthree/rgthree-comfy.git 6b76ee6f2c5a007710b5a16f97c94330d6ecc871

RUN /opt/venv/bin/pip install --no-cache-dir \
      'PyYAML==6.0.3' 'requests==2.34.2' 'psutil==7.2.2' \
      'huggingface_hub==1.27.0'

COPY runtime /opt/mmh3/runtime
COPY config /opt/mmh3/config
COPY workflows /opt/mmh3/workflows
COPY services /opt/mmh3/services
COPY scripts /opt/mmh3/scripts

RUN chmod +x /opt/mmh3/runtime/entrypoint.sh /opt/mmh3/scripts/mmh3 \
    && ln -sfn /opt/mmh3/scripts/mmh3 /usr/local/bin/mmh3

ENV PYTHONUNBUFFERED=1 \
    MMH3_IMAGE_ROOT=/opt/mmh3 \
    MMH3_WORKSPACE=/workspace \
    MMH3_COMFY_DIR=/ComfyUI \
    MMH3_COMFY_PORT=8188 \
    MMH3_PHONE_UI_PORT=7860 \
    MMH3_JUPYTER_PORT=8888 \
    MMH3_AUTO_DOWNLOAD_MODELS=true \
    MMH3_AUTO_DOWNLOAD_LORAS=true

EXPOSE 7860 8188 8888
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/mmh3/runtime/entrypoint.sh"]
