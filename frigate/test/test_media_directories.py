"""Tests for safe recording directory cleanup."""

import tempfile
import unittest
from pathlib import Path

from frigate.util.media import remove_empty_directories


class TestRemoveEmptyDirectories(unittest.TestCase):
    """Ensure directory cleanup stays inside the configured root."""

    def test_cleanup_never_ascends_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            root = base / "recordings"
            nested = root / "day" / "hour" / "camera"
            outside = base / "legacy" / "recordings"
            nested.mkdir(parents=True)
            outside.mkdir(parents=True)

            remove_empty_directories(root, {nested, outside, Path("/")})

            self.assertTrue(root.is_dir())
            self.assertFalse((root / "day").exists())
            self.assertTrue(outside.is_dir())


if __name__ == "__main__":
    unittest.main()
