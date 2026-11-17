"""Minimal local SAM3.1 instance tracker; no imports or GPU work until tracking."""
import inspect
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
from .metrics import box, core_bbox

ROOT = Path(__file__).resolve().parents[2]


def validate_prompt(prompt, positive_count=None, negative_count=None):
    if not isinstance(prompt, dict) or prompt.get('status') not in ('found', 'absent') or (not isinstance(prompt.get('evidence'), str)):
        raise ValueError('grounding must contain found/absent status and evidence')
    for key in ('positive_points', 'negative_points'):
        points = prompt.get(key)
        if not isinstance(points, list):
            raise ValueError('point arrays required')
        for p in points:
            if not isinstance(p, list) or len(p) != 2 or any((isinstance(v, bool) or not isinstance(v, (int, float)) or (not math.isfinite(v)) or (not 0 <= v < 1000) for v in p)):
                raise ValueError('points must be finite xy pairs in [0,1000)')
    all_points = prompt['positive_points'] + prompt['negative_points']
    if len(set(map(tuple, all_points))) != len(all_points):
        raise ValueError('points must be distinct')
    if prompt['status'] == 'absent':
        if prompt.get('bbox_xyxy') is not None or all_points:
            raise ValueError('absent grounding must have no geometry')
        return prompt
    bounds = box(prompt.get('bbox_xyxy'))
    if not prompt['positive_points']:
        raise ValueError('found grounding requires positive points')
    for p in prompt['positive_points']:
        if not bounds[0] <= p[0] <= bounds[2] or not bounds[1] <= p[1] <= bounds[3]:
            raise ValueError('positive point lies outside bbox')
    for key, count in (('positive_points', positive_count), ('negative_points', negative_count)):
        if count is not None and len(prompt[key]) != count:
            raise ValueError('grounding point count differs from the required count')
    return prompt


def instance_prompt_to_sam(prompt):
    validate_prompt(prompt)
    if prompt['status'] != 'found':
        raise ValueError('tracking requires found geometry')
    x0, y0, x1, y1 = prompt['bbox_xyxy']
    positive, negative = (prompt['positive_points'], prompt['negative_points'])
    return dict(points=[[x0 / 1000, y0 / 1000], [x1 / 1000, y1 / 1000]] + [[x / 1000, y / 1000] for x, y in positive + negative], point_labels=[2, 3] + [1] * len(positive) + [0] * len(negative), rel_coordinates=True, clear_old_points=True)


def sample_indices(meta, interval=None):
    ratio = meta['fps'] / 4
    stride = round(ratio)
    if stride < 1 or not math.isclose(ratio, stride):
        raise ValueError('4 fps must exactly divide source fps')
    indices = list(range(0, meta['frame_count'], stride))
    if interval is None:
        return indices
    start, end = interval
    if not all((isinstance(v, (int, float)) and (not isinstance(v, bool)) and math.isfinite(v) for v in interval)) or not 0 <= start < end <= meta['frame_count'] / meta['fps'] + 1e-09:
        raise ValueError('interval must lie within clip')
    return [i for i in indices if start - 1e-09 <= i / meta['fps'] < end - 1e-09]


def model_paths():
    package = (ROOT / 'third_party/sam3').resolve()
    checkpoint = (ROOT / 'models/sam3.1_multiplex.pt').resolve()
    if not (package / 'sam3/__init__.py').is_file() or (not (package / 'sam3/assets/bpe_simple_vocab_16e6.txt.gz').is_file()):
        raise ValueError('third_party/sam3 must contain SAM source and BPE vocabulary; run bash scripts/setup.sh')
    if not checkpoint.is_file():
        raise ValueError('models/sam3.1_multiplex.pt is missing; run bash scripts/setup.sh')
    return (package, checkpoint)


