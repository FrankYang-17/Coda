"""Judge one generated case against its independent focal-point annotations using GPT."""
import json
from pathlib import Path
import re
import tempfile
import sys

sys.dont_write_bytecode = True
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from eval.visual_retention import media
else:
    from . import media

REASONING_EFFORT = 'high'
MAX_OUTPUT_TOKENS = 8192
REQUEST_ATTEMPTS = 6  # Total attempts, including the first request.

SCHEMA = {'type': 'object', 'properties': {'score': {'type': 'integer', 'minimum': 0, 'maximum': 5}},
          'required': ['score'], 'additionalProperties': False}


def validate_score(value):
    if not isinstance(value, dict) or set(value) != {'score'} or type(value['score']) is not int or not 0 <= value['score'] <= 5:
        raise ValueError('Expected exactly {"score": integer from 0 to 5}')
    return value['score']


def aggregate(entries):
    good = [point for point in entries if point['status'] == 'ok']
    for point in good:
        validate_score({'score': point.get('score')})
    def mean(points):
        scores = [point['score'] for point in points]
        return sum(scores) / len(scores) if scores else None
    done, total = len(good), len(entries)
    return dict(status='ok' if total and done == total else 'partial' if done else 'error',
                score=mean(good), static_score=mean(p for p in good if p['kind'] == 'static'),
                dynamic_score=mean(p for p in good if p['kind'] == 'dynamic'),
                completed_points=done, total_points=total, coverage=done / total if total else None, points=entries)


def focal_points(reference, source):
    points, ids = [], set()
    for kind in ('static', 'dynamic'):
        group = reference.get(f'{kind}_focal_points')
        if not isinstance(group, list):
            raise ValueError(f'Expected {kind}_focal_points array')
        for item in group:
            if not isinstance(item, dict) or set(item) != {'id', 'focus', 'reference_interval_s'}:
                raise ValueError('Expected focal point id, focus, reference_interval_s')
            key = item['id']
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', key) or key in ids:
                raise ValueError('Invalid or duplicate focal point ID')
            if not isinstance(item['focus'], str) or not item['focus'].strip():
                raise ValueError('Expected nonempty focus')
            indices = media.sample_indices(source['frame_count'], item['reference_interval_s'])
            start, end = item['reference_interval_s']
            if abs(end - source['frame_count'] / 24) <= .005:
                end = source['frame_count'] / 24
            ids.add(key)
            points.append(dict(point_id=key, kind=kind, focus=item['focus'], reference_interval_s=[start, end], sample_count=len(indices)))
    if not points:
        raise ValueError('Expected at least one focal point')
    return points


def judging_prompt(point, reference_duration, generated_duration, reference):
    folder = Path(__file__).parent
    template = (folder / 'judge.txt').read_text()
    values = dict(reference_start_s=round(point['reference_interval_s'][0], 3),
                  reference_end_s=round(point['reference_interval_s'][1], 3),
                  reference_clip_logical_duration_s=round(reference['logical_duration_s'], 3),
                  reference_full_duration_s=round(reference_duration, 3),
                  generated_full_logical_duration_s=round(generated_duration, 3),
                  sampling_fps=media.FPS, category=point['kind'], focus=point['focus'])
    return re.sub(r'\{\{\s*(\w+)\s*\}\}', lambda match: json.dumps(values[match[1]], ensure_ascii=False, allow_nan=False), template)


def score_config(config):
    from common import evaluation_model, responses_url
    return {'model': evaluation_model(config),
            'endpoint': responses_url(config), 'image_detail': 'high',
            'reasoning_effort': REASONING_EFFORT,
            'max_output_tokens': MAX_OUTPUT_TOKENS, 'max_retries': REQUEST_ATTEMPTS - 1,
            'timeout_s': 180, 'jpeg_qscale': 2,
            'sampling_fps': media.FPS}


