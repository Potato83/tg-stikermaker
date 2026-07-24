"""Typed domain models shared by the conversion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ResultStatus(str, Enum):
    """Terminal state of one input file."""

    CREATED = "created"
    CACHED = "cached"
    SKIPPED = "skipped"
    FAILED = "failed"
    DRY_RUN = "dry-run"


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Half-open pixel rectangle."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    def union(self, other: "BoundingBox") -> "BoundingBox":
        x1 = min(self.x, other.x)
        y1 = min(self.y, other.y)
        x2 = max(self.right, other.right)
        y2 = max(self.bottom, other.bottom)
        return BoundingBox(x1, y1, x2 - x1, y2 - y1)

    def expand(self, pixels: int, frame_width: int, frame_height: int) -> "BoundingBox":
        x1 = max(0, self.x - pixels)
        y1 = max(0, self.y - pixels)
        x2 = min(frame_width, self.right + pixels)
        y2 = min(frame_height, self.bottom + pixels)
        return BoundingBox(x1, y1, max(1, x2 - x1), max(1, y2 - y1))

    def as_dict(self) -> dict[str, int]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """Relevant ffprobe data for a media file."""

    path: Path
    width: int
    height: int
    duration: float
    fps: float
    frame_count: int | None
    codec_name: str
    pixel_format: str
    has_audio: bool
    alpha_metadata: bool
    format_name: str
    size_bytes: int
    video_stream_index: int = 0
    raw_stream: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Object detection result accumulated across every decoded frame."""

    bounding_box: BoundingBox
    frame_count: int
    has_transparency: bool
    method: str
    foreground_fraction: float


@dataclass(frozen=True, slots=True)
class CropPlan:
    """Crop and timing transformation passed to FFmpeg."""

    bounding_box: BoundingBox
    target_inner_size: int
    speed_factor: float
    output_duration: float
    filter_graph: str


@dataclass(frozen=True, slots=True)
class EncodeAttempt:
    """Measurements from one two-pass VP9 attempt."""

    bitrate_kbps: int
    crf: int
    size_bytes: int
    output_path: Path
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """Best valid attempt selected by the size optimizer."""

    best: EncodeAttempt
    attempts: tuple[EncodeAttempt, ...]


@dataclass(frozen=True, slots=True)
class VerificationIssue:
    """One Telegram compatibility violation."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """Verification result for a single output file."""

    path: Path
    media: MediaInfo | None
    issues: tuple[VerificationIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Serializable worker result shown by the parent process."""

    source: Path
    output: Path
    status: ResultStatus
    elapsed_seconds: float
    source_size: int = 0
    output_size: int = 0
    fps: float = 0.0
    duration: float = 0.0
    speed_factor: float = 1.0
    bitrate_kbps: int | None = None
    crf: int | None = None
    bounding_box: BoundingBox | None = None
    attempts: int = 0
    message: str = ""
