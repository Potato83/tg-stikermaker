import numpy as np

from sticker_maker.detector import ObjectDetector, _mask_box
from sticker_maker.ffmpeg import FFmpeg
from sticker_maker.models import BoundingBox


def test_content_mask_finds_object_on_flat_background() -> None:
    rgb = np.full((100, 120, 3), 240, dtype=np.uint8)
    rgb[20:80, 30:90] = (20, 70, 180)
    detector = ObjectDetector(FFmpeg(), background_threshold=20.0)

    box = _mask_box(detector.content_mask(rgb))

    # The neighbor vote deliberately leaves a one-pixel safety halo.
    assert box == BoundingBox(29, 19, 62, 62)


def test_empty_mask_has_no_box() -> None:
    assert _mask_box(np.zeros((10, 10), dtype=bool)) is None
