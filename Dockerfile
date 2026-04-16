# python 3.12.3설치, cuda128, comfyui 설치됨
# network volume에 custom_nodes, models , input, output에 이미지 하나 저장됨
# /workspace/runpod-slim/ComfyUI/models
# /workspace/runpod-slim/ComfyUI/custom_nodes
# /workspace/runpod-slim/ComfyUI/output
# /workspace/runpod-slim/ComfyUI/input
FROM runpod/comfyui:1.4.0-cuda13.0


ENV DEBIAN_FRONTEND=noninteractive \
    PIP_PREFER_BINARY=1 \
    PYTHONUNBUFFERED=1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /

# apt 패키지 설치
RUN apt-get update && apt-get install -y \
    python3-dev \
    python3-pip \
    git \
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
    rm -rf /var/lib/apt/lists/*

# Python 패키지 설치 (시스템 pip 사용)
RUN pip3 install requests runpod websocket-client

# Handler 복사
COPY rp_handler.py /rp_handler.py

# start.sh 복사 (Network Volume 링크 + ComfyUI 실행 + handler 실행)
COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/bin/bash", "/start.sh"]
