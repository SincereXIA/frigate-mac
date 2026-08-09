"""Tests for macOS service and metrics adapters."""

import subprocess as sp
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from frigate.util import process as process_util
from frigate.util.services import (
    calculate_shm_requirements,
    get_physical_interfaces,
    vainfo_hwaccel,
)


class TestNativeMacOSServices(unittest.TestCase):
    """Ensure Linux-only metrics fail explicitly on native macOS."""

    def test_network_interfaces_use_psutil(self) -> None:
        with patch(
            "frigate.util.services.psutil.net_if_stats",
            return_value={
                "en0": SimpleNamespace(isup=True),
                "en1": SimpleNamespace(isup=False),
                "lo0": SimpleNamespace(isup=True),
            },
        ):
            interfaces = get_physical_interfaces(["en"])

        self.assertEqual(interfaces, ["en0"])

    def test_macos_shared_memory_uses_managed_frame_budget(self) -> None:
        config = SimpleNamespace(
            birdseye=SimpleNamespace(restream=False),
            cameras={
                "front": SimpleNamespace(
                    enabled_in_config=True,
                    detect=SimpleNamespace(width=1920, height=1080),
                ),
            },
        )
        with (
            patch(
                "frigate.util.services.shutil.disk_usage",
                side_effect=FileNotFoundError,
            ),
            patch(
                "frigate.util.services.get_darwin_posix_shm_usage",
                return_value={"bytes": 25 * 1024**2, "object_count": 9},
            ),
            patch("frigate.util.services.sys.platform", "darwin"),
            patch.dict("os.environ", {"SHM_MAX_FRAMES": "20"}),
        ):
            result = calculate_shm_requirements(config)

        self.assertTrue(result["supported"])
        self.assertEqual(result["mount_type"], "posix_shared_memory")
        self.assertEqual(result["capacity_type"], "managed_budget")
        self.assertTrue(result["metrics_available"])
        self.assertEqual(result["object_count"], 9)
        self.assertEqual(result["used"], 25.0)
        self.assertEqual(result["camera_frame_size"], 3.2)
        self.assertEqual(result["shm_frame_count"], 20)
        self.assertEqual(result["min_shm"], 72)
        self.assertEqual(result["total"], 72.0)
        self.assertEqual(result["free"], 47.0)

    def test_macos_shared_memory_reports_unavailable_usage_metrics(self) -> None:
        config = SimpleNamespace(
            birdseye=SimpleNamespace(restream=False),
            cameras={},
        )
        with (
            patch(
                "frigate.util.services.shutil.disk_usage",
                side_effect=FileNotFoundError,
            ),
            patch(
                "frigate.util.services.get_darwin_posix_shm_usage",
                return_value=None,
            ),
            patch("frigate.util.services.sys.platform", "darwin"),
        ):
            result = calculate_shm_requirements(config)

        self.assertFalse(result["metrics_available"])
        self.assertEqual(result["used"], 0.0)
        self.assertEqual(result["object_count"], 0)

    def test_vainfo_is_explicitly_unsupported_on_macos(self) -> None:
        with patch("frigate.util.services.sys.platform", "darwin"):
            result = vainfo_hwaccel()

        self.assertIsInstance(result, sp.CompletedProcess)
        self.assertEqual(result.returncode, 127)
        self.assertIn(b"unsupported", result.stderr)

    def test_process_title_extension_is_disabled_on_macos(self) -> None:
        extension = Mock()
        with (
            patch.object(process_util.sys, "platform", "darwin"),
            patch.object(process_util, "_setproctitle", extension),
        ):
            supported = process_util.set_process_title("frigate.fixture")

        self.assertFalse(supported)
        extension.assert_not_called()


if __name__ == "__main__":
    unittest.main()
