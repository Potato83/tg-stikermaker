"""Media probing and streaming all-frame object detection."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from sticker_maker.ffmpeg import FFmpeg
from sticker_maker.models import AnalysisResult, BoundingBox, MediaInfo


def _float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if np.isfinite(result) else default


def _rate(value: Any) -> float:
    if not value or value == "0/0":
        return 0.0
    try:
        return float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return 0.0


class MediaDetector:
    """Extract normalized stream metadata with ffprobe."""

    def __init__(self, ffmpeg: FFmpeg) -> None:
        self.ffmpeg = ffmpeg

    def probe(self, path: Path, *, count_frames: bool = False) -> MediaInfo:
        document = self.ffmpeg.probe_json(path, count_frames=count_frames)
        streams = document.get("streams", [])
        videos = [stream for stream in streams if stream.get("codec_type") == "video"]
        if not videos:
            raise ValueError("No video stream found")
        stream = videos[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        if width <= 0 or height <= 0:
            raise ValueError("Video stream has invalid dimensions")

        container = document.get("format", {})
        duration = _float(stream.get("duration")) or _float(container.get("duration"))
        fps = _rate(stream.get("avg_frame_rate")) or _rate(stream.get("r_frame_rate"))
        frames_raw = stream.get("nb_read_frames") or stream.get("nb_frames")
        try:
            frame_count = int(frames_raw) if frames_raw not in (None, "N/A") else None
        except (TypeError, ValueError):
            frame_count = None
        if duration <= 0 and frame_count and fps > 0:
            duration = frame_count / fps
        if duration <= 0:
            raise ValueError("Could not determine animation duration")

        pixel_format = str(stream.get("pix_fmt") or "")
        tags = {
            str(key).lower(): value for key, value in (stream.get("tags") or {}).items()
        }
        alpha_metadata = "a" in pixel_format or str(tags.get("alpha_mode", "0")) == "1"
        size_bytes = path.stat().st_size
        return MediaInfo(
            path=path,
            width=width,
            height=height,
            duration=duration,
            fps=fps,
            frame_count=frame_count,
            codec_name=str(stream.get("codec_name") or ""),
            pixel_format=pixel_format,
            has_audio=any(item.get("codec_type") == "audio" for item in streams),
            alpha_metadata=alpha_metadata,
            format_name=str(container.get("format_name") or ""),
            size_bytes=size_bytes,
            video_stream_index=int(stream.get("index") or 0),
            raw_stream=stream,
        )


class ObjectDetector:
    """Find a conservative union of foreground pixels over every frame."""

    def __init__(
        self,
        ffmpeg: FFmpeg,
        *,
        alpha_threshold: int = 8,
        background_threshold: float = 24.0,
    ) -> None:
        self.ffmpeg = ffmpeg
        self.alpha_threshold = alpha_threshold
        self.background_threshold = background_threshold

    def analyze(self, media: MediaInfo) -> AnalysisResult:
        alpha_union: BoundingBox | None = None
        content_union: BoundingBox | None = None
        frames = 0
        transparent_frames = 0
        foreground_total = 0
        pixel_total = 0

        for raw in self.ffmpeg.rgba_frames(media.path, media.width, media.height):
            rgba = np.frombuffer(raw, dtype=np.uint8).reshape(
                media.height, media.width, 4
            )
            alpha = rgba[:, :, 3]
            has_transparent_pixel = bool(np.any(alpha < 250))
            transparent_frames += int(has_transparent_pixel)

            alpha_mask = alpha > self.alpha_threshold
            alpha_box = _mask_box(alpha_mask)
            if alpha_box is not None:
                alpha_union = (
                    alpha_box if alpha_union is None else alpha_union.union(alpha_box)
                )

            content_mask = self.content_mask(rgba[:, :, :3])
            content_box = _mask_box(content_mask)
            if content_box is not None:
                content_union = (
                    content_box
                    if content_union is None
                    else content_union.union(content_box)
                )
                foreground_total += int(np.count_nonzero(content_mask))
            pixel_total += media.width * media.height
            frames += 1

        if frames == 0:
            raise ValueError("The input contains no decodable frames")

        full = BoundingBox(0, 0, media.width, media.height)
        has_transparency = transparent_frames > 0
        if has_transparency and alpha_union is not None:
            selected = alpha_union
            method = "alpha-union"
        elif content_union is not None:
            selected = content_union
            method = "content-union"
        else:
            selected = full
            method = "full-frame-fallback"

        fraction = foreground_total / pixel_total if pixel_total else 1.0
        if method == "content-union" and (
            fraction < 0.0001 or selected.area > full.area * 0.985
        ):
            selected = full
            method = "full-frame-fallback"

        return AnalysisResult(
            bounding_box=selected,
            frame_count=frames,
            has_transparency=has_transparency,
            method=method,
            foreground_fraction=fraction,
        )

    def content_mask(self, rgb: NDArray[np.uint8]) -> NDArray[np.bool_]:
        """Estimate foreground against a border-derived background model."""

        height, width, _ = rgb.shape
        edge = max(1, min(height, width) // 40)
        border = np.concatenate(
            (
                rgb[:edge, :, :].reshape(-1, 3),
                rgb[-edge:, :, :].reshape(-1, 3),
                rgb[:, :edge, :].reshape(-1, 3),
                rgb[:, -edge:, :].reshape(-1, 3),
            ),
            axis=0,
        ).astype(np.float32)
        background = np.median(border, axis=0)
        border_distance = np.sqrt(np.sum((border - background) ** 2, axis=1))
        adaptive = float(np.percentile(border_distance, 95)) + 8.0
        threshold = max(self.background_threshold, adaptive)

        pixels = rgb.astype(np.float32)
        distance = np.sqrt(np.sum((pixels - background) ** 2, axis=2))
        mask = distance > threshold

        # A small neighbor vote removes isolated compression speckles while
        # preserving thin moving parts such as fingers, antennae and outlines.
        padded = np.pad(mask, 1, mode="constant")
        votes = np.zeros_like(mask, dtype=np.uint8)
        for y_offset in range(3):
            for x_offset in range(3):
                votes += padded[
                    y_offset : y_offset + height, x_offset : x_offset + width
                ]
        cleaned = votes >= 3
        occupancy = float(np.count_nonzero(cleaned)) / cleaned.size
        if occupancy > 0.90:
            return np.ones_like(cleaned)
        return cleaned


def _mask_box(mask: NDArray[np.bool_]) -> BoundingBox | None:
    """Convert a mask to a noise-resistant, conservative rectangle."""

    if not np.any(mask):
        return None
    height, width = mask.shape
    row_counts = np.count_nonzero(mask, axis=1)
    column_counts = np.count_nonzero(mask, axis=0)
    row_floor = max(1, int(width * 0.0005))
    column_floor = max(1, int(height * 0.0005))
    ys = np.flatnonzero(row_counts >= row_floor)
    xs = np.flatnonzero(column_counts >= column_floor)
    if xs.size == 0 or ys.size == 0:
        ys, xs = np.nonzero(mask)
    x1, x2 = int(xs[0]), int(xs[-1]) + 1
    y1, y2 = int(ys[0]), int(ys[-1]) + 1
    return BoundingBox(x1, y1, x2 - x1, y2 - y1)
