"""Conservative crop, zoom, scaling and timing plan construction."""

from __future__ import annotations

from sticker_maker.config import CropConfig, EncodingConfig, PlatformSpec
from sticker_maker.models import AnalysisResult, BoundingBox, CropPlan, MediaInfo


def speed_factor(duration: float, platform: PlatformSpec) -> float:
    """Accelerate long inputs while retaining their complete timeline."""

    if duration <= platform.max_duration:
        return 1.0
    # Leave one output-frame interval of muxing tolerance. No trim is applied;
    # every timestamp is compressed into the resulting timeline.
    safe_duration = max(0.1, platform.max_duration - (1.0 / platform.fps))
    return duration / safe_duration


def build_crop_plan(
    media: MediaInfo,
    analysis: AnalysisResult,
    crop: CropConfig,
    encoding: EncodingConfig,
    platform: PlatformSpec,
) -> CropPlan:
    """Create one filter graph used identically in both VP9 passes."""

    if crop.zoom == "auto":
        detected = analysis.bounding_box
        padding = max(
            2, round(max(detected.width, detected.height) * crop.padding_ratio)
        )
        box = detected.expand(padding, media.width, media.height)
    else:
        box = BoundingBox(0, 0, media.width, media.height)

    target = int(min(platform.width, platform.height) * crop.fill_ratio)
    target -= target % 2
    target = max(2, target)
    speed = speed_factor(media.duration, platform)
    output_duration = media.duration / speed

    filters = [
        f"setpts=PTS/{speed:.12f}",
        f"fps={platform.fps:g}",
        f"crop={box.width}:{box.height}:{box.x}:{box.y}",
        (
            f"scale={target}:{target}:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2:flags=lanczos+accurate_rnd+full_chroma_int"
        ),
        (
            f"pad={platform.width}:{platform.height}:"
            "(ow-iw)/2:(oh-ih)/2:color=0x00000000"
        ),
        "setsar=1",
    ]
    if encoding.unsharp_amount > 0:
        filters.append(f"unsharp=5:5:{encoding.unsharp_amount:.3f}:5:5:0.0")
    filters.append("format=yuva420p")
    return CropPlan(box, target, speed, output_duration, ",".join(filters))
