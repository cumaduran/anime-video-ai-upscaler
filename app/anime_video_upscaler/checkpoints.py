"""Inspect and remove only application-owned checkpoint files in a chosen folder."""
from dataclasses import dataclass
import json
from pathlib import Path
import re

_DIR = re.compile(r"\.anime-resume-[0-9a-f]{32}-[0-9a-f]{16}")
_FILE = re.compile(r"(?:manifest\.json|segments\.ffconcat|part-[0-9]{6}(?:\.partial)?\.mkv)")


@dataclass(frozen=True)
class Checkpoint:
    path: Path
    source: str
    size: int
    segments: int


def inspect_checkpoint(directory: Path) -> Checkpoint:
    directory = Path(directory)
    if not _DIR.fullmatch(directory.name) or directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()) or not directory.is_dir():
        raise ValueError("Invalid checkpoint folder")
    entries = list(directory.iterdir())
    for entry in entries:
        if not _FILE.fullmatch(entry.name) or entry.is_symlink() or not entry.is_file():
            raise ValueError("Checkpoint contains unexpected files")
    manifest = directory / "manifest.json"
    if manifest not in entries or manifest.stat().st_size > 1_000_000:
        raise ValueError("Missing or invalid checkpoint manifest")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") not in (2, 3) or not isinstance(data.get("source"), str):
        raise ValueError("Invalid checkpoint manifest")
    return Checkpoint(directory, data["source"], sum(p.stat().st_size for p in entries),
                      sum(bool(re.fullmatch(r"part-[0-9]{6}\.mkv", p.name)) for p in entries))


def find_checkpoints(folder: Path) -> list[Checkpoint]:
    result = []
    for candidate in Path(folder).glob(".anime-resume-*"):
        try:
            result.append(inspect_checkpoint(candidate))
        except (OSError, ValueError, UnicodeError):
            continue
    return sorted(result, key=lambda item: item.path.name)


def remove_checkpoint(checkpoint: Checkpoint) -> None:
    # Revalidate immediately before deleting; never recurse or follow links.
    current = inspect_checkpoint(checkpoint.path)
    if current != checkpoint:
        raise ValueError("Checkpoint changed; refresh the list before removing it")
    for entry in current.path.iterdir():
        if entry.is_symlink() or not _FILE.fullmatch(entry.name) or not entry.is_file():
            raise ValueError("Checkpoint changed; refresh the list before removing it")
        entry.unlink()
    current.path.rmdir()
