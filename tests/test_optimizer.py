from pathlib import Path

import pytest

from sticker_maker.optimizer import BitrateOptimizer, OptimizationError


def test_binary_search_keeps_largest_file_below_limit(tmp_path: Path) -> None:
    optimizer = BitrateOptimizer(
        max_size_bytes=10_000,
        min_bitrate_kbps=10,
        max_bitrate_kbps=200,
        iterations=12,
        tolerance_bytes=0,
        crf_candidates=(4, 12),
    )

    def fake_encode(bitrate: int, crf: int, path: Path) -> None:
        path.write_bytes(b"x" * (bitrate * 100))

    result = optimizer.optimize(
        encode=fake_encode,
        candidate_path=tmp_path / "candidate.webm",
        final_path=tmp_path / "final.webm",
        duration=3.0,
    )

    assert result.best.size_bytes == 10_000
    assert result.best.bitrate_kbps == 100
    assert result.best.crf == 4
    assert (tmp_path / "final.webm").stat().st_size == 10_000


def test_optimizer_reports_impossible_limit(tmp_path: Path) -> None:
    optimizer = BitrateOptimizer(
        max_size_bytes=10,
        min_bitrate_kbps=20,
        max_bitrate_kbps=40,
        iterations=3,
        tolerance_bytes=0,
        crf_candidates=(4, 40),
    )

    def fake_encode(bitrate: int, crf: int, path: Path) -> None:
        path.write_bytes(b"x" * 100)

    with pytest.raises(OptimizationError):
        optimizer.optimize(
            encode=fake_encode,
            candidate_path=tmp_path / "candidate.webm",
            final_path=tmp_path / "final.webm",
            duration=1.0,
        )
