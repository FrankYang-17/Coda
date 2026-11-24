"""Tool payloads retained by background jobs until their results are collected."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ViewedImage:
    path: Path
    label: str


@dataclass
class ToolResult:
    data: dict
    images: list[ViewedImage] = field(default_factory=list)
    finished: bool = False
