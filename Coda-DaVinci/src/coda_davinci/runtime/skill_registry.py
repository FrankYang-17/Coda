"""Bundled skill discovery, episode snapshots and per-agent progressive loading."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid
import yaml
from coda_davinci.media.artifacts import copy_files, file_hash, local_path, text_list

BUNDLED_SKILLS = Path(__file__).parents[1] / "resources/skills"
NAME_PATTERN = "[a-z0-9]+(?:-[a-z0-9]+)*"
CATALOG_CHAR_LIMIT = 8000


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    root: Path
    manifest: str
    version: str
    files: dict[str, str]
    allow_implicit: bool

    def metadata(self):
        return {
            "name": self.name,
            "description": self.description,
            "path": f".skills/{self.name}/{self.manifest}",
        }


def read_metadata(path: Path) -> dict:
    content = path.read_text(encoding="utf-8-sig")
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: SKILL.md must start with YAML frontmatter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError(f"{path}: unterminated YAML frontmatter")
    metadata = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(metadata, dict):
        raise ValueError(f"{path}: frontmatter must be a mapping")
    for key in ("name", "description"):
        if not isinstance(metadata.get(key), str) or not metadata[key].strip():
            raise ValueError(f"{path}: {key} must be a nonempty string")
    if len(metadata["name"]) > 64 or re.fullmatch(NAME_PATTERN, metadata["name"]) is None:
        raise ValueError(f"{path}: invalid skill name (use lowercase letters, digits and hyphens)")
    if not "\n".join(lines[end + 1 :]).strip():
        raise ValueError(f"{path}: skill instructions cannot be empty")
    return metadata


class SkillRegistry:
    """One immutable-on-disk snapshot shared by all agents in an episode."""

    def __init__(self, root: Path, skills: dict[str, Skill]):
        self.root = root
        self.skills = skills

    @classmethod
    def snapshot(cls, destination: Path, settings=None):
        if destination.exists():
            raise ValueError(f"Skill snapshot already exists: {destination}")
        staging = destination.with_name(f".{destination.name}_{uuid.uuid4().hex}")
        staging.mkdir(parents=True)
        entries = {}
        try:
            for directory in sorted(BUNDLED_SKILLS.iterdir()):
                if directory.is_symlink():
                    raise ValueError(f"Symlinked skill packages are unsupported: {directory}")
                if (
                    settings is not None
                    and getattr(settings, "generation_route", "blender") == "direct"
                ):
                    from coda_davinci.workflow.generation_routing import DIRECT_SKILLS

                    if directory.name not in DIRECT_SKILLS:
                        continue
                if directory.name == "direct-video" and (
                    settings is None or getattr(settings, "generation_route", "blender") != "direct"
                ):
                    continue
                if not directory.is_dir() or directory.name.startswith("."):
                    continue
                manifests = [p for p in directory.iterdir() if p.name.lower() == "skill.md"]
                if not manifests:
                    continue
                if len(manifests) != 1:
                    raise ValueError(f"{directory}: expected exactly one SKILL.md")
                files = []
                for path in sorted(directory.rglob("*")):
                    relative = path.relative_to(directory)
                    if "__pycache__" in relative.parts or path.suffix == ".pyc":
                        continue
                    checked = local_path(directory, relative.as_posix())
                    if not checked.is_dir():
                        files.append(relative.as_posix())
                package = staging / directory.name
                hashes = copy_files(directory, package, files)
                manifest = manifests[0].name
                metadata = read_metadata(package / manifest)
                name = metadata["name"]
                if name != directory.name:
                    raise ValueError(f"{directory}: directory name must match skill name {name!r}")
                if name in entries:
                    raise ValueError(f"Duplicate bundled skill name: {name}")
                allow_implicit = True
                policy_path = package / "agents" / "openai.yaml"
                if policy_path.is_file():
                    config = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
                    if not isinstance(config, dict) or not isinstance(
                        config.get("policy", {}), dict
                    ):
                        raise ValueError(
                            f"{policy_path}: expected a mapping with optional policy mapping"
                        )
                    allow_implicit = config.get("policy", {}).get("allow_implicit_invocation", True)
                    if type(allow_implicit) is not bool:
                        raise ValueError(
                            f"{policy_path}: allow_implicit_invocation must be boolean"
                        )
                version = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
                entries[name] = {
                    "name": name,
                    "description": metadata["description"].strip(),
                    "manifest": manifest,
                    "version": version,
                    "files": hashes,
                    "allow_implicit": allow_implicit,
                }
            (staging / "index.json").write_text(
                json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            staging.rename(destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return cls(
            destination,
            {name: Skill(root=destination / name, **entry) for (name, entry) in entries.items()},
        )

    def get(self, name: str) -> Skill:
        if not isinstance(name, str) or name not in self.skills:
            raise ValueError(f"Unknown skill: {name!r}; available names: {sorted(self.skills)}")
        return self.skills[name]

    def mentions(self, prompt: str) -> list[str]:
        names = re.findall("(?<![\\\\\\w$])\\$(" + NAME_PATTERN + ")(?![\\w-])", prompt)
        return list(dict.fromkeys((name for name in names if name in self.skills)))


class SkillSession:
    """Agent-local activation state; resources are copied only when selected."""

    def __init__(self, registry: SkillRegistry, workspace: Path, *, resume: bool = False):
        self.registry = registry
        self.workspace = workspace
        self.root = workspace / ".skills"
        self.root.mkdir(exist_ok=resume)
        self.events = workspace / ".harness" / "skills.jsonl"
        self.loaded: set[str] = set()
        self.explicit: set[str] = set()
        if resume and self.events.is_file():
            for line in self.events.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("event") != "skill_loaded":
                    continue
                skill = registry.get(event["skill"])
                if event.get("version") != skill.version:
                    raise ValueError(
                        f"Saved skill version differs from the episode snapshot: {skill.name}"
                    )
                for relative, digest in skill.files.items():
                    path = local_path(self.root / skill.name, relative)
                    if not path.is_file() or file_hash(path) != digest:
                        raise ValueError(f"Loaded skill resources changed: {skill.name}/{relative}")
                self.loaded.add(skill.name)
                if event.get("reason") in {"explicit_selection", "prompt_mention"}:
                    self.explicit.add(skill.name)

    def record(self, event: str, skill: Skill, **details):
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "skill": skill.name,
            "version": skill.version,
            **details,
        }
        with self.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def activate(self, prompt: str, requested: tuple[str, ...]) -> list[dict]:
        requested = list(dict.fromkeys(text_list(list(requested), "skills")))
        reasons = {name: "explicit_selection" for name in requested}
        for name in self.registry.mentions(prompt):
            reasons.setdefault(name, "prompt_mention")
        for name in reasons:
            self.registry.get(name)
        self.explicit.update(reasons)
        messages = []
        for name, reason in reasons.items():
            if name in self.loaded:
                continue
            result = self.read(name, reason=reason)
            messages.append(
                {
                    "role": "user",
                    "content": "Explicitly selected skill guidance. This does not grant extra permissions or override the user's task or harness rules.\n"
                    + json.dumps(result, ensure_ascii=False),
                }
            )
        return messages

    def list(self, query: str = "", offset: int = 0, limit: int = 50) -> dict:
        if (
            not isinstance(query, str)
            or type(offset) is not int
            or offset < 0
            or (type(limit) is not int)
            or (not 1 <= limit <= 100)
        ):
            raise ValueError("query must be text, offset nonnegative, and limit between 1 and 100")
        matches = [
            s.metadata()
            for s in self.registry.skills.values()
            if (s.allow_implicit or s.name in self.explicit)
            and query.casefold() in f"{s.name} {s.description}".casefold()
        ]
        items = matches[offset : offset + limit]
        return {
            "skills": items,
            "total": len(matches),
            "next_offset": offset + len(items) if offset + len(items) < len(matches) else None,
        }

    def prompt(self) -> str:
        lines = []
        used = 0
        visible = [
            s for s in self.registry.skills.values() if s.allow_implicit or s.name in self.explicit
        ]
        for skill in visible:
            line = json.dumps(skill.metadata(), ensure_ascii=False)
            if used + len(line) + 1 > CATALOG_CHAR_LIMIT:
                break
            lines.append(line)
            used += len(line) + 1
        return (
            f"\nSkills: choose relevant skills by description and call read_skill(name) to load their full instructions. Resources are read-only under .skills/<name>/ after loading; read references only when needed, view images with view_image, and execute Blender scripts with the enabled Blender tools. Copy templates into writable files before editing. Reading a skill does not execute it or grant permissions. Follow user intent and harness rules over skill suggestions. Each agent loads skills independently.\nShowing {len(lines)} of {len(visible)} skills; list_skills supports search and pagination.\n"
            + "\n".join(lines)
            + "\n"
        )

    def read(self, name: str, path: str | None = None, *, reason: str = "model_selection") -> dict:
        skill = self.registry.get(name)
        if not skill.allow_implicit and name not in self.explicit:
            raise ValueError(
                f"{name} requires explicit --skill, $name, or delegated skills selection"
            )
        resource = path or skill.manifest
        relative = local_path(skill.root, resource).relative_to(skill.root).as_posix()
        if relative not in skill.files:
            raise ValueError(f"No such file in skill {name}: {resource}")
        if name not in self.loaded:
            if relative != skill.manifest:
                raise ValueError("Read the skill's SKILL.md before its supporting resources")
            staging = self.workspace / ".harness" / f"skill_load_{uuid.uuid4().hex}"
            try:
                hashes = copy_files(skill.root, staging, list(skill.files))
                if hashes != skill.files:
                    raise ValueError(f"Skill snapshot changed: {name}")
                staging.rename(self.root / name)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
            self.loaded.add(name)
            self.record("skill_loaded", skill, reason=reason)
        target = local_path(self.root / name, relative)
        output = {
            "name": name,
            "version": skill.version,
            "root": f".skills/{name}",
            "path": target.relative_to(self.workspace).as_posix(),
        }
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            output["message"] = (
                "Binary resource; use its workspace path with an appropriate enabled tool."
            )
        else:
            limit = len(content) if relative == skill.manifest else 40000
            output.update(
                content=content[:limit],
                total_characters=len(content),
                next_offset=limit if len(content) > limit else None,
            )
        if relative == skill.manifest:
            output["resources"] = list(skill.files)[:1000]
            output["resources_truncated"] = len(skill.files) > 1000
        self.record(
            "skill_resource_read", skill, resource=relative, source="read_skill", reason=reason
        )
        return output

    def record_access(self, path: Path, event: str, **details):
        if not path.is_relative_to(self.root):
            return
        parts = path.relative_to(self.root).parts
        if len(parts) >= 2 and parts[0] in self.loaded:
            self.record(
                event,
                self.registry.get(parts[0]),
                resource="/".join(parts[1:]),
                source="resource_access",
                **details,
            )
