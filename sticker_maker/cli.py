"""Command-line interface for conversion, verification and previews."""

from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from tqdm import tqdm

from sticker_maker import __version__
from sticker_maker.config import AppConfig, load_config, with_overrides
from sticker_maker.encoder import Verifier, convert_safely
from sticker_maker.ffmpeg import DependencyError, FFmpeg
from sticker_maker.logger import configure_logging
from sticker_maker.models import ConversionResult, ResultStatus
from sticker_maker.utils import (
    discover_inputs,
    discover_webm,
    human_bytes,
    output_for,
    resolve_threads,
    resolve_workers,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sticker-maker",
        description="Create size-optimized 512×512 VP9 Telegram stickers.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    convert = commands.add_parser("convert", help="convert a file or directory")
    convert.add_argument("input", nargs="?", type=Path, default=Path("gifs"))
    convert.add_argument("output", nargs="?", type=Path, default=Path("output"))
    convert.add_argument("--config", type=Path, help="TOML configuration file")
    convert.add_argument(
        "--threads", type=int, help="FFmpeg threads per worker (0 = auto)"
    )
    convert.add_argument("--workers", help="parallel files: auto or a positive integer")
    convert.add_argument(
        "--zoom", choices=("auto", "fit"), help="smart crop or full-frame fit"
    )
    convert.add_argument("--padding", type=float, help="object padding ratio")
    convert.add_argument("--quality", choices=("fast", "balanced", "best"))
    convert.add_argument(
        "--max-size", type=int, metavar="KIB", help="maximum output size"
    )
    convert.add_argument("--overwrite", action="store_true", default=None)
    convert.add_argument(
        "--no-cache", action="store_false", dest="cache_enabled", default=None
    )
    convert.add_argument("--verbose", action="store_true", default=None)
    convert.add_argument(
        "--dry-run", action="store_true", help="analyze only; do not encode"
    )
    convert.add_argument("--cache-dir", type=Path, default=Path("cache"))

    verify = commands.add_parser("verify", help="validate existing WebM stickers")
    verify.add_argument("path", nargs="?", type=Path, default=Path("output"))
    verify.add_argument("--config", type=Path)
    verify.add_argument("--verbose", action="store_true")

    preview = commands.add_parser(
        "preview", help="create GIF previews or PNG contact sheets"
    )
    preview.add_argument("path", type=Path)
    preview.add_argument("output", nargs="?", type=Path)
    preview.add_argument("--format", choices=("sheet", "gif"), default="sheet")
    preview.add_argument(
        "--frames", type=int, default=12, help="contact-sheet frame count"
    )
    preview.add_argument("--fps", type=int, default=15, help="GIF preview FPS")
    preview.add_argument("--overwrite", action="store_true")
    preview.add_argument("--verbose", action="store_true")
    return parser


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    values = list(argv)
    commands = {"convert", "verify", "preview"}
    if not values:
        return ["convert"]
    if values[0] in commands or values[0] in {"-h", "--help", "--version"}:
        return values
    return ["convert", *values]


def _conversion_config(args: argparse.Namespace) -> AppConfig:
    config = load_config(args.config)
    worker_value: str | int | None = args.workers
    if worker_value is not None and worker_value != "auto":
        try:
            worker_value = int(worker_value)
        except ValueError as exc:
            raise ValueError("--workers must be 'auto' or a positive integer") from exc
    return with_overrides(
        config,
        quality=args.quality,
        zoom=args.zoom,
        padding_ratio=args.padding,
        threads=args.threads,
        workers=worker_value,
        overwrite=args.overwrite,
        cache_enabled=args.cache_enabled,
        verbose=args.verbose,
        max_size_kib=args.max_size,
    )


def _show_result(logger: logging.Logger, result: ConversionResult) -> None:
    name = result.source.name
    if result.status is ResultStatus.FAILED:
        logger.error("✖ %s — %s", name, result.message)
        return
    if result.status is ResultStatus.SKIPPED:
        logger.warning("↷ %s — %s", name, result.message)
        return
    if result.status is ResultStatus.DRY_RUN:
        box = result.bounding_box
        bbox = f"{box.width}×{box.height}+{box.x}+{box.y}" if box else "n/a"
        logger.info(
            "✓ %s — dry run | bbox %s | speed %.3f× | %.3fs | %s",
            name,
            bbox,
            result.speed_factor,
            result.duration,
            result.message,
        )
        return
    cached = "cache | " if result.status is ResultStatus.CACHED else ""
    box = result.bounding_box
    bbox = f" | bbox {box.width}×{box.height}+{box.x}+{box.y}" if box else ""
    rate = (
        f" | {result.bitrate_kbps} kbps | CRF {result.crf}"
        if result.bitrate_kbps
        else ""
    )
    logger.info(
        "✔ %s — %s%s → %s | %.2fs | %.3fs | %.3f×%s%s | %d attempt(s)",
        name,
        cached,
        human_bytes(result.source_size),
        human_bytes(result.output_size),
        result.elapsed_seconds,
        result.duration,
        result.speed_factor,
        rate,
        bbox,
        result.attempts,
    )


def _run_convert(args: argparse.Namespace) -> int:
    try:
        config = _conversion_config(args)
        sources = discover_inputs(args.input)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    logger = configure_logging(config.runtime.verbose)
    if not sources:
        logger.error("No supported media files found in %s", args.input)
        return 1
    try:
        FFmpeg().ensure_available()
    except DependencyError as exc:
        logger.error("%s", exc)
        return 2

    workers = resolve_workers(config.runtime.workers, len(sources))
    threads = resolve_threads(config.runtime.threads, workers)
    config = replace(config, runtime=replace(config.runtime, threads=threads))
    logger.info(
        "Processing %d file(s) with %d worker(s), %d FFmpeg thread(s) each, profile=%s",
        len(sources),
        workers,
        threads,
        config.encoding.quality,
    )
    args.output.mkdir(parents=True, exist_ok=True) if not args.dry_run else None
    jobs = [(source, output_for(source, args.output)) for source in sources]
    results: list[ConversionResult] = []

    if workers == 1:
        iterator = tqdm(
            jobs, total=len(jobs), unit="file", desc="processing", dynamic_ncols=True
        )
        for source, output in iterator:
            result = convert_safely(
                source, output, config, args.cache_dir, args.dry_run
            )
            results.append(result)
            _show_result(logger, result)
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    convert_safely, source, output, config, args.cache_dir, args.dry_run
                ): source
                for source, output in jobs
            }
            with tqdm(
                total=len(futures), unit="file", desc="processing", dynamic_ncols=True
            ) as progress:
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
                    _show_result(logger, result)
                    progress.update(1)

    failed = sum(result.status is ResultStatus.FAILED for result in results)
    created = sum(
        result.status in {ResultStatus.CREATED, ResultStatus.CACHED}
        for result in results
    )
    skipped = sum(result.status is ResultStatus.SKIPPED for result in results)
    logger.info("Done: %d created, %d skipped, %d failed", created, skipped, failed)
    return 1 if failed else 0


