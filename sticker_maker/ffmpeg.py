"""Safe subprocess boundary for FFmpeg and ffprobe."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, BinaryIO


class FFmpegError(RuntimeError):
    """A media command failed with a user-readable diagnostic."""

    def __init__(self, command: Sequence[str], stderr: str, returncode: int) -> None:
        self.command = tuple(command)
        self.stderr = stderr.strip()
        self.returncode = returncode
        detail = self.stderr or "no diagnostic output"
        super().__init__(f"FFmpeg exited with code {returncode}: {detail}")


class DependencyError(RuntimeError):
    """A required executable or codec is unavailable."""


class FFmpeg:
    """Thin, typed wrapper around locally installed FFmpeg tools."""

    def __init__(
        self, ffmpeg_bin: str = "ffmpeg", ffprobe_bin: str = "ffprobe"
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin

    def ensure_available(self) -> None:
        """Validate executables and required VP9 encoder support."""

        missing = [
            binary
            for binary in (self.ffmpeg_bin, self.ffprobe_bin)
            if shutil.which(binary) is None
        ]
        if missing:
            names = ", ".join(missing)
            raise DependencyError(
                f"Missing required executable(s): {names}. Install FFmpeg and ensure it is on PATH."
            )
        result = self.run(
            [self.ffmpeg_bin, "-hide_banner", "-encoders"],
            check=True,
            capture_stdout=True,
        )
        if "libvpx-vp9" not in result.stdout:
            raise DependencyError(
                "This FFmpeg build has no libvpx-vp9 encoder. Install a build compiled with --enable-libvpx."
            )

    def run(
        self,
        command: Sequence[str],
        *,
        check: bool = True,
        capture_stdout: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command without a shell and capture diagnostics."""

        result = subprocess.run(
            list(command),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and result.returncode != 0:
            raise FFmpegError(command, result.stderr, result.returncode)
        return result

    def probe_json(self, path: Path, *, count_frames: bool = False) -> dict[str, Any]:
        """Return ffprobe JSON for all streams and the container."""

        command = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
        ]
        if count_frames:
            command.append("-count_frames")
        command.append(str(path))
        result = self.run(command, capture_stdout=True)
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise FFmpegError(command, "ffprobe returned invalid JSON", 1) from exc
        if not isinstance(parsed, dict):
            raise FFmpegError(command, "ffprobe returned an unexpected document", 1)
        return parsed

    def rgba_frames(self, path: Path, width: int, height: int) -> Iterator[bytes]:
        """Decode and yield every source video frame as RGBA bytes."""

        command = [
            self.ffmpeg_bin,
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vsync",
            "0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "pipe:1",
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            raise RuntimeError("Could not open FFmpeg pipes")

        frame_size = width * height * 4
        try:
            while True:
                frame = self._read_exact(process.stdout, frame_size)
                if not frame:
                    break
                if len(frame) != frame_size:
                    process.kill()
                    stderr = process.stderr.read().decode("utf-8", errors="replace")
                    raise FFmpegError(command, f"Truncated raw frame. {stderr}", 1)
                yield frame
        finally:
            process.stdout.close()

        stderr = process.stderr.read().decode("utf-8", errors="replace")
        returncode = process.wait()
        process.stderr.close()
        if returncode != 0:
            raise FFmpegError(command, stderr, returncode)

    @staticmethod
    def _read_exact(stream: BinaryIO, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = stream.read(size - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)

    def decode_first_rgba(self, path: Path, width: int, height: int) -> bytes:
        """Decode the first frame with libvpx so WebM alpha is exposed."""

        command = [
            self.ffmpeg_bin,
            "-v",
            "error",
            "-c:v",
            "libvpx-vp9",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "pipe:1",
        ]
        result = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise FFmpegError(
                command,
                result.stderr.decode("utf-8", errors="replace"),
                result.returncode,
            )
        expected = width * height * 4
        if len(result.stdout) < expected:
            raise FFmpegError(command, "Could not decode a complete RGBA frame", 1)
        return result.stdout[:expected]

    def encode_two_pass(
        self,
        *,
        source: Path,
        output: Path,
        filter_graph: str,
        bitrate_kbps: int,
        crf: int,
        threads: int,
        deadline: str,
        cpu_used: int,
        passlog: Path,
        codec: str,
        pixel_format: str,
    ) -> None:
        """Perform a genuine libvpx first pass and matching second pass."""

        output.parent.mkdir(parents=True, exist_ok=True)
        common = [
            self.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            filter_graph,
            "-c:v",
            codec,
            "-pix_fmt",
            pixel_format,
            "-b:v",
            f"{bitrate_kbps}k",
            "-crf",
            str(crf),
            "-deadline",
            deadline,
            "-cpu-used",
            str(cpu_used),
            "-row-mt",
            "1",
            "-auto-alt-ref",
            "0",
            "-lag-in-frames",
            "0",
            "-threads",
            str(max(1, threads)),
            "-map_metadata",
            "-1",
            "-metadata:s:v:0",
            "alpha_mode=1",
            "-passlogfile",
            str(passlog),
        ]
        first_pass = [*common, "-pass", "1", "-f", "webm", os.devnull]
        second_pass = [*common, "-pass", "2", "-f", "webm", str(output)]
        self.run(first_pass)
        self.run(second_pass)

    def create_contact_sheet(
        self, source: Path, output: Path, frames: int = 12
    ) -> None:
        """Render evenly sampled frames to a transparent PNG sheet."""

        import math

        columns = math.ceil(math.sqrt(frames))
        rows = math.ceil(frames / columns)
        probe = self.probe_json(source)
        duration_raw = probe.get("format", {}).get("duration", 3.0)
        try:
            duration = max(0.1, float(duration_raw))
        except (TypeError, ValueError):
            duration = 3.0
        fps = frames / duration
        graph = (
            f"fps={fps:.8f},scale=256:256:flags=lanczos,"
            f"tile={columns}x{rows}:nb_frames={frames}:padding=8:margin=8:color=0x202124"
        )
        command = [
            self.ffmpeg_bin,
            "-v",
            "error",
            "-c:v",
            "libvpx-vp9",
            "-i",
            str(source),
            "-vf",
            graph,
            "-frames:v",
            "1",
            "-y",
            str(output),
        ]
        output.parent.mkdir(parents=True, exist_ok=True)
        self.run(command)

    def create_gif_preview(self, source: Path, output: Path, fps: int = 15) -> None:
        """Create a palette-optimized GIF preview."""

        graph = (
            f"[0:v]fps={fps},scale=512:512:flags=lanczos,split[a][b];"
            "[a]palettegen=reserve_transparent=1:stats_mode=diff[p];"
            "[b][p]paletteuse=alpha_threshold=128:dither=sierra2_4a"
        )
        command = [
            self.ffmpeg_bin,
            "-v",
            "error",
            "-c:v",
            "libvpx-vp9",
            "-i",
            str(source),
            "-filter_complex",
            graph,
            "-loop",
            "0",
            "-y",
            str(output),
        ]
        output.parent.mkdir(parents=True, exist_ok=True)
        self.run(command)
