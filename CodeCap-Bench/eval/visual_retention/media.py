"""Build 2 fps montages sampled from each clip's original 24 fps start frame."""
import math
from pathlib import Path
import tempfile

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
FPS = 2


def sample_indices(frame_count, bounds=None):
    if type(frame_count) is not int or frame_count <= 0:
        raise ValueError('Expected positive frame_count')
    duration = frame_count / 24
    bounds = [0, duration] if bounds is None else bounds
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
        raise ValueError('Expected interval [start,end]')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds):
        raise ValueError('Interval must contain finite numbers')
    start, end = bounds
    if math.isclose(end, duration, abs_tol=.005, rel_tol=0):
        end = duration
    if not 0 <= start < end <= duration:
        raise ValueError('Interval outside video duration')
    if any(not math.isclose(v * 24, round(v * 24), abs_tol=1e-5) for v in (start, end)):
        raise ValueError('Interval must lie on the original 24 fps frame grid')
    return list(range(round(start * 24), round(end * 24), 24 // FPS))


def judge_layout(generated_count, reference_counts):
    if generated_count <= 0 or not reference_counts or min(reference_counts) <= 0:
        raise ValueError('Montage requires nonempty generated and reference samples')
    rows, columns = 1, 1
    while math.ceil(generated_count / (rows * columns)) + math.ceil(max(reference_counts) / (rows * columns)) > 50:
        if rows == columns:
            columns += 1
        else:
            rows += 1
    return rows, columns


def fits(width, height):
    return max(width, height) <= 2048 and math.ceil(width / 32) * math.ceil(height / 32) <= 2500


def target_dimensions(width, height):
    if width <= 0 or height <= 0:
        raise ValueError('Expected positive image dimensions')
    if fits(width, height):
        return width, height
    scale = min(2048 / max(width, height), 1)
    width, height = math.floor(width * scale + .5), math.floor(height * scale + .5)
    if not fits(width, height):
        scale = math.sqrt(32 ** 2 * 2500 / width / height)
        wide, high = width * scale / 32, height * scale / 32
        scale *= min(math.floor(wide) / wide, math.floor(high) / high)
        width, height = math.floor(width * scale), math.floor(height * scale)
    width, height = width // 2 * 2, height // 2 * 2
    if min(width, height) < 2 or not fits(width, height):
        raise ValueError('Cannot fit montage aspect ratio within image limits')
    return width, height


def montage_plan(source, bounds, layout, label):
    indices = sample_indices(source['frame_count'], bounds)
    rows, columns = layout
    if type(rows) is not int or type(columns) is not int or min(rows, columns) <= 0 or label not in ('GEN', 'REF'):
        raise ValueError('Invalid montage layout or label')
    packing = rows * columns
    if math.ceil(len(indices) / packing) > 50:
        raise ValueError('Montage exceeds 50 images')
    footer, font_size = (64, 36) if source['width'] >= 800 else (42, 23)
    native_width = columns * (source['width'] + 8) + 8
    native_height = rows * (source['height'] + footer + 8) + 8
    width, height = target_dimensions(native_width, native_height)
    start_frame = indices[0]
    end_frame = source['frame_count'] if bounds is None or math.isclose(bounds[1], source['frame_count'] / 24, abs_tol=.005, rel_tol=0) else round(bounds[1] * 24)
    sheets = []
    for offset in range(0, len(indices), packing):
        cells = [{'source_frame': n, 'timestamp_s': n / 24} for n in indices[offset:offset + packing]]
        sheets.append({'image': f'{len(sheets) + 1:03d}.jpg', 'cells': cells + [None] * (packing - len(cells))})
    return dict(fps=FPS, label=label, rows=rows, columns=columns, frames_per_image=packing,
                indices=indices, timestamps_s=[n / 24 for n in indices],
                display_timestamps_s=[(n - start_frame) / 24 for n in indices],
                timestamp_origin_s=start_frame / 24, interval_s=[start_frame / 24, end_frame / 24],
                logical_duration_s=(end_frame - start_frame) / 24,
                source_frame_count=source['frame_count'], frame_width=source['width'], frame_height=source['height'],
                image_count=len(sheets), sheets=sheets, native_width=native_width, native_height=native_height,
                image_width=width, image_height=height, footer_px=footer, font_size=font_size,
                margin_px=8, padding_px=8, jpeg_qscale=2)


def create_montage(frames, source, output, *, bounds=None, layout=(1, 1), label='REF'):
    from common import local_path, run
    record = montage_plan(source, bounds, layout, label)
    output = local_path(output)
    output.mkdir(parents=True, exist_ok=False)
    if not Path(FONT).is_file():
        raise ValueError(f'Timestamp font missing: {FONT}')
    footer = record['footer_px']
    filters = (f'pad=iw:ih+{footer}:0:0:color=black,'
               f'drawtext=fontfile={FONT}:fontcolor=white:fontsize={record["font_size"]}:x=12:y=h-{footer-8}:'
               f"text='{label} %{{pts\\:hms}}',tile={record['columns']}x{record['rows']}:"
               f"nb_frames={record['frames_per_image']}:padding=8:margin=8:color=black")
    if (record['image_width'], record['image_height']) != (record['native_width'], record['native_height']):
        filters += f',scale={record["image_width"]}:{record["image_height"]}:flags=lanczos'
    with tempfile.TemporaryDirectory(dir=output, prefix='.frames-') as scratch:
        scratch = Path(scratch)
        for index, source_frame in enumerate(record['indices']):
            frame = (Path(frames) / f'{source_frame:05d}.jpg').resolve(strict=True)
            (scratch / f'{index:05d}.jpg').symlink_to(frame)
        run(['ffmpeg', '-v', 'error', '-threads', '2', '-framerate', str(FPS),
             '-i', str(scratch / '%05d.jpg'), '-vf', filters, '-vsync', '0',
             '-q:v', '2', '-threads', '2', str(output / '%03d.jpg')])
    images = sorted(output.glob('*.jpg'))
    if len(images) != record['image_count']:
        raise ValueError('Montage image count differs from sampling plan')
    record['images'] = images
    return record


def public_record(record):
    return dict({key: value for key, value in record.items()
                 if key not in ('images', 'sheets', 'timestamps_s', 'display_timestamps_s')},
                images=[p.name for p in record['images']], media_retained=False)
