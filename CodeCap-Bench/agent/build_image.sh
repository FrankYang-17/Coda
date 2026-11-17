#!/usr/bin/env bash
# 构建 agent/docker 镜像。用法：bash agent/build_image.sh [tag] [额外 docker build 参数...]
set -euo pipefail

agent_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
tag="${1:-video2code-env:latest}"
shift || true

proxy_args=()
for name in http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY; do
  if [[ -n "${!name:-}" ]]; then
    proxy_args+=(--build-arg "$name=${!name}")
  fi
done

exec docker build --network host "${proxy_args[@]}" -t "$tag" "$@" "$agent_dir/docker"
