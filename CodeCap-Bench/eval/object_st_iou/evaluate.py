"""Evaluate object-level spatio-temporal IoU with GPT localization and SAM3.1 tracking."""
import json
import math
from pathlib import Path
import re
import shutil
import sys
sys.dont_write_bytecode = True
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from common import EVAL_REASONING_EFFORT, evaluation_model, responses_url, evaluation_parser, evaluation_inputs, extract_frames, load_env, request_json, write_json
from eval.object_st_iou.metrics import boxes_frame_mean, aggregate_targets
from eval.object_st_iou.tracking import SamTracker, model_paths, sample_indices, validate_prompt
HERE = Path(__file__).resolve().parent


def temporal_frames(value, meta):
    if not isinstance(value, dict) or value.get('status') not in ('found', 'absent') or (not isinstance(value.get('evidence'), str)):
        raise ValueError('temporal result requires found/absent status and evidence')
    times = [value.get(k) for k in ('start_s', 'end_s', 'reference_s')]
    if value['status'] == 'absent':
        if any((t is not None for t in times)):
            raise ValueError('absent temporal result must have null times')
        return dict(value, reference_frame=None, start_frame=None, end_frame=None)
    if any((isinstance(t, bool) or not isinstance(t, (int, float)) or (not math.isfinite(t)) for t in times)):
        raise ValueError('temporal times must be finite numbers')
    start, end, reference = times
    if not 0 <= start <= reference < end <= meta['duration_s'] + 1e-09:
        raise ValueError('temporal keyframe must lie in valid clip interval')
    for key, t in zip(('start_s', 'end_s', 'reference_s'), times):
        if key == 'end_s' and math.isclose(t, meta['duration_s'], abs_tol=1e-08):
            continue
        if not math.isclose(t * 4, round(t * 4), abs_tol=1e-07):
            raise ValueError('temporal times must lie on 4 fps sampling grid')
    frames = [round(t * meta['fps']) for t in times]
    if any((not math.isclose(t * meta['fps'], i, abs_tol=1e-07) for t, i in zip(times, frames))):
        raise ValueError('temporal times must map to original source frames')
    if frames[2] not in sample_indices(meta, [start, end]):
        raise ValueError('temporal keyframe outside sampled interval')
    return dict(value, start_frame=frames[0], end_frame=frames[1], reference_frame=frames[2])


