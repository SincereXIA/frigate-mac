"""Tests for native macOS VideoToolbox FFmpeg presets."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from frigate.const import FFMPEG_HWACCEL_VIDEOTOOLBOX
from frigate.ffmpeg_presets import (
    EncodeTypeEnum,
    parse_preset_hardware_acceleration_decode,
    parse_preset_hardware_acceleration_encode,
    parse_preset_hardware_acceleration_scale,
    parse_preset_output_record,
)
from frigate.stats.util import get_camera_hwaccel_status, set_gpu_stats
from frigate.util import services


class TestVideoToolboxPresets(unittest.TestCase):
    """Validate strict hardware decode and encode command generation."""

    def test_decode_and_scale_remain_on_videotoolbox(self) -> None:
        decode = parse_preset_hardware_acceleration_decode(
            FFMPEG_HWACCEL_VIDEOTOOLBOX,
            fps=5,
            width=320,
            height=180,
            gpu=0,
        )
        scale = parse_preset_hardware_acceleration_scale(
            FFMPEG_HWACCEL_VIDEOTOOLBOX,
            ["-f", "rawvideo", "-pix_fmt", "yuv420p"],
            fps=5,
            width=320,
            height=180,
        )

        self.assertEqual(
            decode,
            [
                "-hwaccel",
                "videotoolbox",
                "-hwaccel_output_format",
                "videotoolbox_vld",
            ],
        )
        self.assertIn("fps=5,scale_vt=w=320:h=180,hwdownload,format=nv12", scale)

    def test_encode_presets_disallow_software_fallback(self) -> None:
        for encode_type in EncodeTypeEnum:
            command = parse_preset_hardware_acceleration_encode(
                "/native/ffmpeg",
                FFMPEG_HWACCEL_VIDEOTOOLBOX,
                "-i input",
                "output.mp4",
                encode_type,
            )
            self.assertIn("-c:v h264_videotoolbox", command)
            self.assertIn("-allow_sw 0", command)

        timelapse = parse_preset_hardware_acceleration_encode(
            "/native/ffmpeg",
            FFMPEG_HWACCEL_VIDEOTOOLBOX,
            "-i input",
            "output.mp4",
            EncodeTypeEnum.timelapse,
        )
        self.assertIn("-hwaccel videotoolbox", timelapse)

    def test_apple_silicon_auto_detection_selects_videotoolbox(self) -> None:
        with (
            patch.object(services.sys, "platform", "darwin"),
            patch.object(services.platform, "machine", return_value="arm64"),
        ):
            result = services.auto_detect_hwaccel()

        self.assertEqual(result, FFMPEG_HWACCEL_VIDEOTOOLBOX)

    def test_camera_status_is_active_only_while_frames_are_flowing(self) -> None:
        self.assertEqual(
            get_camera_hwaccel_status(
                FFMPEG_HWACCEL_VIDEOTOOLBOX, ffmpeg_pid=123, current_fps=5
            ),
            {
                "requested": "videotoolbox",
                "decode_required": True,
                "status": "active",
            },
        )
        self.assertEqual(
            get_camera_hwaccel_status(
                FFMPEG_HWACCEL_VIDEOTOOLBOX, ffmpeg_pid=123, current_fps=0
            )["status"],
            "inactive",
        )
        self.assertIsNone(get_camera_hwaccel_status("", 123, 5))

    def test_gpu_stats_report_configured_videotoolbox(self) -> None:
        camera = SimpleNamespace(
            ffmpeg=SimpleNamespace(
                hwaccel_args=FFMPEG_HWACCEL_VIDEOTOOLBOX,
                inputs=[],
            )
        )
        config = SimpleNamespace(cameras={"front": camera})
        stats = {}

        asyncio.run(set_gpu_stats(config, stats, {}))

        self.assertEqual(
            stats["gpu_usages"]["apple-videotoolbox"],
            {
                "vendor": "apple",
                "gpu": "",
                "mem": "",
                "decode": "configured",
                "encode": "configured",
            },
        )

    def test_recording_preset_remains_stream_copy(self) -> None:
        record = parse_preset_output_record(
            "preset-record-generic-audio-aac", force_record_hvc1=False
        )

        self.assertIsNotNone(record)
        self.assertIn("copy", record)
        self.assertNotIn("h264_videotoolbox", record)


if __name__ == "__main__":
    unittest.main()
