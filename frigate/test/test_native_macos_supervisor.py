"""Tests for the native Frigate process supervisor."""

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

from frigate.runtime.paths import RuntimePaths
from frigate.runtime.supervisor import NativeProcessSupervisor, ProcessSpec


class TestNativeMacOSSupervisor(unittest.TestCase):
    """Verify readiness, occupied-port rejection, and graceful cleanup."""

    def test_smoke_test_starts_and_stops_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = self._paths(root)
            port = self._free_port()
            command = (
                sys.executable,
                "-c",
                "import http.server; "
                f"http.server.ThreadingHTTPServer(('127.0.0.1', {port}), "
                "http.server.SimpleHTTPRequestHandler).serve_forever()",
            )
            spec = ProcessSpec("fixture", command, port, os.environ.copy(), root)
            supervisor = NativeProcessSupervisor(
                paths,
                [spec],
                readiness_timeout=30,
                shutdown_timeout=5,
            )

            result = supervisor.run(smoke_test_seconds=0.5)

            self.assertTrue(result["clean_shutdown"])
            self.assertIsNotNone(supervisor.processes["fixture"].poll())
            self.assertTrue((paths.log_dir / "fixture" / "current").is_file())

    def test_occupied_readiness_port_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = self._paths(root)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                port = listener.getsockname()[1]
                spec = ProcessSpec(
                    "fixture",
                    (sys.executable, "-c", "pass"),
                    port,
                    os.environ.copy(),
                    root,
                )
                supervisor = NativeProcessSupervisor(paths, [spec])

                with self.assertRaisesRegex(RuntimeError, "already in use"):
                    supervisor.run(smoke_test_seconds=0.5)

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]

    @staticmethod
    def _paths(root: Path) -> RuntimePaths:
        return RuntimePaths.from_environment(
            {
                "FRIGATE_INSTALL_DIR": str(root / "install"),
                "FRIGATE_CONFIG_DIR": str(root / "config"),
                "FRIGATE_MEDIA_DIR": str(root / "media"),
                "FRIGATE_CACHE_DIR": str(root / "cache"),
                "FRIGATE_LOG_DIR": str(root / "logs"),
                "FRIGATE_RUNTIME_DIR": str(root / "runtime"),
            }
        )


if __name__ == "__main__":
    unittest.main()
