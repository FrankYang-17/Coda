#!/usr/bin/env python3
"""Evaluate visual similarity for one reference/generated pair with V-JEPA 2.1 ViT-G."""
from contextlib import nullcontext
import importlib.util
import json
import math
import os
from pathlib import Path
import sys

# User-configured upstream code remains read-only, including Python bytecode.
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
SOURCE_FPS = 24
SAMPLE_STEP = 12
MAX_WINDOW_SECONDS = 8


def windows(frame_count):
    """Partition whole-second 24-fps input into balanced windows of at most 8s."""
    if type(frame_count) is not int or frame_count < SOURCE_FPS or frame_count % SOURCE_FPS:
        raise ValueError('V-JEPA requires a positive whole-second prepared 24-fps video')
    seconds = frame_count // SOURCE_FPS
    count = math.ceil(seconds / MAX_WINDOW_SECONDS)
    base, extra = divmod(seconds, count)
    start = 0
    plan = []
    for index in range(count):
        length = base + (index < extra)
        stop = start + length * SOURCE_FPS
        plan.append({'start_frame': start, 'stop_frame': stop,
                     'step_frames': SAMPLE_STEP, 'count': 2 * length,
                     'start_s': start / SOURCE_FPS, 'end_s': stop / SOURCE_FPS})
        start = stop
    return plan


def frame_indices(window):
    return list(range(window['start_frame'], window['stop_frame'], window['step_frames']))


def cosine(first, second):
    if not first or len(first) != len(second):
        raise ValueError('Feature vectors must be nonempty and equally sized')
    first, second = [float(x) for x in first], [float(x) for x in second]
    if not all(math.isfinite(x) for x in first + second):
        raise ValueError('Feature vectors contain non-finite values')
    norm_first, norm_second = math.hypot(*first), math.hypot(*second)
    if not norm_first or not norm_second:
        raise ValueError('Zero feature vector')
    score = sum((x / norm_first) * (y / norm_second) for x, y in zip(first, second))
    if not math.isfinite(score):
        raise ValueError('Non-finite cosine similarity')
    return max(-1.0, min(1.0, score))


def weighted_score(scores, plan):
    if not plan or len(scores) != len(plan):
        raise ValueError('A finite score is required for every window')
    lengths = [window['stop_frame'] - window['start_frame'] for window in plan]
    if any(length <= 0 for length in lengths) or any(not math.isfinite(score) for score in scores):
        raise ValueError('Invalid window duration or score')
    return sum(score * length for score, length in zip(scores, lengths)) / sum(lengths)


def model_paths():
    package = (ROOT / 'third_party/vjepa2').resolve()
    checkpoint = (ROOT / 'models/vjepa2_1_vitG_384.pt').resolve()
    for relative in ('app/vjepa_2_1/models/vision_transformer.py', 'evals/hub/preprocessor.py'):
        if not (package / relative).is_file():
            raise ValueError(f'third_party/vjepa2 lacks {relative}; run bash scripts/setup.sh')
    if not checkpoint.is_file() or not checkpoint.stat().st_size:
        raise ValueError('models/vjepa2_1_vitG_384.pt is missing or empty; run bash scripts/setup.sh')
    missing = [name for name in ('torch', 'torchvision', 'numpy', 'decord', 'cv2', 'PIL', 'timm', 'einops')
               if importlib.util.find_spec(name) is None]
    if missing:
        raise ValueError('Missing inference dependencies: ' + ', '.join(missing) +
                         '; install eval/visual_similarity/requirements.txt')
    return package, checkpoint


def load_encoder(package, checkpoint, device, dtype):
    """Load only the local target_encoder using the upstream ViT-G constructor."""
    import torch
    from unittest.mock import patch

    sys.path.insert(0, str(package))
    from app.vjepa_2_1.models.vision_transformer import vit_gigantic_xformers

    # The constructor calls .item() on the tiny drop-path schedule: keep only
    # that tensor on CPU while the 2B parameter model is constructed on meta.
    linspace = torch.linspace
    with patch('torch.linspace', lambda *args, **kwargs:
               linspace(*args, **{**kwargs, 'device': 'cpu'})):
        with torch.device('meta'):
            encoder = vit_gigantic_xformers(
                img_size=(384, 384), patch_size=16, num_frames=64, tubelet_size=2,
                use_sdpa=True, use_silu=False, wide_silu=True, uniform_power=False,
                use_rope=True, img_temporal_dim_size=1, interpolate_rope=True)
    state = torch.load(checkpoint, map_location='cpu', weights_only=True, mmap=True)
    if 'target_encoder' not in state:
        raise ValueError('V-JEPA checkpoint does not contain target_encoder')
    weights = {key.replace('module.', '').replace('backbone.', ''): value
               for key, value in state['target_encoder'].items()}
    encoder.load_state_dict(weights, strict=True, assign=True)
    return encoder.eval().requires_grad_(False).to(device=device, dtype=dtype)


