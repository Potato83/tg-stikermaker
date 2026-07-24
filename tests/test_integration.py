from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sticker_maker.config import AppConfig, EncodingConfig, RuntimeConfig
from sticker_maker.encoder import StickerEncoder, Verifier
from sticker_maker.ffmpeg import DependencyError, FFmpeg
from sticker_maker.models import ResultStatus


def _ffmpeg_available() -> bool:
    try:
        FFmpeg().ensure_available()
    except DependencyError:
        return False
    return True


@pytest.mark.skipif(not _ffmpeg_available(), reason="libvpx-vp9 is unavailable")
def test_transparent_gif_end_to_end(tmp_path: Path) -> None:
    source = tmp_path / "moving.gif"
    frames: list[Image.Image] = []
    for index in range(12):
        frame = Image.new("RGBA", (96, 72), (0, 0, 0, 0))
        draw = ImageDraw.Draw(frame)
        x = 8 + index * 4
        draw.ellipse((x, 18, x + 28, 46), fill=(230, 40, 90, 255))
        frames.append(frame)
    frames[0].save(
        source,
        save_all=True,
        append_images=frames[1:],
        duration=80,
        loop=0,
        disposal=2,
        transparency=0,
    )

    base = AppConfig()
    config = replace(
        base,
        encoding=EncodingConfig(
            quality="fast",
            min_bitrate_kbps=24,
            max_bitrate_kbps=500,
            search_iterations=4,
            size_tolerance_bytes=4096,
            unsharp_amount=0.2,
        ),
        runtime=RuntimeConfig(
            workers=1, threads=2, overwrite=True, cache_enabled=False
        ),
    )
    output = tmp_path / "moving.webm"

    result = StickerEncoder(config, tmp_path / "cache").convert(source, output)
    report = Verifier(FFmpeg(), config).verify(output)

    assert result.status is ResultStatus.CREATED
    assert output.stat().st_size <= 256 * 1024
    assert report.ok, [issue.message for issue in report.issues]
    assert report.media is not None
    assert (report.media.width, report.media.height) == (512, 512)
    assert report.media.fps == pytest.approx(30.0)
