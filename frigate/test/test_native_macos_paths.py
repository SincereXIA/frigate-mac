"""Tests for platform-aware Frigate runtime paths."""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from frigate.runtime.paths import RuntimePaths
from frigate.util.config import resolve_ffmpeg_path


class TestRuntimePaths(unittest.TestCase):
    """Validate Docker defaults and native path overrides."""

    def test_docker_defaults_remain_unchanged(self) -> None:
        paths = RuntimePaths.from_environment({})

        self.assertEqual(paths.install_dir, Path("/opt/frigate"))
        self.assertEqual(paths.config_dir, Path("/config"))
        self.assertEqual(paths.database, Path("/config/frigate.db"))
        self.assertEqual(paths.model_cache_dir, Path("/config/model_cache"))
        self.assertEqual(paths.certificates_dir, Path("/config/certs"))
        self.assertEqual(paths.media_dir, Path("/media/frigate"))
        self.assertEqual(paths.cache_dir, Path("/tmp/cache"))
        self.assertEqual(paths.log_dir, Path("/dev/shm/logs"))
        self.assertEqual(paths.runtime_dir, Path("/tmp/cache"))
        self.assertEqual(paths.labelmap_path, Path("/labelmap.txt"))
        self.assertEqual(paths.audio_labelmap_path, Path("/audio-labelmap.txt"))
        self.assertEqual(paths.audio_model_path, Path("/cpu_audio_model.tflite"))
        self.assertEqual(paths.labelmap_dir, Path("/labelmap"))
        self.assertEqual(paths.birdseye_pipe, Path("/tmp/cache/birdseye"))
        self.assertEqual(paths.ipc_endpoint("comms"), "ipc:///tmp/cache/comms")

    def test_native_overrides_are_applied_to_derived_paths(self) -> None:
        root = Path("/tmp/frigate-native-test")
        paths = RuntimePaths.from_environment(
            {
                "FRIGATE_INSTALL_DIR": str(root / "install"),
                "FRIGATE_CONFIG_DIR": str(root / "config"),
                "FRIGATE_MEDIA_DIR": str(root / "media"),
                "FRIGATE_CACHE_DIR": str(root / "cache"),
                "FRIGATE_LOG_DIR": str(root / "logs"),
                "FRIGATE_RUNTIME_DIR": str(root / "runtime"),
            }
        )

        self.assertEqual(paths.database, root / "config" / "frigate.db")
        self.assertEqual(paths.model_cache_dir, root / "config" / "model_cache")
        self.assertEqual(paths.certificates_dir, root / "config" / "certs")
        self.assertEqual(paths.clips_dir, root / "media" / "clips")
        self.assertEqual(paths.recordings_dir, root / "media" / "recordings")
        self.assertEqual(paths.exports_dir, root / "media" / "exports")
        self.assertEqual(paths.birdseye_pipe, root / "runtime" / "birdseye")
        self.assertEqual(paths.labelmap_path, root / "install" / "labelmap.txt")
        self.assertEqual(
            paths.audio_labelmap_path, root / "install" / "audio-labelmap.txt"
        )
        self.assertEqual(
            paths.audio_model_path, root / "install" / "cpu_audio_model.tflite"
        )
        self.assertEqual(paths.labelmap_dir, root / "install" / "labelmap")
        self.assertEqual(
            paths.ipc_endpoint("proxy_pub"),
            "ipc:///tmp/frigate-native-test/runtime/proxy_pub",
        )

    def test_environment_export_round_trips(self) -> None:
        paths = RuntimePaths.from_environment(
            {
                "FRIGATE_INSTALL_DIR": "/a/install",
                "FRIGATE_CONFIG_DIR": "/a/config",
                "FRIGATE_MEDIA_DIR": "/a/media",
                "FRIGATE_CACHE_DIR": "/a/cache",
                "FRIGATE_LOG_DIR": "/a/logs",
                "FRIGATE_RUNTIME_DIR": "/a/runtime",
                "FRIGATE_LABELMAP_PATH": "/a/labels.txt",
                "FRIGATE_AUDIO_LABELMAP_PATH": "/a/audio-labels.txt",
                "FRIGATE_AUDIO_MODEL_PATH": "/a/audio-model.tflite",
                "FRIGATE_LABELMAP_DIR": "/a/labelmaps",
            }
        )

        self.assertEqual(RuntimePaths.from_environment(paths.as_environment()), paths)

    def test_relative_or_empty_overrides_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute path"):
            RuntimePaths.from_environment({"FRIGATE_CONFIG_DIR": "relative"})
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            RuntimePaths.from_environment({"FRIGATE_CONFIG_DIR": ""})

    def test_invalid_or_long_ipc_names_are_rejected(self) -> None:
        paths = RuntimePaths.from_environment(
            {"FRIGATE_RUNTIME_DIR": "/tmp/frigate-runtime"}
        )

        with self.assertRaisesRegex(ValueError, "Invalid IPC endpoint"):
            paths.ipc_endpoint("../outside")

        long_paths = RuntimePaths.from_environment(
            {"FRIGATE_RUNTIME_DIR": f"/tmp/{'a' * 100}"}
        )
        with self.assertRaisesRegex(ValueError, "exceeds"):
            long_paths.ipc_endpoint("comms")

    def test_new_runtime_directories_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = RuntimePaths.from_environment(
                {
                    "FRIGATE_INSTALL_DIR": str(root / "install"),
                    "FRIGATE_CONFIG_DIR": str(root / "config"),
                    "FRIGATE_MEDIA_DIR": str(root / "media"),
                    "FRIGATE_CACHE_DIR": str(root / "cache"),
                    "FRIGATE_LOG_DIR": str(root / "logs"),
                    "FRIGATE_RUNTIME_DIR": str(root / "runtime"),
                }
            )
            paths.ensure_directories([paths.model_cache_dir, paths.clips_dir])

            for directory in (
                paths.config_dir,
                paths.media_dir,
                paths.cache_dir,
                paths.log_dir,
                paths.runtime_dir,
                paths.model_cache_dir,
                paths.clips_dir,
            ):
                mode = stat.S_IMODE(os.stat(directory).st_mode)
                self.assertEqual(mode, 0o700)

    def test_native_defaults_use_user_library_directories(self) -> None:
        paths = RuntimePaths.native_defaults(
            Path("/Applications/Frigate.app/Contents/Resources"),
            home=Path("/Users/test"),
        )

        self.assertEqual(
            paths.config_dir,
            Path("/Users/test/Library/Application Support/Frigate/config"),
        )
        self.assertEqual(
            paths.runtime_dir, Path("/Users/test/Library/Caches/Frigate/runtime")
        )
        self.assertEqual(
            paths.labelmap_path,
            Path("/Applications/Frigate.app/Contents/Resources/labelmap.txt"),
        )
        self.assertEqual(
            paths.labelmap_dir,
            Path("/Applications/Frigate.app/Contents/Resources/labelmap"),
        )

    def test_macos_volumes_require_an_active_mount(self) -> None:
        paths = RuntimePaths.from_environment(
            {"FRIGATE_MEDIA_DIR": "/Volumes/frigate-missing-test"}
        )

        self.assertTrue(paths.media_directory_requires_mount("darwin"))
        self.assertFalse(paths.media_directory_available("darwin"))
        with patch("frigate.runtime.paths.sys.platform", "darwin"):
            with self.assertRaisesRegex(RuntimeError, "is not mounted"):
                paths.ensure_directories()

    def test_non_macos_media_directory_does_not_require_a_mount(self) -> None:
        paths = RuntimePaths.from_environment(
            {"FRIGATE_MEDIA_DIR": "/Volumes/frigate-local-test"}
        )

        self.assertFalse(paths.media_directory_requires_mount("linux"))

    def test_native_ffmpeg_binary_overrides_are_used_for_default_path(self) -> None:
        environment = {
            "FRIGATE_FFMPEG_PATH": "/app/runtime/bin/ffmpeg",
            "FRIGATE_FFPROBE_PATH": "/app/runtime/bin/ffprobe",
        }

        with patch.dict(os.environ, environment):
            self.assertEqual(
                resolve_ffmpeg_path("default"),
                "/app/runtime/bin/ffmpeg",
            )
            self.assertEqual(
                resolve_ffmpeg_path("default", "ffprobe"),
                "/app/runtime/bin/ffprobe",
            )

    def test_explicit_ffmpeg_path_ignores_native_binary_override(self) -> None:
        with patch.dict(
            os.environ,
            {"FRIGATE_FFMPEG_PATH": "/app/runtime/bin/ffmpeg"},
        ):
            self.assertEqual(
                resolve_ffmpeg_path("/custom/ffmpeg"),
                "/custom/ffmpeg/bin/ffmpeg",
            )


if __name__ == "__main__":
    unittest.main()