def score_pair(reference, generated, frame_count, plan, package, checkpoint, device_name):
    import torch
    import torch.nn.functional as functional
    from decord import VideoReader, cpu

    device = torch.device(device_name)
    if device.type not in ('cuda', 'cpu'):
        raise ValueError('Supported devices: cpu, cuda, cuda:N')
    if device.type == 'cuda':
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable; --check verifies inputs without GPU inference')
        torch.cuda.set_device(device)
    dtype = (torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16) \
        if device.type == 'cuda' else torch.float32
    readers = [VideoReader(str(path), ctx=cpu(0), num_threads=2) for path in (reference, generated)]
    if any(len(reader) != frame_count for reader in readers):
        raise ValueError('Decoded video frame count differs from the prepared metadata')
    if any(abs(reader.get_avg_fps() - SOURCE_FPS) > 0.01 for reader in readers):
        raise ValueError('Both prepared videos must decode at 24 fps')
    encoder = load_encoder(package, checkpoint, device, dtype)
    from evals.hub.preprocessor import vjepa2_preprocessor
    transform = vjepa2_preprocessor(crop_size=384)
    scores = []
    with torch.inference_mode():
        for window in plan:
            indices = frame_indices(window)
            features = []
            # The reference and generated clip are inferred sequentially;
            # one model serves the entire case, with no clip microbatch.
            for reader in readers:
                clip = transform(reader.get_batch(indices).asnumpy())[0].unsqueeze(0)
                context = torch.autocast(device_type='cuda', dtype=dtype) \
                    if device.type == 'cuda' else nullcontext()
                with context:
                    tokens = encoder(clip.to(device=device, dtype=dtype))
                features.append(functional.normalize(tokens.float().mean(dim=1), dim=-1).cpu()[0].tolist())
                del tokens, clip
            score = cosine(*features)
            window['cosine_similarity'] = score
            scores.append(score)
    return weighted_score(scores, plan), str(dtype)


def main(argv=None):
    sys.path.insert(0, str(ROOT))
    from common import evaluation_inputs, evaluation_parser, write_json

    parser = evaluation_parser(__doc__)
    parser.add_argument('--device', default='cuda:0')
    args = parser.parse_args(argv)
    output = Path(args.output).expanduser().resolve()
    result_path = (output / str(args.video_id) / 'eval' / 'visual_similarity').resolve() / 'result.json'
    result = {'video_id': args.video_id,
              'status': 'failed', 'score': None, 'device': args.device}
    try:
        case, generated_folder = evaluation_inputs(args, metrics=('visual_similarity',))
        metadata = case['metadata']
        plan = windows(metadata['frame_count'])
        for window in plan:
            window['frame_indices'] = frame_indices(window)
        result.update(reference=str(case['video']), generated=str(generated_folder / 'video.mp4'),
                      fps=SOURCE_FPS,
                      frame_count=metadata['frame_count'], duration_s=metadata['duration_s'], windows=plan)
        try:
            package, checkpoint = model_paths()
            result.update(package=str(package), checkpoint=str(checkpoint), model_available=True)
        except ValueError as exc:
            if not args.check:
                raise
            result.update(model_available=False, model_preflight_error=str(exc))
        if args.check:
            result['status'] = 'checked'
            print(json.dumps(result, indent=2, allow_nan=False))
            return 0
        cache = (result_path.parent / 'cache').resolve()
        cache.mkdir(parents=True, exist_ok=True)
        for key in ('TORCH_HOME', 'TORCH_EXTENSIONS_DIR', 'XDG_CACHE_HOME', 'HF_HOME',
                    'CUDA_CACHE_PATH', 'TRITON_CACHE_DIR', 'TORCHINDUCTOR_CACHE_DIR', 'TMPDIR'):
            os.environ[key] = str(cache)
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['HF_DATASETS_OFFLINE'] = '1'
        import tempfile
        tempfile.tempdir = str(cache)
        score, dtype = score_pair(case['video'], generated_folder / 'video.mp4', metadata['frame_count'],
                                 plan, package, checkpoint, args.device)
        result.update(status='scored', score=score, dtype=dtype,
                      metric='duration_weighted_window_cosine', range=[-1, 1],
                      preprocessing='official short side 438, center crop 384, ImageNet normalization',
                      pooling='last-layer token mean then L2 normalization')
    except (Exception, KeyboardInterrupt) as exc:
        result.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        if not args.check:
            write_json(result_path, result)
        print(result['error'], file=sys.stderr)
        return 1
    write_json(result_path, result)
    print(f"Visual similarity score: {result['score']:.6f}; saved {result_path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
