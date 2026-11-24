import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from dotenv import load_dotenv


def doctor():
    checks = []
    for name, value in (
        ("Blender", os.environ.get("BLENDER_EXECUTABLE", "external/blender/blender")),
        ("FFmpeg", os.environ.get("FFMPEG_EXECUTABLE", "ffmpeg")),
        ("FFprobe", os.environ.get("FFPROBE_EXECUTABLE", "ffprobe")),
        ("bubblewrap", os.environ.get("BWRAP_EXECUTABLE", "bwrap")),
        ("NVIDIA driver", "nvidia-smi"),
    ):
        found = shutil.which(str(Path(value).expanduser()))
        checks.append((name, bool(found), found or value))
    for key in (
        "MODEL",
        "API_KEY",
        "BASE_URL",
        "VIDEO_MODEL",
        "VIDEO_MODEL_API_KEY",
        "VIDEO_MODEL_BASE_URL",
        "IMAGE_MODEL",
        "IMAGE_API_KEY",
        "IMAGE_BASE_URL",
    ):
        checks.append(
            (
                key,
                bool(os.environ.get(key, "").strip()),
                "configured" if os.environ.get(key) else "missing",
            )
        )
    for name, ok, detail in checks:
        print(f"{('OK' if ok else 'MISSING'):7} {name}: {detail}")
    if not all((ok for (_, ok, _) in checks)):
        return 1
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
        text=True,
        capture_output=True,
        timeout=20,
    )
    print(result.stdout.strip() or result.stderr.strip() or "No NVIDIA GPU was reported")
    return result.returncode or (0 if result.stdout.strip() else 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Coda-DaVinci task-adaptive video generation")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local executables and configuration")
    run = sub.add_parser("generate", help="Generate a video or a sequence of continued clips")
    prompts = run.add_mutually_exclusive_group(required=True)
    prompts.add_argument("--prompt")
    prompts.add_argument("--prompt-file", type=Path)
    run.add_argument("--output-dir", type=Path, default=Path("outputs"))
    run.add_argument("--duration", type=int, default=8)
    run.add_argument("--reference-image", action="append", default=[], metavar="LABEL=PATH")
    run.add_argument("--reference-video", action="append", default=[], metavar="LABEL=PATH")
    run.add_argument("--interactions", type=Path, help="JSON file with sequential turns")
    run.add_argument("--parallel-agents", type=int, default=3)
    run.add_argument("--parallel-renders", type=int, default=1)
    args = parser.parse_args(argv)
    if args.env_file.is_file():
        load_dotenv(args.env_file, override=False)
    elif args.env_file != Path(".env"):
        parser.error(f"Configuration file not found: {args.env_file}")
    if args.command == "doctor":
        return doctor()
    if min(args.duration, args.parallel_agents, args.parallel_renders) <= 0:
        parser.error("Duration and concurrency must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from coda_davinci.application import generate

    prompt = args.prompt_file.read_text() if args.prompt_file else args.prompt
    (episode, result) = generate(
        prompt,
        output_root=args.output_dir,
        duration=args.duration,
        reference_images=args.reference_image,
        reference_videos=args.reference_video,
        interactions=args.interactions,
        parallel_agents=args.parallel_agents,
        parallel_renders=args.parallel_renders,
    )
    print(json.dumps({"episode": str(episode), **result}, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "completed" else 2
