#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$repo_dir/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  echo 'Python environment missing. Run bash scripts/setup.sh.' >&2
  exit 1
fi
exec "$python_bin" -I -B "$repo_dir/agent/run.py" "$@"