def _compatible_predictor(predictor):
    """Adapt native SAM session API locally, without touching installed source."""
    base = type(predictor)

    class LocalSessionPredictor(base):

        def start_session(
            self,
            resource_path,
            session_id=None,
            offload_video_to_cpu=False,
            offload_state_to_cpu=False,
        ):
            kwargs = dict(
                resource_path=resource_path,
                offload_video_to_cpu=offload_video_to_cpu,
                offload_state_to_cpu=offload_state_to_cpu,
            )
            for name in ('async_loading_frames', 'video_loader_type'):
                if hasattr(self, name):
                    kwargs[name] = getattr(self, name)
            parameters = inspect.signature(self.model.init_state).parameters
            if 'offload_state_to_cpu' not in parameters and (not any((p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()))):
                kwargs.pop('offload_state_to_cpu')
            state = self.model.init_state(**kwargs)
            session_id = session_id or str(uuid.uuid4())
            self._all_inference_states[session_id] = dict(
                state=state,
                session_id=session_id,
                start_time=time.time(),
                last_use_time=time.time(),
            )
            return dict(session_id=session_id)
    predictor.__class__ = LocalSessionPredictor
    model_base = type(predictor.model)
    if hasattr(model_base, '_build_sam2_output'):

        class PointTrackingModel(model_base):

            def _build_sam2_output(self, inference_state, frame_idx, refined_obj_id_to_mask=None):
                inference_state.setdefault('cached_frame_outputs', {}).setdefault(frame_idx, {})
                return super()._build_sam2_output(inference_state, frame_idx, refined_obj_id_to_mask)
        predictor.model.__class__ = PointTrackingModel
    return predictor


