"""End-to-end conversion, caching and Telegram verification."""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from sticker_maker.config import AppConfig
from sticker_maker.cropper import build_crop_plan
from sticker_maker.detector import MediaDetector, ObjectDetector
from sticker_maker.ffmpeg import FFmpeg
from sticker_maker.models import (
    ConversionResult,
    ResultStatus,
    VerificationIssue,
    VerificationReport,
)
from sticker_maker.optimizer import BitrateOptimizer
from sticker_maker.utils import cache_key, copy_atomic


PIPELINE_VERSION = "1.0.0"


class Verifier:
    """Validate dimensions, timing, codec, audio, alpha and byte size."""

    def __init__(self, ffmpeg: FFmpeg, config: AppConfig) -> None:
        self.ffmpeg = ffmpeg
        self.config = config
        self.detector = MediaDetector(ffmpeg)

    def verify(self, path: Path) -> VerificationReport:
        issues: list[VerificationIssue] = []
        try:
            media = self.detector.probe(path, count_frames=True)
        except Exception as exc:
            return VerificationReport(
                path,
                None,
                (VerificationIssue("unreadable", f"Unreadable WebM: {exc}"),),
            )

        platform = self.config.platform
        if media.codec_name != "vp9":
            issues.append(
                VerificationIssue(
                    "codec", f"Codec is {media.codec_name or 'unknown'}, not VP9"
                )
            )
        if (media.width, media.height) != (platform.width, platform.height):
            issues.append(
                VerificationIssue(
                    "dimensions",
                    f"Dimensions are {media.width}×{media.height}, expected {platform.width}×{platform.height}",
                )
            )
        if abs(media.fps - platform.fps) > 0.02:
            issues.append(
                VerificationIssue(
                    "fps", f"FPS is {media.fps:.4g}, expected {platform.fps:g}"
                )
            )
        if media.duration > platform.max_duration + 0.001:
            issues.append(
                VerificationIssue(
                    "duration",
                    f"Duration is {media.duration:.3f}s, maximum is {platform.max_duration:.3f}s",
                )
            )
        if media.size_bytes > platform.max_size_bytes:
            issues.append(
                VerificationIssue(
                    "size",
                    f"Size is {media.size_bytes} bytes, maximum is {platform.max_size_bytes} bytes",
                )
            )
        if media.has_audio:
            issues.append(VerificationIssue("audio", "An audio stream is present"))
        if not media.alpha_metadata:
            issues.append(
                VerificationIssue("alpha-metadata", "No WebM alpha metadata is present")
            )
        else:
            try:
                rgba = self.ffmpeg.decode_first_rgba(path, media.width, media.height)
                alpha = np.frombuffer(rgba, dtype=np.uint8).reshape(
                    media.height, media.width, 4
                )[:, :, 3]
                if not np.any(alpha < 255):
                    issues.append(
                        VerificationIssue(
                            "alpha", "The decoded frame has no transparent pixels"
                        )
                    )
            except Exception as exc:
                issues.append(
                    VerificationIssue(
                        "alpha-decode", f"Could not validate transparency: {exc}"
                    )
                )
        return VerificationReport(path, media, tuple(issues))