def evaluate(case, generated_folder, output, config):
    from common import extract_frames, local_path, request_json, write_json
    source = case['metadata']
    points = focal_points(case['visual_retention'], source)
    generated_folder = Path(generated_folder)
    metadata = json.loads((generated_folder / 'metadata.json').read_text())
    generated_source = metadata['video']
    frames_entry = metadata.get('frames', {'path': 'frames'})
    generated_frames = generated_folder / (frames_entry['path'] if isinstance(frames_entry, dict) else frames_entry)
    output = local_path(output)
    output.mkdir(parents=True, exist_ok=True)
    generated_media = None
    settings = score_config(config)
    entries = [dict({k: v for k, v in point.items() if k != 'sample_count'},
                    status='pending', score=None) for point in points]
    def save():
        result = dict(video_id=case['video_id'], config=settings, generated_media=generated_media,
                      **aggregate(entries))
        write_json(output / 'result.json', result)
        return result

    layout = media.judge_layout(len(media.sample_indices(generated_source['frame_count'])), [p['sample_count'] for p in points])
    try:
        local_path(output / 'result.json')
        if not config.get('EVAL_API_KEY', '').strip():
            raise ValueError('EVAL_API_KEY is required')
        with tempfile.TemporaryDirectory(dir=output, prefix='.media-') as scratch:
            scratch = Path(scratch)
            reference_frames = extract_frames(case['video'], scratch / 'reference_frames')
            context = media.create_montage(generated_frames, generated_source, scratch / 'generated', layout=layout, label='GEN')
            generated_media = dict(media.public_record(context), source=generated_source)
            references = {}
            for point, entry in zip(points, entries):
                try:
                    bounds = tuple(point['reference_interval_s'])
                    if bounds not in references:
                        references[bounds] = media.create_montage(reference_frames, source, scratch / f'reference_{len(references):03d}', bounds=bounds, layout=layout, label='REF')
                    reference = references[bounds]
                    images = context['images'] + reference['images']
                    if len(images) > 50:
                        raise ValueError('Combined GEN and REF exceed 50 images')
                    prompt = judging_prompt(point, source['frame_count'] / 24, generated_source['frame_count'] / 24, reference)
                    entry['prompt'] = prompt
                    entry['reference_media'] = media.public_record(reference)
                    save()
                    response, usage = request_json(config, images, prompt, SCHEMA, name='visual_retention_score',
                                                   reasoning_effort=REASONING_EFFORT,
                                                   max_output_tokens=MAX_OUTPUT_TOKENS,
                                                   image_labels={
                                                       0: f'GEN: {len(context["images"])} images; each sheet has '
                                                          f'{context["rows"]} rows and {context["columns"]} columns. '
                                                          'Timestamps are full-video seconds.',
                                                       len(context['images']): f'REF: {len(reference["images"])} images; each sheet has '
                                                          f'{reference["rows"]} rows and {reference["columns"]} columns. '
                                                          'Timestamps are clip-local seconds.'},
                                                   attempts=REQUEST_ATTEMPTS, validate=validate_score)
                    entry.update(response=response, usage=usage)
                    entry.update(status='ok', score=validate_score(response),
                                 actual_model=usage.get('response_model', settings['model']))
                except Exception as error:
                    entry.update(status='error', score=None, error=str(error))
                save()
    except Exception as error:
        for entry in entries:
            if entry['status'] == 'pending':
                entry.update(status='error', score=None, error=str(error))
    return save()


def main(argv=None):
    from common import evaluation_model, responses_url, evaluation_inputs, evaluation_parser, load_env
    parser = evaluation_parser('Evaluate fine-grained visual information retention using GPT')
    args = parser.parse_args(argv)
    try:
        case, generated_folder = evaluation_inputs(args, metrics=('visual_retention',))
        points = focal_points(case['visual_retention'], case['metadata'])
        config = load_env()
        if args.check:
            print(json.dumps({'video_id': case['video_id'], 'check': 'ok', 'points': len(points),
                              'model': evaluation_model(config), 'endpoint': responses_url(config),
                              'api_key_configured': bool(config.get('EVAL_API_KEY'))}))
            return 0
        result = evaluate(case, generated_folder, args.output / str(args.video_id) / 'eval' / 'visual_retention', config)
        print(json.dumps({name: result[name] for name in
                          ('video_id', 'status', 'score', 'coverage')}, ensure_ascii=False))
        return 0 if result['status'] == 'ok' else 1
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        parser.exit(2, f'error: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
