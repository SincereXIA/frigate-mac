"""Tests for the native macOS development baseline."""

import json
import platform
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MACOS_DIR = REPO_ROOT / "macos"
SUPPORT_FILE = MACOS_DIR / "support.json"
PATCHES_FILE = MACOS_DIR / "patches.json"
FIXTURE_CONFIG = MACOS_DIR / "tests" / "fixtures" / "config.yaml"
FIXTURE_MEDIA = MACOS_DIR / "tests" / "fixtures" / "native-macos-h264.mp4"


class TestNativeMacOSBaseline(unittest.TestCase):
    """Validate the phase 0 metadata and synthetic media fixture."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.support = json.loads(SUPPORT_FILE.read_text())
        cls.patch_manifest = json.loads(PATCHES_FILE.read_text())

    def test_upstream_baseline_is_consistent(self) -> None:
        commit = self.support["upstream"]["commit"]

        self.assertRegex(commit, re.compile(r"^[0-9a-f]{40}$"))
        self.assertEqual(commit, self.patch_manifest["baseline_commit"])
        self.assertEqual(
            self.support["upstream"]["repository"],
            "https://github.com/blakeblackshear/frigate.git",
        )

    def test_native_target_is_apple_silicon(self) -> None:
        target = self.support["native_target"]

        self.assertEqual(target["architecture"], "arm64")
        self.assertGreaterEqual(int(target["minimum_macos"].split(".")[0]), 13)
        self.assertEqual(target["development_python"], "3.13")
        self.assertIn("3.11", target["runtime_python_compatibility"])
        self.assertIn("3.13", target["runtime_python_compatibility"])
        self.assertEqual(target["production_container_python"], "3.11.2")

    def test_every_patch_has_owner_and_acceptance_tests(self) -> None:
        patches = self.patch_manifest["patches"]
        patch_ids = [patch["id"] for patch in patches]

        self.assertEqual(len(patch_ids), len(set(patch_ids)))
        self.assertEqual({patch["phase"] for patch in patches}, set(range(6)))
        for patch in patches:
            self.assertTrue(patch["owner"])
            self.assertTrue(patch["paths"])
            self.assertTrue(patch["acceptance_tests"])
            self.assertIn(patch["status"], {"planned", "implemented", "verified"})

    def test_configuration_inventory_is_redacted(self) -> None:
        serialized = json.dumps(self.support["configuration_compatibility"])
        forbidden = ("rtsp://", "password", "username", "api_key", "token")

        for value in forbidden:
            self.assertNotIn(value, serialized.lower())

    def test_fixture_configuration_contains_no_credentials(self) -> None:
        config = FIXTURE_CONFIG.read_text()
        forbidden = ("rtsp://", "password:", "username:", "api_key:", "token:")

        self.assertIn("{FRIGATE_TEST_MEDIA}", config)
        for value in forbidden:
            self.assertNotIn(value, config.lower())

    def test_fixture_is_an_mp4_container(self) -> None:
        header = FIXTURE_MEDIA.read_bytes()[:12]

        self.assertGreater(FIXTURE_MEDIA.stat().st_size, 0)
        self.assertEqual(header[4:8], b"ftyp")

    @unittest.skipUnless(sys.platform == "darwin", "requires native macOS")
    def test_local_media_is_h264_on_apple_silicon(self) -> None:
        self.assertEqual(platform.machine(), "arm64")
        ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height",
                "-of",
                "json",
                str(FIXTURE_MEDIA),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        stream = json.loads(result.stdout)["streams"][0]

        self.assertEqual(stream["codec_name"], "h264")
        self.assertEqual((stream["width"], stream["height"]), (320, 180))


if __name__ == "__main__":
    unittest.main()