def _run_verify(args: argparse.Namespace) -> int:
    logger = configure_logging(args.verbose)
    try:
        config = load_config(args.config)
        files = discover_webm(args.path)
        ffmpeg = FFmpeg()
        ffmpeg.ensure_available()
    except (OSError, ValueError, DependencyError) as exc:
        logger.error("%s", exc)
        return 2
    if not files:
        logger.error("No WebM files found in %s", args.path)
        return 1
    verifier = Verifier(ffmpeg, config)
    failures = 0
    for path in files:
        report = verifier.verify(path)
        if report.ok:
            media = report.media
            logger.info(
                "✔ OK  %s — %s | %.3fs | %.3g FPS | VP9 + alpha | no audio",
                path,
                human_bytes(media.size_bytes if media else 0),
                media.duration if media else 0.0,
                media.fps if media else 0.0,
            )
        else:
            failures += 1
            logger.error("✖ %s", path)
            for issue in report.issues:
                logger.error("  ✖ %s", issue.message)
    return 1 if failures else 0


def _preview_destination(
    source: Path, requested: Path | None, kind: str, multiple: bool
) -> Path:
    suffix = ".gif" if kind == "gif" else ".png"
    if requested is None:
        return source.with_name(f"{source.stem}.preview{suffix}")
    if multiple or requested.suffix.lower() != suffix:
        return requested / f"{source.stem}.preview{suffix}"
    return requested


def _run_preview(args: argparse.Namespace) -> int:
    logger = configure_logging(args.verbose)
    try:
        files = discover_webm(args.path)
        ffmpeg = FFmpeg()
        ffmpeg.ensure_available()
    except (OSError, ValueError, DependencyError) as exc:
        logger.error("%s", exc)
        return 2
    if not files:
        logger.error("No WebM files found in %s", args.path)
        return 1
    if args.frames < 1 or args.fps < 1:
        logger.error("--frames and --fps must be positive")
        return 2
    failed = 0
    for source in files:
        destination = _preview_destination(
            source, args.output, args.format, len(files) > 1
        )
        if destination.exists() and not args.overwrite:
            logger.warning("↷ %s exists; use --overwrite", destination)
            continue
        try:
            if args.format == "gif":
                ffmpeg.create_gif_preview(source, destination, args.fps)
            else:
                ffmpeg.create_contact_sheet(source, destination, args.frames)
            logger.info("✔ %s", destination)
        except Exception as exc:
            failed += 1
            logger.error("✖ %s — %s", source, exc)
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point supporting explicit and implicit convert commands."""

    arguments = _normalize_argv(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(arguments)
    if args.command == "convert":
        return _run_convert(args)
    if args.command == "verify":
        return _run_verify(args)
    if args.command == "preview":
        return _run_preview(args)
    raise AssertionError(f"Unhandled command: {args.command}")
