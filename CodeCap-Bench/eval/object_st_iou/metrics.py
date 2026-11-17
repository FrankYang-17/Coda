"""Core bounding boxes and frame IoU averaged on an absolute timeline."""
import math


def box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError('bbox must be xyxy with four numbers')
    if any((isinstance(v, bool) or not isinstance(v, (int, float)) or (not math.isfinite(v)) or (not 0 <= v <= 1000) for v in value)):
        raise ValueError('bbox coordinates must be finite numbers in 0..1000')
    if value[0] >= value[2] or value[1] >= value[3]:
        raise ValueError('bbox must have positive width and height')
    return list(value)


def core_bbox(mask):
    """8-connected components >=1% largest; original occupied-pixel extrema."""
    import numpy as np
    import cv2
    cv2.setNumThreads(1)
    if not isinstance(mask, np.ndarray) or mask.dtype != np.bool_ or mask.ndim != 2 or (min(mask.shape) <= 0):
        raise ValueError('mask must be a 2D bool array with a nonempty canvas')
    if not mask.any():
        return None
    _, _, stats, _ = cv2.connectedComponentsWithStats(
        np.ascontiguousarray(mask, dtype=np.uint8),
        connectivity=8,
    )
    largest = max((int(row[4]) for row in stats[1:]))
    kept = [row for row in stats[1:] if int(row[4]) >= largest * 0.01]
    x0 = min((int(r[0]) for r in kept))
    y0 = min((int(r[1]) for r in kept))
    x1 = max(x0 + 1, max((int(r[0] + r[2]) - 1 for r in kept)))
    y1 = max(y0 + 1, max((int(r[1] + r[3]) - 1 for r in kept)))
    h, w = mask.shape
    return [x0 / w * 1000, y0 / h * 1000, x1 / w * 1000, y1 / h * 1000]


def boxes_frame_mean(reference, response, ref_meta, response_meta, fps=4):
    source_fps = float(ref_meta['fps'])
    if not math.isclose(source_fps, float(response_meta['fps'])) or source_fps <= 0:
        raise ValueError('videos must have the same positive fps')
    stride = round(source_fps / fps)
    if stride < 1 or not math.isclose(stride, source_fps / fps):
        raise ValueError('evaluation fps must exactly divide video fps')
    origin = ref_meta.get('time_origin_s', 0)
    offset = (response_meta.get('time_origin_s', 0) - origin) * source_fps
    shift = round(offset)
    if not math.isclose(offset, shift, abs_tol=1e-06) or shift % stride:
        raise ValueError('video origins must lie on the same sampling lattice')
    for values, meta in ((reference, ref_meta), (response, response_meta)):
        for index, value in values.items():
            if type(index) != int or not 0 <= index < meta['frame_count'] or index % stride:
                raise ValueError('track index outside video or sampling grid')
            if value is not None:
                box(value)
    counts = dict(
        both_present_frames=0,
        reference_only_frames=0,
        response_only_frames=0,
        both_empty_frames=0,
    )
    rows = []
    active = []
    for index in range(
        min(0, shift),
        max(ref_meta['frame_count'], shift + response_meta['frame_count']),
        stride,
    ):
        a = reference.get(index)
        b = response.get(index - shift)
        area_a = (a[2] - a[0]) * (a[3] - a[1]) if a else 0
        area_b = (b[2] - b[0]) * (b[3] - b[1]) if b else 0
        intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1])) if a and b else 0
        union = area_a + area_b - intersection
        iou = intersection / union if union else None
        if iou is not None:
            active.append(iou)
        label = 'both_present_frames' if a and b else 'reference_only_frames' if a else 'response_only_frames' if b else 'both_empty_frames'
        counts[label] += 1
        rows.append(dict(
            timestamp_s=origin + index / source_fps,
            reference_bbox=a,
            response_bbox=b,
            intersection=intersection / 1000000.0,
            union=union / 1000000.0,
            iou=iou,
        ))
    return dict(
        iou=math.fsum(active) / len(active) if active else None,
        scored_frames=len(active),
        per_frame=rows,
        **counts,
    )


def aggregate_targets(targets):
    scores = [t['iou'] for t in targets if t.get('status') == 'ok' and t.get('iou') is not None]
    failures = sum((t.get('status') == 'error' for t in targets))
    return dict(
        iou=math.fsum(scores) / len(scores) if scores and len(scores) == len(targets) else None,
        expected_targets=len(targets),
        scored_targets=len(scores),
        failed_targets=failures,
        coverage_complete=bool(targets) and len(scores) == len(targets),
    )
