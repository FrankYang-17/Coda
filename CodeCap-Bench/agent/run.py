"""Run one prepared video through an agent and Docker media tools."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common import case_inputs, load_env, local_path, write_json
from agent.backends import IMAGE_TOOLS, generation_config, prepare, summarize


class Docker:
    def __init__(self, image, executable='docker'):
        self.image, self.executable = image, executable

    def run(self, argv, *, mounts, timeout=900, network='none', env=None, stdout=None):
        name = 'codecap-' + uuid.uuid4().hex[:12]
        command = [self.executable, 'run', '--rm', '--init', '--name', name,
                   '--network', network, '--shm-size', '2g', '-w', '/workspace']
        for host, target, readonly in mounts:
            if ',' in str(host):
                raise ValueError('Docker bind paths must not contain commas')
            command += ['--mount', f'type=bind,source={host},target={target}' + (',readonly' if readonly else '')]
        for key, value in (env or {}).items():
            command += ['-e', f'{key}={value}']
        command += [self.image, *map(str, argv)]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=stdout or subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        try:
            output, error = process.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            subprocess.run([self.executable, 'kill', name], capture_output=True, timeout=30)
            if process.poll() is None:
                process.terminate()
            _, error = process.communicate(timeout=30)
            if sys.exc_info()[0] is KeyboardInterrupt:
                raise
            detail = (error or b'').decode('utf-8', 'replace')
            raise TimeoutError(f'Container exceeded {timeout} seconds\n{detail}'.rstrip()) from None
        return process.returncode, (output or b'').decode('utf-8', 'replace'), error.decode('utf-8', 'replace')


def freeze_artifact(workspace, artifact):
    html = workspace / 'index.html'
    if not html.is_file() or html.is_symlink():
        raise ValueError('Agent must produce a regular index.html')
    assets = workspace / 'assets'
    if assets.is_symlink():
        raise ValueError('Agent assets must not be links')
    if assets.exists():
        for item in assets.rglob('*'):
            if item.is_symlink() or not (item.is_file() or item.is_dir()):
                raise ValueError('Agent assets contain links or special files')
    artifact.mkdir(parents=True)
    shutil.copyfile(html, artifact / 'index.html')
    if assets.is_dir():
        shutil.copytree(assets, artifact / 'assets')


def checked_container(docker, argv, mounts, log, timeout, record=None, stage=None):
    stage = stage or argv[0]
    try:
        code, output, error = docker.run(argv, mounts=mounts, timeout=timeout)
    except TimeoutError as exc:
        code, output, error = None, '', str(exc)
    with log.open('a', encoding='utf-8') as stream:
        stream.write(f'\n[{stage}]\n' + output + error + '\n')
    if record is not None:
        value = {'exit_code': code, 'stderr': error}
        try:
            value['result'] = json.loads(output)
        except json.JSONDecodeError:
            value['output'] = output
        record['stages'][stage] = value
        write_json(log.parent.parent / 'task.json', record)
    if code is None:
        raise TimeoutError(error)
    if code:
        raise RuntimeError(f'{stage} failed; see logs/run.log')
    return output


def run_case(args):
    case = case_inputs(args.video_id, args.data_dir, metrics=())
    config = load_env()
    selected = generation_config(config, getattr(args, 'agent', 'pi'), getattr(args, 'provider', None))
    agent, key, model = selected['agent'], selected['api_key'], selected['model']
    docker = Docker(config.get('DOCKER_IMAGE', 'video2code-env:latest'))
    if args.check:
        print(json.dumps({'video_id': args.video_id, 'metadata': case['metadata'],
                          'image': docker.image, 'docker_available': bool(shutil.which('docker')),
                          'agent': agent, 'provider': selected['provider'], 'api': selected['api'],
                          'base_url': selected['base_url'], 'model': model}, ensure_ascii=False, indent=2))
        return 0
    image = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', docker.image],
                           capture_output=True, text=True, timeout=30)
    if image.returncode:
        raise ValueError(f'Docker image {docker.image} is unavailable; build it first')
    folder = local_path(args.output) / str(args.video_id)
    folder.mkdir(parents=True, exist_ok=False)
    logs = folder / 'logs'
    logs.mkdir()
    run_log = logs / 'run.log'
    scratch = folder / '.work'
    scratch.mkdir()
    record = {'video_id': args.video_id, 'agent': agent, 'provider': selected['provider'], 'api': selected['api'], 'model': model,
              'base_url': selected['base_url'],
              'started_at': datetime.now(timezone.utc).isoformat(), 'input': case['metadata'],
              'image': {'tag': docker.image, 'id': image.stdout.strip()}, 'generation': 'pending',
              'media': 'pending', 'preparation': 'pending', 'errors': [],
              'settings': {name: selected[name] for name in ('reasoning_effort', 'context_window', 'max_output_tokens')},
              'stages': {}}
    published = {'source': {'video_id': args.video_id,
                 'video': str(case['video'].relative_to(ROOT)) if case['video'].is_relative_to(ROOT) else str(case['video'])},
                 'video': None, 'frames': None, 'complete': False}
    write_json(folder / 'task.json', record)
    write_json(folder / 'metadata.json', published)
    phase = 'generation'
    try:
        inp, workspace, home = [scratch / name for name in ('input', 'workspace', 'home')]
        for path in (inp, workspace, home):
            path.mkdir()
        shutil.copyfile(case['video'], inp / 'reference.mp4')
        prompt = (ROOT / 'agent/prompt.txt').read_text(encoding='utf-8')
        prompt = prompt.replace('{{video_path}}', '/input/reference.mp4').replace('view_image', IMAGE_TOOLS[agent])
        (logs / 'prompt.txt').write_text(prompt, encoding='utf-8')
        backend = prepare(selected, home)
        record['settings'] = backend['settings']
        env = {'HOME': '/home/agent', 'TMPDIR': '/tmp', 'LANG': 'C.UTF-8',
               'PYTHONDONTWRITEBYTECODE': '1', **backend['env']}
        for name in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
                     'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY'):
            if config.get(name):
                env[name] = config[name]
        mounts = [(inp, '/input', True), (workspace, '/workspace', False), (home, '/home/agent', False)]
        checked_container(docker, [agent, '--version'], [], run_log, 120, record, 'version')
        print(f'Generating video_id={args.video_id} with agent={agent} model={model}', flush=True)
        code, stderr = None, ''
        with (scratch / 'events.jsonl').open('wb') as events:
            try:
                code, _, stderr = docker.run([*backend['command'], prompt], mounts=mounts,
                                             timeout=args.timeout, network='host', env=env, stdout=events)
            except TimeoutError as exc:
                stderr = str(exc)
                record['generation_timed_out'] = True
        stderr = stderr.replace(key, '[redacted]')
        with run_log.open('a', encoding='utf-8') as stream:
            stream.write('\n[generation]\n' + stderr + '\n')
        summary = summarize(agent, (scratch / 'events.jsonl').read_text(errors='replace'), code)
        if summary.get('agent_error'):
            summary['agent_error'] = summary['agent_error'].replace(key, '[redacted]')
        with run_log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(summary, ensure_ascii=False) + '\n')
        record['stages']['generation'] = dict(summary, stderr=stderr)
        record['generation'] = 'ok' if summary['completed'] else 'error'
        if code:
            record['errors'].append({'stage': 'generation', 'message': f'{agent} exited {code}; see logs/run.log'})
        elif code is None:
            record['errors'].append({'stage': 'generation', 'message': stderr})
        elif summary.get('agent_error'):
            record['errors'].append({'stage': 'generation', 'message': summary['agent_error']})
        sessions = sorted(home.glob(backend['session_glob']))
        trajectory = sessions[-1] if sessions else scratch / 'events.jsonl'
        (logs / 'trajectory.jsonl').write_text(trajectory.read_text(errors='replace').replace(key, '[redacted]'), encoding='utf-8')
        phase = 'export'
        artifact = scratch / 'artifact'
        freeze_artifact(workspace, artifact)
        shutil.copyfile(artifact / 'index.html', folder / 'index.html')
        if (artifact / 'assets').is_dir():
            shutil.copytree(artifact / 'assets', folder / 'assets')
        trusted_input = scratch / 'trusted-input'
        trusted_input.mkdir()
        shutil.copytree(artifact, trusted_input / 'artifact')
        trusted_workspace = scratch / 'trusted-workspace'
        trusted_workspace.mkdir()
        trusted_mounts = [(trusted_input, '/input', True), (trusted_workspace, '/workspace', False)]
        phase = 'render'
        reference = case['metadata']
        write_json(trusted_input / 'render.json', {'html': '/input/artifact/index.html',
                   'output': '/workspace/generated.mp4', 'metadata': '/workspace/render.json',
                   'fps': 24, 'width': reference['width'], 'height': reference['height'],
                   'duration': reference['duration_s'], 'html_start': 0})
        checked_container(docker, ['node', '/opt/tools/render.mjs'], trusted_mounts,
                          run_log, args.render_timeout, record, 'render')
        post = ROOT / 'agent/postprocess.py'
        params = ['--count', reference['frame_count'], '--width', reference['width'],
                  '--height', reference['height'], '--timeout', args.render_timeout]
        post_mounts = [(post, '/opt/postprocess.py', True), (trusted_workspace, '/render', True),
                       (folder, '/publish', False)]
        for step, path in (('verify', '/render/generated.mp4'), ('frames', '/publish')):
            phase = step
            value = json.loads(checked_container(docker, ['python3', '-I', '/opt/postprocess.py', step, path, *params],
                                                post_mounts, run_log, args.render_timeout + 60, record, step))
            updated = dict(published)
            if step == 'verify':
                phase = 'publish'
                shutil.move(trusted_workspace / 'generated.mp4', folder / 'video.mp4')
                value['path'] = 'video.mp4'
                updated['video'] = value
                updated['complete'] = True
            else:
                updated['frames'] = value
            write_json(folder / 'metadata.json', updated)
            published = updated
            if step == 'verify':
                record['media'] = 'ok'
        record['preparation'] = 'ok'
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
        if record['generation'] == 'pending':
            record['generation'] = 'error'
        if record['media'] != 'ok':
            record['media'] = 'error'
        else:
            record['preparation'] = 'error'
        message = str(exc).replace(key, '[redacted]')
        record['errors'].append({'stage': phase, 'message': message})
        with run_log.open('a', encoding='utf-8') as stream:
            stream.write(f'\n[{phase}] {message}\n')
    except KeyboardInterrupt:
        record['errors'].append({'stage': phase, 'message': 'Interrupted'})
        if record['media'] != 'ok':
            record['media'] = 'error'
        else:
            record['preparation'] = 'error'
        raise
    finally:
        shutil.rmtree(scratch)
        staging = folder / '.frames'
        if staging.is_dir():
            shutil.rmtree(staging)
        frames = folder / 'frames'
        if published['frames'] is None and frames.is_dir():
            shutil.rmtree(frames)
        record['ended_at'] = datetime.now(timezone.utc).isoformat()
        record['status'] = 'completed' if record['media'] == 'ok' else 'failed'
        write_json(folder / 'task.json', json.loads(json.dumps(record).replace(key, '[redacted]')))
    print(f'Result: {folder} ({record["status"]})', flush=True)
    return 0 if record['media'] == 'ok' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', choices=('pi', 'codex', 'claude'), default='pi')
    parser.add_argument('--provider', choices=('openai', 'anthropic', 'gemini'))
    parser.add_argument('--video-id', required=True, type=int)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs')
    parser.add_argument('--check', action='store_true', help='Read-only video/config preflight; no API call')
    parser.add_argument('--timeout', type=int, default=3600)
    parser.add_argument('--render-timeout', type=int, default=900,
                        help='HTML-to-MP4 timeout in seconds (default: 900); preparation runs separately')
    args = parser.parse_args(argv)
    try:
        if min(args.timeout, args.render_timeout) <= 0:
            raise ValueError('Timeouts must be positive')
        return run_case(args)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
