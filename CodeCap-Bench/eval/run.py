"""Prepare one generated video and run the selected evaluation metrics."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common import case_inputs, evaluation_inputs, evaluation_parser, local_path, probe_video, write_json
from agent.postprocess import frames

METRICS = ('object_st_iou', 'visual_retention', 'visual_similarity')


def generated_info(case, generated):
    info = probe_video(generated)
    for field in ('width', 'height', 'fps', 'frame_count'):
        if info[field] != case['metadata'][field]:
            raise ValueError(f'Generated {field} differs: reference={case["metadata"][field]}, '
                             f'generated={info[field]}')
    return info


def prepare_media(case, folder, metrics):
    if not any(metric in ('object_st_iou', 'visual_retention') for metric in metrics):
        return
    published = json.loads((folder / 'metadata.json').read_text())
    reference = case['metadata']
    params = (reference['frame_count'], reference['width'], reference['height'], 900)
    if published.get('frames'):
        return
    try:
        published['frames'] = frames(folder, *params)
        published['complete'] = True
        write_json(folder / 'metadata.json', published)
    finally:
        staging = folder / '.frames'
        if staging.is_dir():
            shutil.rmtree(staging)


def prepare_generated(case, generated, folder, metrics):
    info = generated_info(case, generated)
    info.pop('time_origin_s')
    info.pop('duration_s')
    folder = local_path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(generated, folder / 'video.mp4')
    info['path'] = 'video.mp4'
    published = {'source': {'video_id': case['video_id'],
                 'video': str(case['video'])},
                 'video': info, 'frames': None, 'complete': True}
    write_json(folder / 'metadata.json', published)
    prepare_media(case, folder, metrics)


def run_metrics(args, folder, metrics):
    summary = {'video_id': args.video_id, 'status': 'ok', 'metrics': {}}
    for metric in metrics:
        result_path = local_path(folder / 'eval' / metric) / 'result.json'
        if not args.check:
            result_path.unlink(missing_ok=True)
        environment = {
            'object_st_iou': '.venv-sam',
            'visual_retention': '.venv',
            'visual_similarity': '.venv-vjepa',
        }[metric]
        default = ROOT / environment / 'bin/python'
        interpreter = str(default)
        if args.check and not Path(interpreter).is_file():
            interpreter = sys.executable
        command = [interpreter, '-I', '-B', str(ROOT / 'eval' / metric / 'evaluate.py'),
                   '--video-id', str(args.video_id), '--data-dir', str(args.data_dir),
                   '--output', str(args.output)]
        if metric == 'visual_similarity':
            command += ['--device', args.device]
        if args.check:
            command.append('--check')
        print(f'Running {metric} for video_id={args.video_id}', flush=True)
        error = None
        try:
            code = subprocess.run(command).returncode
        except OSError as exc:
            code = 127
            error = f'Cannot start {metric}: {exc}. Run bash scripts/setup.sh.'
        detail = {}
        if not args.check:
            try:
                detail = json.loads(result_path.read_text())
                if not isinstance(detail, dict):
                    raise ValueError('Result must be a JSON object')
                if metric == 'visual_retention' and not {'coverage', 'static_score', 'dynamic_score'} <= detail.keys():
                    raise ValueError('Visual retention result is missing coverage or component scores')
                if metric == 'visual_retention':
                    for field in ('static_score', 'dynamic_score'):
                        component = detail[field]
                        if component is not None and (type(component) not in (int, float) or not 0 <= component <= 5):
                            raise ValueError(f'{field} must be null or a finite number in 0..5')
                if code == 0:
                    expected = 'scored' if metric == 'visual_similarity' else 'ok'
                    if detail.get('status') != expected:
                        raise ValueError(f'Expected successful status {expected}, got {detail.get("status")}')
                    score = detail.get('iou' if metric == 'object_st_iou' else 'score')
                    minimum, maximum = {'object_st_iou': (0, 1), 'visual_retention': (0, 5),
                                        'visual_similarity': (-1, 1)}[metric]
                    if type(score) not in (int, float) or not minimum <= score <= maximum:
                        raise ValueError(f'Score must be a finite number in {minimum}..{maximum}')
                    if metric == 'visual_retention' and (type(detail['coverage']) not in (int, float) or detail['coverage'] != 1):
                        raise ValueError('Visual retention requires complete coverage')
            except (OSError, ValueError) as exc:
                detail = {}
                error = error or f'Invalid result for {metric}: {exc}'
        success = code == 0 and error is None
        entry = {'status': detail.get('status', 'ok') if success else 'error',
                 'score': detail.get('iou' if metric == 'object_st_iou' else 'score') if success else None,
                 'exit_code': code, 'result': f'{metric}/result.json' if detail else None}
        if not success:
            summary['status'] = 'error'
            entry['error'] = error or detail.get('error', f'{metric} exited with code {code}')
        if metric == 'object_st_iou' and detail.get('expected_targets'):
            entry['coverage'] = detail['scored_targets'] / detail['expected_targets']
        elif metric == 'visual_retention' and detail:
            entry.update(coverage=detail['coverage'], static_score=detail['static_score'],
                         dynamic_score=detail['dynamic_score'])
        summary['metrics'][metric] = entry
    if not args.check:
        path = folder / 'eval/result.json'
        write_json(path, summary)
        print(f'Evaluation results: {path}', flush=True)
    return 0 if summary['status'] == 'ok' else 1


def main(argv=None):
    parser = evaluation_parser(__doc__)
    parser.add_argument('--generated', type=Path, help='External generated MP4 to evaluate')
    parser.add_argument('--metrics', default=','.join(METRICS),
                        help='Comma-separated metrics: object_st_iou,visual_retention,visual_similarity')
    parser.add_argument('--device', default='cuda:0', help='V-JEPA device: cpu or cuda:N')
    args = parser.parse_args(argv)
    metrics = tuple(dict.fromkeys(name.strip() for name in args.metrics.split(',')))
    if any(name not in METRICS for name in metrics):
        parser.error('--metrics must contain object_st_iou, visual_retention or visual_similarity')
    args.output = local_path(args.output)
    folder = local_path(args.output / str(args.video_id))
    try:
        if args.generated:
            case = case_inputs(args.video_id, args.data_dir, metrics=())
            if args.check:
                info = generated_info(case, args.generated)
                checked_case = case_inputs(args.video_id, args.data_dir, metrics=metrics)
                if 'object_st_iou' in metrics:
                    from eval.object_st_iou.evaluate import validate_targets
                    validate_targets(checked_case['object_st_iou'], case['metadata'])
                if 'visual_retention' in metrics:
                    from eval.visual_retention.evaluate import focal_points
                    focal_points(checked_case['visual_retention'], case['metadata'])
                if 'visual_similarity' in metrics:
                    from eval.visual_similarity.evaluate import windows
                    windows(info['frame_count'])
                print(json.dumps({'video_id': args.video_id, 'status': 'checked',
                                  'metrics': metrics, 'generated': info, 'output': str(folder)}, indent=2))
                return 0
            prepare_generated(case, args.generated, folder, metrics)
        else:
            case, folder = evaluation_inputs(args, metrics=())
            if not args.check:
                prepare_media(case, folder, metrics)
        return run_metrics(args, folder, metrics)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
