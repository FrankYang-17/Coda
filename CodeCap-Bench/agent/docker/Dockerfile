# video2code 推理/录制环境
# 构建（需要代理时通过 build-arg 传入，不会写进镜像）：
#   docker build --network host \
#     --build-arg http_proxy=$http_proxy --build-arg https_proxy=$https_proxy \
#     -t video2code-env:latest docker/
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    TZ=UTC

# 系统工具、FFmpeg、字体、Python 及图像库
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils \
        bash coreutils findutils grep sed gawk ripgrep file xxd bc \
        ffmpeg imagemagick \
        fontconfig fonts-liberation fonts-noto-cjk fonts-noto-color-emoji \
        python3 python-is-python3 python3-pil python3-numpy python3-opencv \
    && rm -rf /var/lib/apt/lists/*

# Node.js
ARG NODE_VERSION=24.21.0
RUN curl -fsSL https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
        | tar -xJ -C /opt \
    && mv /opt/node-v${NODE_VERSION}-linux-x64 /opt/node
ENV PATH=/opt/node/bin:$PATH

# Playwright + Chromium，路径与提示词和 /opt/tools 脚本中写死的一致
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/runtime/browser/browsers \
    NODE_PATH=/opt/runtime/browser/node_modules
COPY browser/package.json browser/package-lock.json /opt/runtime/browser/
RUN cd /opt/runtime/browser \
    && npm ci --no-audit --no-fund \
    && npx playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.npm

# Codex CLI
ARG CODEX_VERSION=0.155.1
RUN npm install -g --no-audit --no-fund @openai/codex@${CODEX_VERSION} && rm -rf /root/.npm

# pi / Claude Code
ARG PI_VERSION=1.1.0
ARG CLAUDE_CODE_VERSION=2.1.296
RUN npm install -g --no-audit --no-fund \
        @earendil-works/pi-coding-agent@${PI_VERSION} \
        @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && rm -rf /root/.npm

COPY tools/ /opt/tools/

WORKDIR /workspace
CMD ["bash"]
