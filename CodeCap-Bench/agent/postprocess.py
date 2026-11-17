"""Media verification and frame extraction (standard library + ffmpeg/ffprobe).

  python3 agent/postprocess.py verify  <video> --fps 24 --count N --width W --height H
  python3 agent/postprocess.py frames  <publish_dir> --count N --width W --height H

Each command prints one JSON object on stdout; paths in it are relative to the
publish directory.
"""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys


def run(command, timeout):
    process = subprocess.run([str(item) for item in command], capture_output=True, text=True, timeout=timeout)
    if process.returncode:
        raise RuntimeError(f'{Path(str(command[0])).name} failed: {process.stderr.strip()}')
    return process.stdout


def video_info(path, fps, count, width, height, timeout):
    """Check decoded PTS, frame count and dimensions."""
    data = json.loads(run([
        'ffprobe', '-v', 'error', '-threads', '2', '-select_streams', 'v:0',
        '-show_frames', '-show_entries',
        'stream=width,height,avg_frame_rate,time_base,codec_name,pix_fmt:frame=best_effort_timestamp',
        '-of', 'json', path], timeout))
    if not data.get('streams') or not data.get('frames'):
        raise ValueError(f'Rendered media has no decoded video frames: {Path(path).name}')
    stream, frames = data['streams'][0], data['frames']
    time_base = Fraction(stream['time_base'])
    if (len(frames) != count or stream['width'] != width or stream['height'] != height or
            Fraction(stream['avg_frame_rate']) != fps or any(
                int(frame['best_effort_timestamp']) * time_base != Fraction(i, fps)
                for i, frame in enumerate(frames))):
        raise ValueError(f'Video dimensions/frame count/{fps}-fps PTS mismatch: {Path(path).name} '
                         f'({len(frames)} frames, {stream["width"]}x{stream["height"]}, {stream["avg_frame_rate"]})')
    return {'path': Path(path).name, 'width': width, 'height': height,
            'fps': fps, 'frame_count': count,
            'encoding': {'codec': stream['codec_name'], 'pixel_format': stream['pix_fmt']}}


def frames(folder, count, width, height, timeout):
    temporary = folder / '.frames'
    temporary.mkdir()
    run(['ffmpeg', '-v', 'error', '-threads', '2', '-i', folder / 'video.mp4',
         '-map', '0:v:0', '-an', '-vsync', '0', '-q:v', '2', '-threads', '2',
         '-start_number', '0', temporary / '%05d.jpg'], timeout)
    names = [f'{index:05d}.jpg' for index in range(count)]
    if sorted(path.name for path in temporary.iterdir()) != names:
        raise ValueError('Extracted original JPEG frame count mismatch')
    for name in names:
        path = temporary / name
        with path.open('rb') as stream:
            if stream.read(2) != b'\xff\xd8':
                raise ValueError(f'Invalid original JPEG frame: {name}')
        path.chmod(0o644)
    temporary.replace(folder / 'frames')
    return {'path': 'frames'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('verify', 'frames'))
    parser.add_argument('path')
    parser.add_argument('--fps', type=int, default=24)
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--width', type=int, required=True)
    parser.add_argument('--height', type=int, required=True)
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    path = Path(args.path)
    if args.command == 'verify':
        result = video_info(path, args.fps, args.count, args.width, args.height, args.timeout)
    else:
        result = frames(path, args.count, args.width, args.height, args.timeout)
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)
