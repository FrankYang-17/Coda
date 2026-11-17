"""Small shared helpers for one video and configurable Responses API requests."""
import argparse
import base64
from fractions import Fraction
import http.client
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
REQUEST_LIMIT_BYTES = 60_000_000
EVAL_REASONING_EFFORT = 'medium'
ANNOTATION_FILES = {'object_st_iou': 'object_spatiotemporal.jsonl',
                    'visual_retention': 'focal_points.jsonl'}


class ResponseRefusal(ValueError):
    """A permanent model refusal, distinct from a malformed response."""


def local_path(path):
    """Normalize a filesystem path, including user-selected external directories."""
    return Path(path).expanduser().resolve()


def load_env(path=None):
    config = {}
    path = Path(path) if path is not None else ROOT / '.env'
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:]
            key, sep, value = line.partition('=')
            key, value = key.strip(), value.strip()
            if not sep or not key.isidentifier():
                raise ValueError(f'{path.name}:{number}: expected KEY=value')
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            config[key] = value
    return {**config, **os.environ}


def api_base_url(config, prefix='OPENAI'):
    """Read a configured API prefix."""
    name = prefix + '_BASE_URL'
    base = config.get(name, '').strip().rstrip('/')
    if not base:
        raise ValueError(f'Set {name} in .env')
    parts = urllib.parse.urlsplit(base)
    if (parts.scheme not in ('http', 'https') or not parts.hostname or
            parts.username is not None or parts.password is not None or parts.query or parts.fragment):
        raise ValueError(f'{name} must be an HTTP(S) API base URL without credentials, query, or fragment')
    if parts.path.endswith(('/responses', '/chat/completions', '/messages')):
        raise ValueError(f'{name} must be the API prefix, not a request endpoint')
    return base


def responses_url(config):
    return api_base_url(config, 'EVAL') + '/responses'


def evaluation_model(config):
    model = config.get('EVAL_MODEL', '').strip()
    if not model:
        raise ValueError('Set EVAL_MODEL in .env')
    return model


def write_json(path, value):
    path = Path(path).expanduser()
    path = local_path(path.parent) / path.name
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.' + path.name + '-',
                                     encoding='utf-8', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run(command, timeout=900):
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    process = subprocess.run([str(x) for x in command], capture_output=True,
                             text=True, timeout=timeout, env=env)
    if process.returncode:
        raise RuntimeError(f'{Path(str(command[0])).name} failed: {process.stderr.strip()[-2000:]}')
    return process.stdout


def probe_video(video, fps=24):
    video = Path(video).resolve(strict=True)
    data = json.loads(run(['ffprobe', '-v', 'error', '-threads', '2', '-select_streams', 'v:0',
                          '-show_frames', '-show_entries',
                          'stream=width,height,avg_frame_rate,time_base:frame=best_effort_timestamp',
                          '-of', 'json', video], timeout=600))
    if not data.get('streams') or not data.get('frames'):
        raise ValueError('Video has no decoded frames')
    stream, frames = data['streams'][0], data['frames']
    time_base = Fraction(stream['time_base'])
    if Fraction(stream['avg_frame_rate']) != fps or any(
            int(frame['best_effort_timestamp']) * time_base != Fraction(i, fps)
            for i, frame in enumerate(frames)):
        raise ValueError(f'Video must be {fps} fps with exact zero-based CFR timestamps')
    if stream['width'] % 2 or stream['height'] % 2:
        raise ValueError('Video dimensions must be even for rendering')
    return {'path': str(video), 'width': stream['width'], 'height': stream['height'],
            'fps': fps, 'frame_count': len(frames), 'duration_s': len(frames) / fps,
            'time_origin_s': 0}


def annotation_row(path, video_id):
    rows = {}
    for number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            key = row['video_id']
            if type(key) is not int or key <= 0:
                raise ValueError('video_id must be a positive integer')
            if key in rows:
                raise ValueError(f'Duplicate video_id {key}')
            rows[key] = row
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f'{Path(path).name}:{number}: invalid annotation') from exc
    if video_id not in rows:
        raise ValueError(f'Unknown video_id {video_id} in {Path(path).name}')
    return rows[video_id]


def case_inputs(video_id, data_dir=None, metrics=('object_st_iou', 'visual_retention')):
    if type(video_id) is not int or video_id <= 0:
        raise ValueError('video_id must be a positive integer')
    data_dir = Path(data_dir) if data_dir is not None else ROOT / 'data'
    annotations = {metric: annotation_row(data_dir / ANNOTATION_FILES[metric], video_id)
                   for metric in metrics if metric in ANNOTATION_FILES}
    video = (data_dir / 'videos' / f'{video_id}.mp4').resolve(strict=True)
    return {'video_id': video_id, 'video': video, **annotations,
            'metadata': probe_video(video)}


def extract_frames(video, folder):
    folder = local_path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '2', '-i', video,
         '-map', '0:v:0', '-an', '-vsync', '0', '-q:v', '2', '-threads', '2',
         '-start_number', '0', folder / '%05d.jpg'])
    return folder


def evaluation_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--video-id', type=int, required=True)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs',
                        help='Output root containing <video-id>/ from agent/run.py')
    parser.add_argument('--check', action='store_true', help='Read-only input preflight; no API/GPU work')
    return parser


