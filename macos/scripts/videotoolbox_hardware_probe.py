#!/usr/bin/env python3
"""Exercise Frigate VideoToolbox presets with synthetic local media."""

import argparse
import json
import shlex
import subprocess
from pathlib import Path


def parse_arguments() -> argparse.Namespace:
    """Parse native FFmpeg and bounded output paths."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def run_command(command: list[str], log_path: Path) -> None:
    """Run a command and retain diagnostics only inside the scratch directory."""
    with log_path.open("wb") as log_file:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if result.returncode:
        raise RuntimeError(
            f"FFmpeg probe {log_path.stem} failed with code {result.returncode}"
        )


def verify_hardware_decode(log_path: Path, codec: str) -> None:
    """Require FFmpeg to report a VideoToolbox decoder selection."""
    log = log_path.read_text(errors="replace")
    expected = (
        f"Selecting decoder '{codec}' because of requested hwaccel method videotoolbox"
    )
    if expected not in log:
        raise RuntimeError(f"{codec} did not select VideoToolbox")
    if "decode errors" not in log or "0 decode errors" not in log:
        raise RuntimeError(f"{codec} decode did not complete cleanly")


def probe_video(ffprobe: Path, path: Path) -> dict[str, object]:
    """Return a small codec summary for one generated video."""
    command = [
        str(ffprobe),
        "-v",
        "error",
        "-count_frames",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,pix_fmt,nb_read_frames,width,height",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    stream = json.loads(result.stdout)["streams"][0]
    return {
        "codec": stream["codec_name"],
        "pixel_format": stream.get("pix_fmt"),
        "frames": int(stream.get("nb_read_frames", 0)),
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "bytes": path.stat().st_size,
    }


def main() -> int:
    """Run H.264/HEVC decode and all VideoToolbox encode preset checks."""
    arguments = parse_arguments()
    arguments.scratch.mkdir(mode=0o700, parents=True, exist_ok=True)

    from frigate.const import FFMPEG_HWACCEL_VIDEOTOOLBOX
    from frigate.ffmpeg_presets import (
        EncodeTypeEnum,
        parse_preset_hardware_acceleration_decode,
        parse_preset_hardware_acceleration_encode,
        parse_preset_hardware_acceleration_scale,
    )

    decode = parse_preset_hardware_acceleration_decode(
        FFMPEG_HWACCEL_VIDEOTOOLBOX, 5, 320, 180, 0
    )
    if decode is None:
        raise RuntimeError("VideoToolbox decode preset is unavailable")
    scale = parse_preset_hardware_acceleration_scale(
        FFMPEG_HWACCEL_VIDEOTOOLBOX,
        ["-f", "rawvideo", "-pix_fmt", "yuv420p"],
        5,
        320,
        180,
    )

    h264_raw = arguments.scratch / "h264.yuv"
    h264_log = arguments.scratch / "h264-decode.log"
    run_command(
        [
            str(arguments.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "verbose",
            *decode,
            "-i",
            str(arguments.fixture),
            "-an",
            *scale,
            "-frames:v",
            "10",
            str(h264_raw),
        ],
        h264_log,
    )
    verify_hardware_decode(h264_log, "h264")

    hevc_path = arguments.scratch / "hevc.mp4"
    run_command(
        [
            str(arguments.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "verbose",
            "-y",
            "-i",
            str(arguments.fixture),
            "-an",
            "-c:v",
            "hevc_videotoolbox",
            "-allow_sw",
            "0",
            "-realtime",
            "1",
            "-tag:v",
            "hvc1",
            str(hevc_path),
        ],
        arguments.scratch / "hevc-encode.log",
    )
    hevc_raw = arguments.scratch / "hevc.yuv"
    hevc_log = arguments.scratch / "hevc-decode.log"
    run_command(
        [
            str(arguments.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "verbose",
            *decode,
            "-i",
            str(hevc_path),
            "-an",
            *scale,
            "-frames:v",
            "10",
            str(hevc_raw),
        ],
        hevc_log,
    )
    verify_hardware_decode(hevc_log, "hevc")

    outputs: dict[str, dict[str, object]] = {
        "hevc": probe_video(arguments.ffprobe, hevc_path)
    }
    for encode_type in EncodeTypeEnum:
        output_path = arguments.scratch / f"{encode_type.value}.mp4"
        if encode_type == EncodeTypeEnum.timelapse:
            input_args = f"-y -i {shlex.quote(str(arguments.fixture))} -t 2 -an"
        else:
            input_args = "-y -f lavfi -i testsrc=size=320x180:rate=5 -t 2 -an"
        output_args = f"-movflags +faststart {shlex.quote(str(output_path))}"
        command = shlex.split(
            parse_preset_hardware_acceleration_encode(
                str(arguments.ffmpeg),
                FFMPEG_HWACCEL_VIDEOTOOLBOX,
                input_args,
                output_args,
                encode_type,
            )
        )
        if "h264_videotoolbox" not in command or not {"-allow_sw", "0"}.issubset(
            command
        ):
            raise RuntimeError(f"{encode_type.value} allows a software fallback")
        log_path = arguments.scratch / f"{encode_type.value}-encode.log"
        run_command(command, log_path)
        if "h264_videotoolbox" not in log_path.read_text(errors="replace"):
            raise RuntimeError(f"{encode_type.value} did not use VideoToolbox")
        outputs[encode_type.value] = probe_video(arguments.ffprobe, output_path)

    expected_raw_bytes = 320 * 180 * 3 // 2 * 10
    if h264_raw.stat().st_size != expected_raw_bytes:
        raise RuntimeError("H.264 hardware decode produced an unexpected frame count")
    if hevc_raw.stat().st_size != expected_raw_bytes:
        raise RuntimeError("HEVC hardware decode produced an unexpected frame count")

    arguments.output.write_text(
        json.dumps(
            {
                "h264_decode": {
                    "frames": 10,
                    "bytes": h264_raw.stat().st_size,
                    "hardware": "videotoolbox",
                },
                "hevc_decode": {
                    "frames": 10,
                    "bytes": hevc_raw.stat().st_size,
                    "hardware": "videotoolbox",
                },
                "outputs": outputs,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