class StickerEncoder:
    """Convert one source file into an optimized Telegram WebM sticker."""

    def __init__(
        self, config: AppConfig, cache_directory: Path, ffmpeg: FFmpeg | None = None
    ) -> None:
        self.config = config
        self.cache_directory = cache_directory
        self.ffmpeg = ffmpeg or FFmpeg()
        self.media_detector = MediaDetector(self.ffmpeg)
        self.object_detector = ObjectDetector(
            self.ffmpeg,
            alpha_threshold=config.crop.alpha_threshold,
            background_threshold=config.crop.background_threshold,
        )
        self.verifier = Verifier(self.ffmpeg, config)

    def convert(
        self,
        source: Path,
        output: Path,
        *,
        dry_run: bool = False,
    ) -> ConversionResult:
        started = time.monotonic()
        source = source.resolve()
        output = output.resolve()
        if source == output:
            raise ValueError("Input and output paths resolve to the same file")
        if output.exists() and not self.config.runtime.overwrite:
            return ConversionResult(
                source,
                output,
                ResultStatus.SKIPPED,
                time.monotonic() - started,
                source_size=source.stat().st_size,
                output_size=output.stat().st_size,
                message="output exists; use --overwrite",
            )

        settings = self.config.as_dict()
        fingerprint = cache_key(source, settings, PIPELINE_VERSION)
        cached_path = self.cache_directory / f"{fingerprint}.webm"
        if self.config.runtime.cache_enabled and cached_path.is_file():
            report = self.verifier.verify(cached_path)
            if report.ok:
                copy_atomic(cached_path, output)
                media = report.media
                return ConversionResult(
                    source,
                    output,
                    ResultStatus.CACHED,
                    time.monotonic() - started,
                    source_size=source.stat().st_size,
                    output_size=output.stat().st_size,
                    fps=media.fps if media else 0.0,
                    duration=media.duration if media else 0.0,
                    message="restored from cache",
                )
            cached_path.unlink(missing_ok=True)

        media = self.media_detector.probe(source, count_frames=False)
        analysis = self.object_detector.analyze(media)
        if analysis.frame_count < 2:
            raise ValueError("Input is not animated (fewer than two decoded frames)")
        plan = build_crop_plan(
            media,
            analysis,
            self.config.crop,
            self.config.encoding,
            self.config.platform,
        )
        if dry_run:
            return ConversionResult(
                source,
                output,
                ResultStatus.DRY_RUN,
                time.monotonic() - started,
                source_size=media.size_bytes,
                fps=self.config.platform.fps,
                duration=plan.output_duration,
                speed_factor=plan.speed_factor,
                bounding_box=plan.bounding_box,
                message=f"{analysis.method}; {analysis.frame_count} frames analyzed",
            )

        self.cache_directory.mkdir(parents=True, exist_ok=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="sticker-", dir=self.cache_directory
        ) as temporary_name:
            temporary = Path(temporary_name)
            candidate = temporary / "candidate.webm"
            best = temporary / "best.webm"
            passlog = temporary / "vp9-pass"
            profile = self.config.quality_profile
            iterations = min(
                self.config.encoding.search_iterations, profile.search_iterations
            )
            optimizer = BitrateOptimizer(
                max_size_bytes=self.config.platform.max_size_bytes,
                min_bitrate_kbps=self.config.encoding.min_bitrate_kbps,
                max_bitrate_kbps=self.config.encoding.max_bitrate_kbps,
                iterations=iterations,
                tolerance_bytes=self.config.encoding.size_tolerance_bytes,
                crf_candidates=profile.crf_candidates,
            )

            def encode_attempt(bitrate: int, crf: int, path: Path) -> None:
                self.ffmpeg.encode_two_pass(
                    source=source,
                    output=path,
                    filter_graph=plan.filter_graph,
                    bitrate_kbps=bitrate,
                    crf=crf,
                    threads=self.config.runtime.threads,
                    deadline=profile.deadline,
                    cpu_used=profile.cpu_used,
                    passlog=passlog,
                    codec=self.config.platform.codec,
                    pixel_format=self.config.platform.pixel_format,
                )

            optimized = optimizer.optimize(
                encode=encode_attempt,
                candidate_path=candidate,
                final_path=best,
                duration=plan.output_duration,
            )
            copy_atomic(best, output)
            report = self.verifier.verify(output)
            if not report.ok:
                output.unlink(missing_ok=True)
                details = "; ".join(issue.message for issue in report.issues)
                raise ValueError(f"Encoder produced a non-compliant file: {details}")

            if self.config.runtime.cache_enabled:
                copy_atomic(best, cached_path)
                metadata = {
                    "source": str(source),
                    "created_at": time.time(),
                    "settings": settings,
                    "analysis": {
                        **asdict(analysis),
                        "bounding_box": analysis.bounding_box.as_dict(),
                    },
                    "bitrate_kbps": optimized.best.bitrate_kbps,
                    "crf": optimized.best.crf,
                    "size_bytes": optimized.best.size_bytes,
                }
                cached_path.with_suffix(".json").write_text(
                    json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
                )

        result_media = report.media
        return ConversionResult(
            source,
            output,
            ResultStatus.CREATED,
            time.monotonic() - started,
            source_size=media.size_bytes,
            output_size=output.stat().st_size,
            fps=result_media.fps if result_media else self.config.platform.fps,
            duration=result_media.duration if result_media else plan.output_duration,
            speed_factor=plan.speed_factor,
            bitrate_kbps=optimized.best.bitrate_kbps,
            crf=optimized.best.crf,
            bounding_box=plan.bounding_box,
            attempts=len(optimized.attempts),
            message=f"{analysis.method}; {analysis.frame_count} frames analyzed",
        )


def convert_safely(
    source: Path,
    output: Path,
    config: AppConfig,
    cache_directory: Path,
    dry_run: bool,
) -> ConversionResult:
    """Pickle-friendly process worker that turns exceptions into results."""

    started = time.monotonic()
    try:
        return StickerEncoder(config, cache_directory).convert(
            source, output, dry_run=dry_run
        )
    except Exception as exc:
        return ConversionResult(
            source=source,
            output=output,
            status=ResultStatus.FAILED,
            elapsed_seconds=time.monotonic() - started,
            source_size=source.stat().st_size if source.exists() else 0,
            message=str(exc),
        )