def validate_targets(reference, meta):
    targets = reference.get('targets')
    if not isinstance(targets, list) or not targets:
        raise ValueError('Object spatio-temporal reference requires nonempty target list')
    seen = set()
    for target in targets:
        identifier = target.get('id')
        if not isinstance(identifier, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', identifier) or identifier in seen:
            raise ValueError('invalid or duplicate reference object ID')
        seen.add(identifier)
        for key in ('name', 'description', 'membership'):
            if not isinstance(target.get(key), str) or not target[key].strip():
                raise ValueError(f'missing target {key}')
        temporal = temporal_frames(target['temporal'], meta)
        if temporal['status'] != 'found':
            raise ValueError('reference target must have found temporal bounds')
        for key in ('start_frame', 'end_frame', 'reference_frame'):
            if type(target['temporal'].get(key)) != int or target['temporal'][key] != temporal[key]:
                raise ValueError(f'reference {key} disagrees with temporal seconds')
        validate_prompt(target['grounding'])
        if target['grounding']['status'] != 'found':
            raise ValueError('reference target requires found grounding')
    return targets


def overlay_reference(frame, prompt, path, points=True):
    from PIL import Image, ImageDraw
    with Image.open(frame) as source:
        image = source.convert('RGB')
    width, height = image.size
    draw = ImageDraw.Draw(image)
    stroke = max(2, round(min(width, height) / 300))
    radius = max(3, round(min(width, height) / 150))
    x0, y0, x1, y1 = prompt['bbox_xyxy']
    draw.rectangle(
        (
            min(width - 1, x0 * width / 1000),
            min(height - 1, y0 * height / 1000),
            min(width - 1, x1 * width / 1000),
            min(height - 1, y1 * height / 1000),
        ),
        outline=(255, 180, 0),
        width=stroke,
    )
    if points:
        for key, color in [('positive_points', (0, 255, 80)), ('negative_points', (255, 40, 80))]:
            for x, y in prompt[key]:
                x, y = (x * width / 1000, y * height / 1000)
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    image.save(path, format='PNG')
    return path


def prepare_montage(frames, meta, directory):
    """Group sampled frames into timestamped sheets, leaving room for a reference image."""
    from PIL import Image, ImageDraw, ImageFont
    indices = sample_indices(meta)
    n = len(indices)
    group = 1 if n <= 48 else 2 if n <= 96 else 4 if n <= 192 else 2 * math.ceil(n / 96)
    columns = 1 if group <= 2 else 2
    rows = math.ceil(group / columns)
    width = min(1280, meta['width'])
    height = round(width * meta['height'] / meta['width'])
    gutter, label = (24, 48)
    size = (columns * width + (columns + 1) * gutter, rows * (height + label) + (rows + 1) * gutter)
    font_path = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    font = ImageFont.truetype(str(font_path), 26)
    directory.mkdir(parents=True, exist_ok=True)
    sheets = []
    for start in range(0, n, group):
        canvas = Image.new('RGB', size, '#202020')
        draw = ImageDraw.Draw(canvas)
        entries = []
        for slot, index in enumerate(indices[start:start + group]):
            x = gutter + slot % columns * (width + gutter)
            y = gutter + slot // columns * (height + label + gutter)
            with Image.open(Path(frames) / f'{index:05d}.jpg') as image:
                if image.size != (meta['width'], meta['height']):
                    raise ValueError('montage source dimensions disagree')
                canvas.paste(
                    image.convert('RGB').resize((width, height), Image.Resampling.LANCZOS),
                    (x, y),
                )
            seconds = index / meta['fps']
            draw.rectangle((x, y + height, x + width - 1, y + height + label - 1), fill='white')
            draw.text(
                (x + 12, y + height + 7),
                f't = {seconds:06.2f} s   |   frame {start + slot:03d} @ 4 fps',
                font=font,
                fill='black',
            )
            entries.append(dict(
                timestamp_s=seconds,
                source_frame=index,
                sampled_frame=start + slot,
                image_rect=[x, y, x + width, y + height],
            ))
        path = directory / f'{len(sheets):03d}.jpg'
        canvas.save(path, quality=94, subsampling=0)
        sheets.append(dict(path=str(path), frames=entries))
    manifest = dict(
        fps=4,
        frame_count=n,
        group_size=group,
        sheet_count=len(sheets),
        tile_size=[width, height],
        sheets=sheets,
    )
    return manifest


def object_schema(properties):
    return dict(
        type='object',
        properties=properties,
        required=list(properties),
        additionalProperties=False,
    )


def localize(target, meta, frames, reference_frame, folder, montage, config, *, record=None, save=None):
    record = record if record is not None else {}
    identity = {key: target[key] for key in ('id', 'name', 'description', 'membership')}
    locator = overlay_reference(
        reference_frame,
        target['grounding'],
        folder / 'locator.png',
        points=False,
    )
    index = '\n'.join((f'Image {i}: ' + ', '.join((f"{row['timestamp_s']:.3f}s" for row in sheet['frames'])) for i, sheet in enumerate(montage['sheets'], 1)))
    inputs = f"Video duration: {meta['duration_s']} seconds\nSampling rate: 4 fps\nSampled video frames: {montage['frame_count']}\nImage time index:\n{index}\n\nTarget:\n" + json.dumps(identity, ensure_ascii=False, indent=2)
    prompt = (HERE / 'temporal_grounding.txt').read_text().replace('{{input}}', inputs)
    props = {'status': dict(type='string', enum=['found', 'absent']), **{key: dict(type=['number', 'null']) for key in ('start_s', 'end_s', 'reference_s')}, 'evidence': dict(type='string')}
    record['temporal'] = dict(prompt=prompt)
    if save:
        save()
    raw, usage = request_json(config, [Path(sheet['path']) for sheet in montage['sheets']] + [locator],
                              prompt, object_schema(props), name='sam_temporal_montage',
                              image_labels={len(montage['sheets']):
                                  'The next image is a reference image for identification only, not part of the video being localized. '
                                  'All output times refer to the preceding montage images.'},
                              validate=lambda value: temporal_frames(value, meta))
    record['temporal'].update(response=raw, usage=usage)
    if save:
        save()
    interval = temporal_frames(raw, meta)
    if interval['status'] == 'absent':
        return dict(status='absent', stage='temporal', temporal=interval)
    reference_overlay = overlay_reference(reference_frame, target['grounding'], folder / 'prompt.png')
    positive_count = len(target['grounding']['positive_points'])
    negative_count = len(target['grounding']['negative_points'])
    inputs = f'Positive point count when found: {positive_count}\nNegative point count when found: {negative_count}\n\nTarget:\n' + json.dumps(identity, ensure_ascii=False, indent=2)
    point = dict(
        type='array',
        items=dict(type='number', minimum=0, exclusiveMaximum=1000),
        minItems=2,
        maxItems=2,
    )
    props = dict(
        status=dict(type='string', enum=['found', 'absent']),
        bbox_xyxy=dict(
            type=['array', 'null'],
            items=dict(type='number', minimum=0, maximum=1000),
            minItems=4,
            maxItems=4,
        ),
        positive_points=dict(type='array', items=point),
        negative_points=dict(type='array', items=point),
        evidence=dict(type='string'),
    )
    prompt = (HERE / 'spatial_grounding.txt').read_text().replace('{{input}}', inputs)
    record['spatial'] = dict(prompt=prompt)
    if save:
        save()
    grounding, usage = request_json(
        config,
        [reference_overlay, Path(frames) / f"{interval['reference_frame']:05d}.jpg"],
        prompt,
        object_schema(props),
        name='sam_grounding',
        validate=lambda value: validate_prompt(value, positive_count, negative_count),
    )
    record['spatial'].update(response=grounding, usage=usage)
    if save:
        save()
    validate_prompt(grounding, positive_count, negative_count)
    return dict(status=grounding['status'], stage='spatial', temporal=interval, prompt=grounding)


def generated_metadata(document, folder):
    meta = dict(document['video'])
    for key in ('width', 'height', 'frame_count'):
        if type(meta.get(key)) != int or meta[key] <= 0:
            raise ValueError(f'invalid generated {key}')
    if isinstance(meta.get('fps'), bool) or not isinstance(meta.get('fps'), (int, float)) or (not math.isfinite(meta['fps'])) or (meta['fps'] <= 0):
        raise ValueError('invalid generated fps')
    meta.update(
        duration_s=meta['frame_count'] / meta['fps'],
        path=str(folder / 'video.mp4'),
        time_origin_s=0,
    )
    sample_indices(meta)
    return meta


def main(argv=None):
    parser = evaluation_parser('Evaluate object-level spatio-temporal IoU with GPT grounding and SAM3.1')
    args = parser.parse_args(argv)
    try:
        case, generated = evaluation_inputs(args, metrics=('object_st_iou',))
        targets = validate_targets(case['object_st_iou'], case['metadata'])
        response_meta = generated_metadata(
            json.loads((generated / 'metadata.json').read_text()),
            generated,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    try:
        config = load_env()
        judge_model = evaluation_model(config)
        endpoint = responses_url(config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.check:
        try:
            package, checkpoint = model_paths()
            weights = dict(available=True, package=str(package), checkpoint=str(checkpoint))
        except ValueError as exc:
            weights = dict(available=False, reason=str(exc))
        print(json.dumps(
            dict(
                status='ok',
                video_id=args.video_id,
                target_count=len(targets),
                fps=4,
                model=weights,
                judge_model=judge_model,
                endpoint=endpoint,
                api_configured=bool(config.get('EVAL_API_KEY')),
                output=str(generated / 'eval/object_st_iou'),
            ),
            indent=2,
        ))
        return 0
    output = generated / 'eval/object_st_iou'
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    result = dict(
        video_id=args.video_id,
        status='error',
        config=dict(
            sampling_fps=4,
            model=judge_model,
            endpoint=endpoint,
            reasoning_effort=EVAL_REASONING_EFFORT,
            max_output_tokens=16384,
            sam_package=str(HERE.parents[1] / 'third_party/sam3'),
            sam_checkpoint=str(HERE.parents[1] / 'models/sam3.1_multiplex.pt'),
            sam_compile=False,
            sam_warm_up=False,
            sam_async_loading_frames=False,
            sam_max_objects=16,
        ),
        reference_metadata=case['metadata'],
        generated_metadata=response_meta,
        targets=[dict(target_id=target['id'], name=target['name'], status='pending',
                      iou=None, details={}) for target in targets],
    )
    try:
        if not config.get('EVAL_API_KEY', '').strip():
            raise ValueError('Set EVAL_API_KEY in .env')
        tracker = SamTracker(output)
        reference_frames = extract_frames(case['video'], output / 'reference_frames')
        montage = prepare_montage(generated / 'frames', response_meta, output / 'montage')
        result['montage'] = montage
        def save():
            write_json(output / 'result.json', dict(result, **aggregate_targets(result['targets'])))
        for target, row in zip(targets, result['targets']):
            folder = output / target['id']
            folder.mkdir()
            stage = 'reference_tracking'
            try:
                temporal = target['temporal']
                reference = tracker.track(
                    case['metadata'],
                    reference_frames,
                    temporal['reference_frame'],
                    target['grounding'],
                    [temporal['start_s'], temporal['end_s']],
                    folder / 'reference_masks',
                )
                row['details']['reference_track'] = reference
                save()
                if reference['status'] == 'absent':
                    raise ValueError('reference SAM tracking yielded no nonempty core boxes')
                stage = 'localization'
                localized = localize(
                    target,
                    response_meta,
                    generated / 'frames',
                    reference_frames / f"{temporal['reference_frame']:05d}.jpg",
                    folder,
                    montage,
                    config,
                    record=row['details'], save=save,
                )
                row['localization'] = localized
                if localized['status'] == 'absent':
                    response = dict(
                        status='absent',
                        boxes={},
                        coverage=dict(complete=True, expected=0, processed_frames=0),
                    )
                else:
                    stage = 'generated_tracking'
                    interval = localized['temporal']
                    response = tracker.track(
                        response_meta,
                        generated / 'frames',
                        interval['reference_frame'],
                        localized['prompt'],
                        [interval['start_s'], interval['end_s']],
                        folder / 'generated_masks',
                    )
                row['details']['generated_track'] = response
                save()
                score = boxes_frame_mean(
                    reference['boxes'],
                    response['boxes'],
                    case['metadata'],
                    response_meta,
                )
                if score['iou'] is None:
                    raise ValueError('no scoreable frames')
                row.update(
                    score,
                    status='ok',
                    reference_coverage=reference['coverage'],
                    generated_coverage=response['coverage'],
                )
            except Exception as exc:
                row.update(status='error', stage=stage, error=f'{type(exc).__name__}: {exc}')
            save()
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        for row in result['targets']:
            if row['status'] == 'pending':
                row.update(status='error', stage='setup', error=result['error'])
    result['usage'] = []
    for row in result['targets']:
        for stage in ('temporal', 'spatial'):
            request = row.get('details', {}).get(stage)
            if request and 'usage' in request:
                result['usage'].append(dict(target_id=row['target_id'], stage=stage, **request['usage']))
    result['config']['response_models'] = sorted({
        item['response_model']
        for item in result['usage']
        if item.get('response_model')
    })
    result.update(aggregate_targets(result['targets']))
    result['status'] = 'ok' if result['coverage_complete'] else 'error'
    write_json(output / 'result.json', result)
    print(json.dumps(
        dict(
            status=result['status'],
            video_id=args.video_id,
            iou=result['iou'],
            result=str(output / 'result.json'),
        ),
        indent=2,
    ))
    return 0 if result['status'] == 'ok' else 1
if __name__ == '__main__':
    raise SystemExit(main())
