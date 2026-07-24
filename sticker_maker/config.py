"""TOML configuration and quality profile resolution."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class PlatformSpec:
    """Output constraints for a sticker platform."""

    name: str = "telegram"
    width: int = 512
    height: int = 512
    fps: float = 30.0
    max_duration: float = 3.0
    max_size_kib: int = 256
    codec: str = "libvpx-vp9"
    pixel_format: str = "yuva420p"

    @property
    def max_size_bytes(self) -> int:
        return self.max_size_kib * 1024


@dataclass(frozen=True, slots=True)
class CropConfig:
    """Smart-crop behavior."""

    zoom: str = "auto"
    padding_ratio: float = 0.015
    fill_ratio: float = 0.97
    alpha_threshold: int = 8
    background_threshold: float = 24.0


@dataclass(frozen=True, slots=True)
class EncodingConfig:
    """VP9 encoder and optimizer defaults."""

    quality: str = "best"
    min_bitrate_kbps: int = 24
    max_bitrate_kbps: int = 5000
    search_iterations: int = 11
    size_tolerance_bytes: int = 768
    unsharp_amount: float = 0.35


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Parallelism, cache and display settings."""

    workers: str | int = "auto"
    threads: int = 0
    overwrite: bool = False
    cache_enabled: bool = True
    verbose: bool = False


@dataclass(frozen=True, slots=True)
class QualityProfile:
    """libvpx speed/quality trade-off."""

    name: str
    deadline: str
    cpu_used: int
    crf_candidates: tuple[int, ...]
    search_iterations: int


QUALITY_PROFILES: Mapping[str, QualityProfile] = {
    "fast": QualityProfile("fast", "good", 5, (18, 24, 30, 36, 42), 6),
    "balanced": QualityProfile("balanced", "good", 3, (10, 16, 22, 28, 34, 40), 8),
    "best": QualityProfile("best", "good", 1, (4, 8, 12, 16, 22, 28, 36, 44), 11),
}


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Complete immutable application configuration."""

    platform: PlatformSpec = field(default_factory=PlatformSpec)
    crop: CropConfig = field(default_factory=CropConfig)
    encoding: EncodingConfig = field(default_factory=EncodingConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    @property
    def quality_profile(self) -> QualityProfile:
        try:
            return QUALITY_PROFILES[self.encoding.quality]
        except KeyError as exc:
            choices = ", ".join(QUALITY_PROFILES)
            raise ValueError(
                f"Unknown quality profile; choose one of: {choices}"
            ) from exc

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"Configuration section [{name}] must be a table")
    return value


def _construct(data: Mapping[str, Any]) -> AppConfig:
    return AppConfig(
        platform=PlatformSpec(**_section(data, "platform")),
        crop=CropConfig(**_section(data, "crop")),
        encoding=EncodingConfig(**_section(data, "encoding")),
        runtime=RuntimeConfig(**_section(data, "runtime")),
    )


def validate_config(config: AppConfig) -> None:
    """Reject unsafe or internally inconsistent values early."""

    platform = config.platform
    crop = config.crop
    encoding = config.encoding
    if platform.width <= 0 or platform.height <= 0 or platform.fps <= 0:
        raise ValueError("Output dimensions and FPS must be positive")
    if platform.max_duration <= 0 or platform.max_size_kib <= 0:
        raise ValueError("Duration and size limits must be positive")
    if not 0.5 <= crop.fill_ratio <= 1.0:
        raise ValueError("crop.fill_ratio must be between 0.5 and 1.0")
    if not 0.0 <= crop.padding_ratio <= 0.5:
        raise ValueError("crop.padding_ratio must be between 0 and 0.5")
    if crop.zoom not in {"auto", "fit"}:
        raise ValueError("crop.zoom must be 'auto' or 'fit'")
    if encoding.min_bitrate_kbps <= 0:
        raise ValueError("encoding.min_bitrate_kbps must be positive")
    if encoding.max_bitrate_kbps < encoding.min_bitrate_kbps:
        raise ValueError("Maximum bitrate cannot be below minimum bitrate")
    if encoding.search_iterations < 1:
        raise ValueError("encoding.search_iterations must be positive")
    _ = config.quality_profile


def load_config(path: Path | None = None) -> AppConfig:
    """Load TOML configuration, falling back to built-in defaults."""

    candidate = path
    if candidate is None:
        env_path = os.environ.get("STICKER_MAKER_CONFIG")
        candidate = (
            Path(env_path).expanduser() if env_path else Path("sticker-maker.toml")
        )

    if candidate.exists():
        with candidate.open("rb") as handle:
            raw = tomllib.load(handle)
        config = _construct(raw)
    elif path is not None:
        raise FileNotFoundError(f"Configuration file not found: {candidate}")
    else:
        config = AppConfig()

    validate_config(config)
    return config


def with_overrides(
    config: AppConfig,
    *,
    quality: str | None = None,
    zoom: str | None = None,
    padding_ratio: float | None = None,
    threads: int | None = None,
    workers: str | int | None = None,
    overwrite: bool | None = None,
    cache_enabled: bool | None = None,
    verbose: bool | None = None,
    max_size_kib: int | None = None,
) -> AppConfig:
    """Apply only explicitly supplied CLI values."""

    platform = config.platform
    crop = config.crop
    encoding = config.encoding
    runtime = config.runtime
    if max_size_kib is not None:
        platform = replace(platform, max_size_kib=max_size_kib)
    if zoom is not None or padding_ratio is not None:
        crop = replace(
            crop,
            zoom=zoom if zoom is not None else crop.zoom,
            padding_ratio=(
                padding_ratio if padding_ratio is not None else crop.padding_ratio
            ),
        )
    if quality is not None:
        encoding = replace(encoding, quality=quality)
    runtime = replace(
        runtime,
        threads=threads if threads is not None else runtime.threads,
        workers=workers if workers is not None else runtime.workers,
        overwrite=overwrite if overwrite is not None else runtime.overwrite,
        cache_enabled=cache_enabled
        if cache_enabled is not None
        else runtime.cache_enabled,
        verbose=verbose if verbose is not None else runtime.verbose,
    )
    result = AppConfig(platform, crop, encoding, runtime)
    validate_config(result)
    return result
