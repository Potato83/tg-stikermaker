from sticker_maker.models import BoundingBox


def test_bounding_box_union_and_expand() -> None:
    first = BoundingBox(10, 20, 30, 40)
    second = BoundingBox(5, 30, 20, 50)

    assert first.union(second) == BoundingBox(5, 20, 35, 60)
    assert first.expand(15, 100, 100) == BoundingBox(0, 5, 55, 70)


def test_bounding_box_clamps_to_frame() -> None:
    box = BoundingBox(90, 95, 10, 5)
    assert box.expand(20, 100, 100) == BoundingBox(70, 75, 30, 25)
