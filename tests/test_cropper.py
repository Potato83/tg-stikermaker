from pathlib import Path

from sticker_maker.config import CropConfig, EncodingConfig, PlatformSpec
from sticker_maker.cropper import build_crop_plan, speed_factor
from sticker_maker.models import AnalysisResult, BoundingBox, MediaInfo


def media(duration: float = 6.0) -> MediaInfo:
    return MediaInfo(
        path=Path("source.gif"),
        width=800,
        height=600,
        duration=duration,
        fps=24.0,
        frame_count=144,
        codec_name="gif",
        pixel_format="bgra",
        has_audio=False,
        alpha_metadata=True,
        format_name="gif",
        size_bytes=1000,
    )


def test_long_animation_is_accelerated_without_trim_filter() -> None:
    platform = PlatformSpec()
    analysis = AnalysisResult(
        BoundingBox(100, 50, 400, 500), 144, True, "alpha-union", 0.4
    )
    plan = build_crop_plan(media(), analysis, CropConfig(), EncodingConfig(), platform)

    assert plan.speed_factor > 2.0
    assert plan.output_duration < 3.0
    assert "trim=" not in plan.filter_graph
    assert "setpts=PTS/" in plan.filter_graph
    assert "fps=30" in plan.filter_graph
    assert "flags=lanczos" in plan.filter_graph
    assert "pad=512:512" in plan.filter_graph


def test_fit_mode_uses_full_frame() -> None:
    analysis = AnalysisResult(
        BoundingBox(100, 50, 200, 200), 12, True, "alpha-union", 0.2
    )
    plan = build_crop_plan(
        media(2.0),
        analysis,
        CropConfig(zoom="fit"),
        EncodingConfig(),
        PlatformSpec(),
    )
    assert plan.bounding_box == BoundingBox(0, 0, 800, 600)


def test_short_animation_keeps_normal_speed() -> None:
    assert speed_factor(2.5, PlatformSpec()) == 1.0
