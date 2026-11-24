"""Filesystem sandbox construction for Blender and FFmpeg child processes.

The sandbox keeps the host filesystem visible for reading, but remounts it
read-only.  The episode workspace and isolated temporary filesystems are
writable.  The host /dev is passed through with device access so GPU drivers
can reach their complete device tree, including nested capability nodes.
A fresh writable procfs permits CUDA initialization; Blender's persistent CUDA
cache can be mounted separately without exposing the rest of the host home for writing.
"""

import shutil
from pathlib import Path


def wrap_command(
    command: list[str],
    *,
    executable: Path,
    workspace: Path,
    cwd: Path,
    read_only: tuple[Path, ...] = (),
    env_overrides: dict[str, str | None] | None = None,
    hidden_paths: tuple[Path, ...] = (),
    cuda_cache: Path | None = None,
) -> list[str]:
    """Return *command* wrapped in a read-only-root bwrap environment.

    CUDA initialization needs writable procfs entries. Do not inherit /proc
    through the read-only root bind or silently fall back to that configuration.

    Environment overrides apply inside the sandbox, after bwrap has loaded its
    own libraries. A None value removes the variable from the child environment.
    Hidden directories are masked before restoring the writable workspace.
    """
    bwrap = shutil.which(str(executable))
    if bwrap is None:
        raise FileNotFoundError(f"bubblewrap executable not found: {executable}")
    workspace = workspace.resolve()
    cwd = cwd.resolve()
    if workspace == Path("/") or not workspace.is_dir():
        raise ValueError("Sandbox workspace must be an existing non-root directory")
    if not cwd.is_relative_to(workspace):
        raise ValueError("Sandbox cwd must be inside the workspace")
    protected = []
    for path in read_only:
        path = path.resolve()
        if not path.is_relative_to(workspace) or not path.is_dir():
            raise ValueError("Read-only task directories must exist within the workspace")
        protected.extend(("--ro-bind", str(path), str(path)))
    cache_args = []
    if cuda_cache is not None:
        cache = cuda_cache.resolve()
        if (
            not cache.is_dir()
            or workspace.is_relative_to(cache)
            or any(
                (
                    cache.is_relative_to(p.resolve()) or p.resolve().is_relative_to(cache)
                    for p in (*read_only, *hidden_paths)
                )
            )
        ):
            raise ValueError(
                "CUDA cache must be separate from protected/hidden paths and workspace ancestors"
            )
        cache_args = ["--bind", str(cache), str(cache)]
    environment_args = []
    for name, value in (env_overrides or {}).items():
        if value is None:
            environment_args.extend(("--unsetenv", name))
        else:
            environment_args.extend(("--setenv", name, value))
    hidden_args = []
    for path in hidden_paths:
        path = path.resolve()
        if path == Path("/") or not path.is_dir():
            raise ValueError("Hidden paths must be existing non-root directories")
        hidden_args.extend(("--tmpfs", str(path)))
    isolated_args = ["--unshare-pid", "--unshare-net"] if hidden_paths else []
    wrapped = [
        bwrap,
        "--die-with-parent",
        "--ro-bind",
        "/",
        "/",
        *hidden_args,
        "--bind",
        str(workspace),
        str(workspace),
        "--tmpfs",
        "/tmp",
        *isolated_args,
        "--proc",
        "/proc",
        "--dev-bind",
        "/dev",
        "/dev",
        "--dir",
        "/tmp/code2video-home",
        *protected,
        *cache_args,
        *environment_args,
        "--chdir",
        str(cwd),
        "--",
    ]
    return wrapped + command