def evaluation_inputs(args, metrics=('object_st_iou', 'visual_retention')):
    case = case_inputs(args.video_id, args.data_dir, metrics=metrics)
    folder = local_path(local_path(args.output) / str(args.video_id))
    published = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
    if published.get('complete') is not True:
        raise ValueError('Generated media package is not complete')
    source, video = published.get('source', {}), published.get('video', {})
    if source.get('video_id') != args.video_id:
        raise ValueError('Generated video source differs from selected case')
    for field in ('width', 'height', 'fps', 'frame_count'):
        if video.get(field) != case['metadata'][field]:
            raise ValueError(f'Reference/generated {field} differs')
    if not local_path(folder / 'video.mp4').is_file():
        raise ValueError('Generated media is missing video.mp4')
    if not any(metric in ('object_st_iou', 'visual_retention') for metric in metrics):
        return case, folder
    frames = published.get('frames')
    if not isinstance(frames, dict) or frames.get('path') != 'frames':
        raise ValueError('Generated package requires frames metadata with path=frames')
    if not local_path(folder / 'frames').is_dir():
        raise ValueError('Generated media is missing frames')
    names = [f'{index:05d}.jpg' for index in range(video['frame_count'])]
    if sorted(path.name for path in (folder / 'frames').iterdir()) != names:
        raise ValueError('Generated frames have incomplete coverage')
    for name in names:
        if not local_path(folder / 'frames' / name).is_file():
            raise ValueError(f'Generated media is missing frame {name}')
    return case, folder


def parse_response(response):
    if not isinstance(response, dict):
        raise ValueError('GPT response must be a JSON object')
    if response.get('status') != 'completed' or response.get('error'):
        raise ValueError('GPT response failed or is incomplete')
    texts = []
    for item in response.get('output', []):
        if item.get('status') in ('failed', 'incomplete'):
            raise ValueError('GPT output is incomplete')
        for part in item.get('content', []):
            if part.get('type') == 'refusal':
                raise ResponseRefusal('GPT refused the request')
            if part.get('type') == 'output_text':
                texts.append(part['text'])
    try:
        result = json.loads(''.join(texts))
    except json.JSONDecodeError as exc:
        raise ValueError('GPT did not return a JSON object') from exc
    if not isinstance(result, dict):
        raise ValueError('GPT must return a JSON object')
    usage = dict(response.get('usage') or {})
    if response.get('model'):
        usage['response_model'] = response['model']
    return result, usage


def request_json(config, images, prompt, schema, name='result', *,
                 reasoning_effort=EVAL_REASONING_EFFORT, max_output_tokens=16384, attempts=3,
                 validate=None, image_labels=None):
    key = config.get('EVAL_API_KEY', '').strip()
    if not key:
        raise ValueError('Set EVAL_API_KEY in .env')
    if not 1 <= len(images) <= 50:
        raise ValueError('Evaluation requests require 1–50 images')
    content = []
    for index, image in enumerate(images):
        if image_labels and index in image_labels:
            content.append({'type': 'input_text', 'text': image_labels[index]})
        data = Path(image).read_bytes()
        mime = 'image/png' if data.startswith(b'\x89PNG') else 'image/jpeg'
        content.append({'type': 'input_image', 'detail': 'high',
                        'image_url': f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')})
    content.append({'type': 'input_text', 'text': prompt})
    image_parts = [part for part in content if part['type'] == 'input_image']
    payload = {'model': evaluation_model(config), 'input': [{'role': 'user', 'content': content}],
               'max_output_tokens': max_output_tokens,
               'text': {'format': {'type': 'json_schema', 'name': name, 'strict': True, 'schema': schema}}}
    payload['reasoning'] = {'effort': reasoning_effort}
    body = json.dumps(payload, allow_nan=False).encode('utf-8')
    scale = 100
    # Fit the serialized request below the byte limit with 5% Lanczos resize steps.
    # Each attempt reads the original finished image; no temporary files.
    for percent in range(95, 0, -5):
        if len(body) < REQUEST_LIMIT_BYTES:
            break
        for image, part in zip(images, image_parts):
            png = part['image_url'].startswith('data:image/png;')
            codec = ['-c:v', 'png'] if png else ['-q:v', '2', '-c:v', 'mjpeg']
            resized = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '2',
                '-i', str(image), '-vf',
                f"scale=w='max(1,trunc(iw*{percent}/100))':h='max(1,trunc(ih*{percent}/100))':flags=lanczos",
                '-frames:v', '1', '-threads', '2', *codec,
                '-f', 'image2pipe', 'pipe:1'], capture_output=True, timeout=180)
            signature = b'\x89PNG\r\n\x1a\n' if png else b'\xff\xd8'
            if resized.returncode or not resized.stdout.startswith(signature):
                raise ValueError('Cannot resize evaluation image')
            mime = 'image/png' if png else 'image/jpeg'
            part['image_url'] = f'data:{mime};base64,' + base64.b64encode(resized.stdout).decode('ascii')
        scale = percent
        body = json.dumps(payload, allow_nan=False).encode('utf-8')
    if len(body) >= REQUEST_LIMIT_BYTES:
        raise ValueError('Evaluation request cannot fit below the image request budget')
    request = urllib.request.Request(responses_url(config), data=body,
                                    headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                result, usage = parse_response(json.load(response))
                if validate is not None:
                    validate(result)
                usage.update(image_scale_percent=scale, request_body_bytes=len(body))
                return result, usage
        except urllib.error.HTTPError as exc:
            # Never include request credentials or unrestricted response bodies in errors.
            error = RuntimeError(f'Responses API returned HTTP {exc.code}')
            retry = exc.code in (408, 425, 429, 500, 502, 503, 504)
            exc.close()
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            error = RuntimeError('Cannot connect to the configured Responses API')
            retry = True
        except ResponseRefusal as exc:
            error = exc
            retry = False
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            error = exc
            retry = True
        if not retry or attempt == attempts - 1:
            raise error from None
        time.sleep(min(.25 * 2 ** attempt, 4))
