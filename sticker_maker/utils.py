"""Filesystem, hashing and formatting helpers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any


SUPPORTED_EXTENSIONS = {
    ".gif",
    ".webp",
    ".apng",
    ".png",
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
}


def discover_inputs(path: Path) -> list[Path]:
    """Return deterministic supported files from a file or directory."""

    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_EXTENSIONS else []
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")
    return sorted(
        (
            item
            for item in path.iterdir()
            if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS
        ),
        key=lambda item: item.name.casefold(),
    )


def discover_webm(path: Path) -> list[Path]:
    """Return one or all WebM files for verify/preview commands."""

    if path.is_file():
        return [path]
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")
    return sorted(path.glob("*.webm"), key=lambda item: item.name.casefold())


def output_for(source: Path, output_directory: Path) -> Path:
    """Map every supported source name to a WebM output."""

    return output_directory / f"{source.stem}.webm"


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file without loading it into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def cache_key(source: Path, settings: dict[str, Any], version: str) -> str:
    """Fingerprint source bytes, software version and material settings."""

    digest = hashlib.sha256()
    digest.update(file_sha256(source).encode("ascii"))
    digest.update(version.encode("utf-8"))
    digest.update(
        json.dumps(settings, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return digest.hexdigest()


def copy_atomic(source: Path, destination: Path) -> None:
    """Replace a destination via an adjacent temporary file."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def human_bytes(value: int) -> str:
    """Format byte counts compactly."""

    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(size) < 1024.0 or unit == "GiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} GiB"


def resolve_workers(value: str | int, job_count: int) -> int:
    """Choose conservative process parallelism for expensive encoders."""

    if job_count <= 0:
        return 1
    cpu_count = os.cpu_count() or 1
    if value == "auto":
        return max(1, min(job_count, max(1, cpu_count // 2)))
    workers = int(value)
    if workers < 1:
        raise ValueError("workers must be 'auto' or a positive integer")
    return min(workers, job_count)


def resolve_threads(value: int, workers: int) -> int:
    """Allocate FFmpeg threads without gross CPU oversubscription."""

    if value > 0:
        return value
    return max(1, (os.cpu_count() or 1) // max(1, workers))
