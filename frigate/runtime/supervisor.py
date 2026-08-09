"""Native process supervision for Frigate development and app packaging."""

import os
import signal
import socket
import subprocess as sp
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from frigate.runtime.binaries import NativeBinaries
from frigate.runtime.paths import RuntimePaths


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """Describe one supervised native process."""

    name: str
    command: tuple[str, ...]
    readiness_port: int | None
    environment: dict[str, str]
    working_directory: Path
    readiness_path: Path | None = None


class NativeProcessSupervisor:
    """Start native services in order and stop them without orphaning children."""

    def __init__(
        self,
        paths: RuntimePaths,
        specs: Sequence[ProcessSpec],
        *,
        readiness_timeout: float = 120.0,
        shutdown_timeout: float = 20.0,
    ) -> None:
        self.paths = paths
        self.specs = tuple(specs)
        self.readiness_timeout = readiness_timeout
        self.shutdown_timeout = shutdown_timeout
        self.processes: dict[str, sp.Popen[bytes]] = {}
        self.log_files: dict[str, BinaryIO] = {}
        self.stop_event = threading.Event()

    @staticmethod
    def _port_is_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            return False

    def _wait_until_ready(self, spec: ProcessSpec, process: sp.Popen[bytes]) -> None:
        deadline = time.monotonic() + self.readiness_timeout
        while time.monotonic() < deadline:
            return_code = process.poll()
            if return_code is not None:
                raise RuntimeError(
                    f"Native service {spec.name} exited before readiness "
                    f"with code {return_code}"
                )
            if spec.readiness_port is not None and self._port_is_open(
                spec.readiness_port
            ):
                return
            if spec.readiness_path is not None and spec.readiness_path.exists():
                return
            time.sleep(0.2)

        raise RuntimeError(
            f"Native service {spec.name} did not become ready within "
            f"{self.readiness_timeout:g} seconds"
        )

    def _open_log(self, name: str) -> BinaryIO:
        log_directory = self.paths.log_dir / name
        self.paths.ensure_directories([log_directory])
        log_path = log_directory / "current"
        descriptor = os.open(log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        return os.fdopen(descriptor, "ab", buffering=0)

    def start(self) -> None:
        """Start each service after checking its loopback port is available."""
        occupied = [
            spec.readiness_port
            for spec in self.specs
            if spec.readiness_port is not None
            and self._port_is_open(spec.readiness_port)
        ]
        if occupied:
            ports = ", ".join(str(port) for port in occupied)
            raise RuntimeError(f"Native readiness ports are already in use: {ports}")

        for spec in self.specs:
            log_file = self._open_log(spec.name)
            self.log_files[spec.name] = log_file
            process = sp.Popen(
                spec.command,
                cwd=spec.working_directory,
                env=spec.environment,
                stdin=sp.DEVNULL,
                stdout=log_file,
                stderr=sp.STDOUT,
                start_new_session=True,
            )
            self.processes[spec.name] = process
            self._wait_until_ready(spec, process)

    def _check_processes(self) -> None:
        for name, process in self.processes.items():
            return_code = process.poll()
            if return_code is not None:
                raise RuntimeError(
                    f"Native service {name} exited unexpectedly with code {return_code}"
                )

    def run(self, smoke_test_seconds: float | None = None) -> dict[str, object]:
        """Run until signaled, a process exits, or the smoke-test deadline passes."""
        if smoke_test_seconds is not None and smoke_test_seconds <= 0:
            raise ValueError("Smoke-test duration must be greater than zero")

        previous_handlers: dict[int, signal.Handlers] = {}

        def request_stop(_signal: int, _frame: object) -> None:
            self.stop_event.set()

        if threading.current_thread() is threading.main_thread():
            for signal_number in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[signal_number] = signal.getsignal(signal_number)
                signal.signal(signal_number, request_stop)

        started_at = time.monotonic()
        try:
            self.start()
            deadline = (
                started_at + smoke_test_seconds
                if smoke_test_seconds is not None
                else None
            )
            while not self.stop_event.wait(0.25):
                self._check_processes()
                if deadline is not None and time.monotonic() >= deadline:
                    break
        finally:
            self.stop()
            for signal_number, handler in previous_handlers.items():
                signal.signal(signal_number, handler)

        return {
            "services": tuple(spec.name for spec in self.specs),
            "runtime_seconds": round(time.monotonic() - started_at, 2),
            "clean_shutdown": all(
                process.poll() is not None for process in self.processes.values()
            ),
        }

    def stop(self) -> None:
        """Request graceful shutdown, then kill only remaining process groups."""
        for process in reversed(tuple(self.processes.values())):
            if process.poll() is None:
                process.terminate()

        deadline = time.monotonic() + self.shutdown_timeout
        for process in reversed(tuple(self.processes.values())):
            remaining = deadline - time.monotonic()
            if process.poll() is not None or remaining <= 0:
                continue
            try:
                process.wait(timeout=remaining)
            except sp.TimeoutExpired:
                pass

        for process in reversed(tuple(self.processes.values())):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)

        for log_file in self.log_files.values():
            log_file.close()


def build_process_specs(
    paths: RuntimePaths,
    binaries: NativeBinaries,
    manifest: dict[str, object],
) -> tuple[ProcessSpec, ...]:
    """Build the standard go2rtc, Frigate, and nginx process sequence."""
    if not binaries.go2rtc or not binaries.nginx:
        raise FileNotFoundError("Native go2rtc and nginx executables are required")

    backend_config = str(manifest["backend_config"])
    go2rtc_config = str(manifest["go2rtc_config"])
    nginx_config = str(manifest["nginx_config"])
    environment = os.environ.copy()
    environment.update(paths.as_environment())
    environment.update(
        {
            "CONFIG_FILE": backend_config,
            "HF_HOME": str(paths.cache_dir / "huggingface"),
            "PYTHONUNBUFFERED": "1",
        }
    )

    common = {
        "environment": environment,
        "working_directory": paths.install_dir,
    }
    specs: list[ProcessSpec] = []
    if os.environ.get("FRIGATE_NATIVE_TEST_DETECTOR") == "1":
        detector_fixture = (
            paths.install_dir / "macos" / "tests" / "zmq_detector_fixture.py"
        )
        specs.append(
            ProcessSpec(
                "test_detector",
                (sys.executable, str(detector_fixture)),
                None,
                environment,
                paths.install_dir,
                readiness_path=paths.runtime_dir / "zmq_detector",
            )
        )

    specs.extend(
        (
            ProcessSpec(
                "go2rtc",
                (
                    str(binaries.go2rtc),
                    f"-config={paths.config_dir / 'go2rtc_homekit.yml'}",
                    f"-config={go2rtc_config}",
                ),
                1984,
                **common,
            ),
            ProcessSpec(
                "frigate",
                (sys.executable, "-m", "frigate"),
                5001,
                **common,
            ),
            ProcessSpec(
                "nginx",
                (
                    str(binaries.nginx),
                    "-c",
                    nginx_config,
                    "-p",
                    str(paths.runtime_dir),
                ),
                int(os.environ.get("FRIGATE_NATIVE_PORT", "8971")),
                **common,
            ),
        )
    )
    return tuple(specs)
