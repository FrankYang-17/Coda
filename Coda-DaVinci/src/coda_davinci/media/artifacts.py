"""Read delivery manifests and copy explicit, regular-file snapshots."""

import hashlib
import json
from pathlib import Path
import shutil

KINDS = ("asset", "camera", "review")


def local_path(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Expected a nonempty relative path using '/' separators")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or (not relative.parts):
        raise ValueError(f"Invalid relative path: {value}")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Symlinks are not allowed in task packages: {value}")
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes package: {value}")
    return current


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_files(source: Path, target: Path, paths: list[str]) -> dict[str, str]:
    """Copy files/directories preserving relative paths; reject links and special files."""
    files = {}
    for value in paths:
        path = local_path(source, value)
        entries = [path, *path.rglob("*")] if path.is_dir() else [path]
        for entry in entries:
            relative = entry.relative_to(source).as_posix()
            checked = local_path(source, relative)
            if ".harness" in checked.relative_to(source).parts:
                raise ValueError("Execution records cannot be copied as task inputs or assets")
            if checked.is_dir():
                continue
            if not checked.is_file():
                raise ValueError(f"Not a regular file: {relative}")
            files[relative] = checked
    hashes = {}
    for relative, source_file in sorted(files.items()):
        destination = local_path(target, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, destination)
        hashes[relative] = file_hash(destination)
    return hashes


def text_list(value, name: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or any((not isinstance(x, str) or not x.strip() for x in value)):
        raise ValueError(f"{name} must be a list of nonempty strings")
    if nonempty and (not value):
        raise ValueError(f"{name} cannot be empty")
    return value


def read_delivery(manifest_path: Path) -> dict:
    """Read the transport fields only; asset quality and metadata are agent-owned."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Delivery manifest must be a JSON object")
    text_list(data.get("files"), "files")
    return data