class SamTracker:

    def __init__(self, workspace):
        self.package, self.checkpoint = model_paths()
        self.workspace = Path(workspace).resolve()
        self.predictor = None

    def _load(self):
        if self.predictor is not None:
            return
        runtime = self.workspace / 'runtime'
        runtime.mkdir(parents=True, exist_ok=True)
        for variable, name in [
            ('HF_HOME', 'huggingface'),
            ('TORCH_HOME', 'torch'),
            ('XDG_CACHE_HOME', 'xdg'),
            ('TRITON_CACHE_DIR', 'triton'),
            ('TORCHINDUCTOR_CACHE_DIR', 'inductor'),
            ('TMPDIR', 'tmp'),
            ('CUDA_CACHE_PATH', 'cuda'),
            ('TORCH_EXTENSIONS_DIR', 'torch_extensions'),
            ('HF_HUB_CACHE', 'huggingface/hub'),
            ('HF_ASSETS_CACHE', 'huggingface/assets'),
            ('NUMBA_CACHE_DIR', 'numba'),
            ('MPLCONFIGDIR', 'matplotlib'),
            ('TEMP', 'tmp'),
            ('TMP', 'tmp'),
        ]:
            path = runtime / name
            path.mkdir(parents=True, exist_ok=True)
            os.environ[variable] = str(path)
        tempfile.tempdir = str(runtime / 'tmp')
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['HF_DATASETS_OFFLINE'] = '1'
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(self.package))
        import torch
        torch.hub.set_dir(str(runtime / 'torch/hub'))
        if not torch.cuda.is_available():
            raise RuntimeError('SAM3.1 tracking requires a visible CUDA GPU')
        from sam3.model_builder import build_sam3_multiplex_video_predictor
        self.torch = torch
        predictor = build_sam3_multiplex_video_predictor(
            checkpoint_path=str(self.checkpoint),
            bpe_path=str(self.package / 'sam3/assets/bpe_simple_vocab_16e6.txt.gz'),
            max_num_objects=16,
            use_fa3=False,
            compile=False,
            warm_up=False,
            async_loading_frames=False,
        )
        for owner in (predictor, getattr(predictor.model, 'tracker', None)):
            context = getattr(owner, 'bf16_context', None)
            if context is not None:
                context.__exit__(None, None, None)
                owner.bf16_context = None
        self.predictor = _compatible_predictor(predictor)

    def track(self, meta, frames, prompt_frame, prompt, interval, output):
        indices = sample_indices(meta, interval)
        if prompt_frame not in indices:
            raise ValueError('prompt frame must be on 4 fps target grid')
        encoded = instance_prompt_to_sam(prompt)
        output = Path(output).resolve()
        if not output.is_relative_to(self.workspace):
            raise ValueError('tracking output escapes workspace')
        output.mkdir(parents=True, exist_ok=True)
        self._load()
        import numpy as np
        from PIL import Image
        seen = set()
        boxes = {}
        duplicates = 0
        session = str(uuid.uuid4())
        position = indices.index(prompt_frame)

        def consume(result):
            nonlocal duplicates
            index = result.get('frame_index')
            if type(index) != int or not 0 <= index < len(indices):
                raise ValueError('SAM index exceeds sampled coverage')
            source = indices[index]
            data = result['outputs']

            def array(value):
                if hasattr(value, 'detach'):
                    value = value.detach().cpu().numpy()
                return np.array(value, copy=True)
            ids = array(data['out_obj_ids'])
            masks = array(data['out_binary_masks'])
            if ids.ndim != 1 or ids.dtype.kind not in 'iu' or len(ids) > 1 or any((i != 1 for i in ids)):
                raise ValueError('SAM output must contain requested obj_id=1 only')
            if masks.shape != (len(ids), meta['height'], meta['width']) or masks.dtype != np.bool_:
                raise ValueError('SAM masks must match full video canvas and have bool dtype')
            candidate = core_bbox(masks[0]) if len(ids) else None
            if source in seen:
                duplicates += 1
                if source != prompt_frame or duplicates > 2:
                    raise ValueError('unexpected duplicate SAM frame')
                if boxes.get(source) is not None or candidate is None:
                    return
            seen.add(source)
            boxes[source] = candidate
            if len(ids):
                np.savez_compressed(
                    output / f'{source:05d}.npz',
                    mask=masks[0],
                    frame_index=source,
                    object_id=1,
                )
                Image.fromarray(masks[0].astype(np.uint8) * 255).save(output / f'{source:05d}.png')
        predictor = self.predictor
        with self.torch.inference_mode(), self.torch.autocast(device_type='cuda', dtype=self.torch.bfloat16):
            with tempfile.TemporaryDirectory(prefix='sampled-', dir=output) as folder:
                for index in indices:
                    source = (Path(frames) / f'{index:05d}.jpg').resolve(strict=True)
                    (Path(folder) / f'{index:05d}.jpg').symlink_to(source)
                opened = False
                failure = None
                try:
                    response = predictor.handle_request(dict(
                        type='start_session',
                        session_id=session,
                        resource_path=folder,
                        offload_video_to_cpu=False,
                    ))
                    opened = True
                    if response.get('session_id') != session:
                        raise ValueError('unexpected SAM session')
                    consume(predictor.handle_request(dict(
                        type='add_prompt',
                        session_id=session,
                        frame_index=position,
                        obj_id=1,
                        **encoded,
                    )))
                    direction = 'backward' if position == len(indices) - 1 else 'both'
                    for response in predictor.handle_stream_request(dict(
                        type='propagate_in_video',
                        session_id=session,
                        propagation_direction=direction,
                        start_frame_index=position,
                    )):
                        consume(response)
                    if seen != set(indices):
                        raise ValueError(f'incomplete SAM coverage: missing {sorted(set(indices) - seen)}')
                except Exception as exc:
                    failure = exc
                    raise
                finally:
                    if opened:
                        try:
                            predictor.handle_request(dict(type='close_session', session_id=session))
                        except Exception:
                            if failure is None:
                                raise
        return dict(status='found' if any((v is not None for v in boxes.values())) else 'absent', boxes=boxes, coverage=dict(
            expected=len(indices),
            processed_frames=len(seen),
            complete=True,
        ), prompt_frame=prompt_frame, interval_s=interval)
