"""Bounded bitrate search that maximizes quality under a byte limit."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

from sticker_maker.models import EncodeAttempt, OptimizationResult


EncodeFunction = Callable[[int, int, Path], None]


class OptimizationError(RuntimeError):
    """No encoder setting could satisfy the platform size limit."""


class BitrateOptimizer:
    """Run two-pass encodes and retain the closest valid byte size."""

    def __init__(
        self,
        *,
        max_size_bytes: int,
        min_bitrate_kbps: int,
        max_bitrate_kbps: int,
        iterations: int,
        tolerance_bytes: int,
        crf_candidates: tuple[int, ...],
    ) -> None:
        self.max_size_bytes = max_size_bytes
        self.min_bitrate_kbps = min_bitrate_kbps
        self.max_bitrate_kbps = max_bitrate_kbps
        self.iterations = iterations
        self.tolerance_bytes = tolerance_bytes
        self.crf_candidates = crf_candidates

    def optimize(
        self,
        *,
        encode: EncodeFunction,
        candidate_path: Path,
        final_path: Path,
        duration: float,
    ) -> OptimizationResult:
        """Find the largest valid result, increasing CRF only if necessary."""

        attempts: list[EncodeAttempt] = []
        best: EncodeAttempt | None = None
        # The relation between requested bitrate and WebM bytes is deliberately
        # not assumed: short/simple clips can undershoot by an order of magnitude.
        # Every bound below is established from a completed, measured encode.
        upper_limit = self.max_bitrate_kbps

        for crf in self.crf_candidates:
            low = self.min_bitrate_kbps
            high = upper_limit
            valid_for_crf = False
            seen_rates: set[int] = set()

            for _ in range(self.iterations):
                if low > high:
                    break
                bitrate = (low + high) // 2
                if bitrate in seen_rates:
                    break
                seen_rates.add(bitrate)
                started = time.monotonic()
                encode(bitrate, crf, candidate_path)
                elapsed = time.monotonic() - started
                size = candidate_path.stat().st_size
                attempt = EncodeAttempt(bitrate, crf, size, candidate_path, elapsed)
                attempts.append(attempt)

                if size <= self.max_size_bytes:
                    valid_for_crf = True
                    if (
                        best is None
                        or size > best.size_bytes
                        or (size == best.size_bytes and crf < best.crf)
                    ):
                        final_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(candidate_path, final_path)
                        best = EncodeAttempt(bitrate, crf, size, final_path, elapsed)
                    if self.max_size_bytes - size <= self.tolerance_bytes:
                        return OptimizationResult(best, tuple(attempts))
                    low = bitrate + 1
                else:
                    high = bitrate - 1

            # Lower CRF is preferable at a comparable constrained bitrate. If
            # it already produced a valid file, further CRFs cannot improve it.
            if valid_for_crf:
                break

        if best is None:
            raise OptimizationError(
                "Could not encode below the size limit even at the minimum bitrate. "
                "Try a higher max_size value or a simpler/shorter source."
            )
        return OptimizationResult(best, tuple(attempts))
