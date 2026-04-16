# python 3.12.3설치, cuda128, comfyui 설치됨
# network volume에 custom_nodes, models , input, output에 이미지 하나 저장됨
# /workspace/runpod-slim/ComfyUI/models
# /workspace/runpod-slim/ComfyUI/custom_nodes
# /workspace/runpod-slim/ComfyUI/output
# /workspace/runpod-slim/ComfyUI/input
FROM runpod/comfyui:cuda13.0


ENV DEBIAN_FRONTEND=noninteractive \
    PIP_PREFER_BINARY=1 \
    PYTHONUNBUFFERED=1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /workspace

COPY rp_handler.py /workspace/handler.py

# Upgrade apt packages and install required dependencies

RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y \
    python3-dev \
    python3-pip \
    fonts-dejavu-core \
    rsync \
    git \
    jq \
    moreutils \
    aria2 \
    wget \
    curl \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    ffmpeg \
    libgoogle-perftools4 \
    libtcmalloc-minimal4 \
    procps && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/* && \
    apt-get clean

# comfyui 설치하기 (이미 설치가 되어있음)


# Install Worker dependencies
RUN /workspace/runpod-slim/ComfyUI/.venv-cu128/bin/pip install requests runpod websocket-client

# # Add RunPod Handler and Docker container start script
# COPY start.sh rp_handler.py ./

# # Add validation schemas
# COPY schemas /schemas

# # Start the container
# RUN chmod +x start.sh
# # CMD ["/bin/bash", "start.sh"]


CMD ["python", "-u", "/workspace/handler.py"]
