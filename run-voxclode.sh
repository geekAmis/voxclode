#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export HF_HOME="${HF_HOME:-$ROOT/.cache/huggingface}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$ROOT/.cache/modelscope}"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Окружение не найдено: $ROOT/.venv" >&2
  echo "Создайте его: python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

exec "$ROOT/.venv/bin/python" "$ROOT/voxclode.py" "$@"
