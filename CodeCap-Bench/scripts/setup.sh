#!/usr/bin/env bash
# Install Python environments and models after the image build in README.md.
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
set -a
source .env
set +a
unset PYTHONPATH PYTHONHOME
export PYTHONDONTWRITEBYTECODE=1
for executable in uv uvx ffmpeg ffprobe git curl; do
  command -v "$executable" >/dev/null || { echo "Missing dependency: $executable (see README.md)" >&2; exit 1; }
done
uv python install 3.12
for environment in .venv .venv-sam .venv-vjepa; do
  if [[ ! -x "$environment/bin/python" ]]; then
    uv venv --managed-python --python 3.12 "$environment"
  fi
done
mkdir -p third_party models
if [[ ! -d third_party/sam3 ]]; then
  git clone https://github.com/facebookresearch/sam3.git third_party/sam3
fi
git -C third_party/sam3 checkout 2345a4ad109ac29c569da749c91d84f10dc08c40
if [[ ! -d third_party/vjepa2 ]]; then
  git clone https://github.com/facebookresearch/vjepa2.git third_party/vjepa2
fi
git -C third_party/vjepa2 checkout 204698b45b3712590f06245fbfba32d3be539812

uv pip install --python .venv-sam/bin/python torch==2.10.0 torchvision==0.25.0 \
  --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv-sam/bin/python -r eval/object_st_iou/requirements.txt
uv pip install --python .venv-sam/bin/python --no-deps --no-build-isolation -e third_party/sam3
uv pip check --python .venv-sam/bin/python
.venv-sam/bin/python -I -B -c \
  'import torch, torchvision; assert torch.__version__.split("+")[0] == "2.10.0" and torchvision.__version__.split("+")[0] == "0.25.0" and torch.version.cuda == "12.8", "Unexpected SAM PyTorch/CUDA build"; from sam3.model_builder import build_sam3_multiplex_video_predictor; print("SAM import OK")'

uv pip install --python .venv-vjepa/bin/python torch==2.8.0 torchvision==0.23.0 \
  --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv-vjepa/bin/python -r eval/visual_similarity/requirements.txt
uv pip check --python .venv-vjepa/bin/python
.venv-vjepa/bin/python -I -B -c \
  'import sys; sys.path.insert(0, "third_party/vjepa2"); import torch, torchvision; assert torch.__version__.split("+")[0] == "2.8.0" and torchvision.__version__.split("+")[0] == "0.23.0" and torch.version.cuda == "12.8", "Unexpected V-JEPA PyTorch/CUDA build"; from app.vjepa_2_1.models.vision_transformer import vit_gigantic_xformers; from evals.hub.preprocessor import vjepa2_preprocessor; vjepa2_preprocessor(crop_size=384); print("V-JEPA import OK")'

if [[ ! -s models/sam3.1_multiplex.pt ]]; then
  uvx --from huggingface-hub hf download facebook/sam3.1 sam3.1_multiplex.pt \
    --revision daa63191845a41281374e725f4c9e51c7a824460 --local-dir models
fi
if [[ ! -s models/vjepa2_1_vitG_384.pt ]]; then
  curl --fail -L --retry 3 \
    https://dl.fbaipublicfiles.com/vjepa2/vjepa2_1_vitG_384.pt \
    -o models/.vjepa2_1_vitG_384.pt.part
  mv models/.vjepa2_1_vitG_384.pt.part models/vjepa2_1_vitG_384.pt
fi
echo 'Setup complete. Configure the generation API and EVAL_API_KEY, EVAL_BASE_URL, EVAL_MODEL in .env.'
