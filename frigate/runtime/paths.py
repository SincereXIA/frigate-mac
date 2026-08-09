"""Validated filesystem paths for Docker and native Frigate runtimes."""

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INSTALL_DIR = "/opt/frigate"
DEFAULT_CONFIG_DIR = "/config"
DEFAULT_MEDIA_DIR = "/media/frigate"
DEFAULT_CACHE_DIR = "/tmp/cache"
DEFAULT_LOG_DIR = "/dev/shm/logs"
DEFAULT_RUNTIME_DIR = "/tmp/cache"
DEFAULT_LABELMAP_PATH = "/labelmap.txt"
DEFAULT_AUDIO_LABELMAP_PATH = "/audio-labelmap.txt"
DEFAULT_LABELMAP_DIR = "/labelmap"

_IPC_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_IPC_PATH_MAX_BYTES = 103


def _environment_path(environment: Mapping[str, str], name: str, default: str) -> Path:
    value = environment.get(name, default)
    if not value:
        raise ValueError(f"{name} must not be empty")
    if "\0" in value:
        raise ValueError(f"{name} must not contain null bytes")

    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path: {value}")

    return path


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Filesystem layout shared by Docker and native Frigate runtimes."""

    install_dir: Path
    config_dir: Path
    media_dir: Path
    cache_dir: Path
    log_dir: Path
    runtime_dir: Path
    labelmap_path: Path
    audio_labelmap_path: Path
    labelmap_dir: Path

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "RuntimePaths":
        """Create a runtime layout from environment overrides."""
        values = os.environ if environment is None else environment
        install_dir = _environment_path(
            values, "FRIGATE_INSTALL_DIR", DEFAULT_INSTALL_DIR
        )
        default_labelmap = (
            Path(DEFAULT_LABELMAP_PATH)
            if install_dir == Path(DEFAULT_INSTALL_DIR)
            else install_dir / "labelmap.txt"
        )
        default_audio_labelmap = (
            Path(DEFAULT_AUDIO_LABELMAP_PATH)
            if install_dir == Path(DEFAULT_INSTALL_DIR)
            else install_dir / "audio-labelmap.txt"
        )
        default_labelmap_dir = (
            Path(DEFAULT_LABELMAP_DIR)
            if install_dir == Path(DEFAULT_INSTALL_DIR)
            else install_dir / "labelmap"
        )
        return cls(
            install_dir=install_dir,
            config_dir=_environment_path(
                values, "FRIGATE_CONFIG_DIR", DEFAULT_CONFIG_DIR
            ),
            media_dir=_environment_path(values, "FRIGATE_MEDIA_DIR", DEFAULT_MEDIA_DIR),
            cache_dir=_environment_path(values, "FRIGATE_CACHE_DIR", DEFAULT_CACHE_DIR),
            log_dir=_environment_path(values, "FRIGATE_LOG_DIR", DEFAULT_LOG_DIR),
            runtime_dir=_environment_path(
                values, "FRIGATE_RUNTIME_DIR", DEFAULT_RUNTIME_DIR
            ),
            labelmap_path=_environment_path(
                values, "FRIGATE_LABELMAP_PATH", str(default_labelmap)
            ),
            audio_labelmap_path=_environment_path(
                values,
                "FRIGATE_AUDIO_LABELMAP_PATH",
                str(default_audio_labelmap),
            ),
            labelmap_dir=_environment_path(
                values, "FRIGATE_LABELMAP_DIR", str(default_labelmap_dir)
            ),
        )

    @property
    def database(self) -> Path:
        """Return the default SQLite database path."""
        return self.config_dir / "frigate.db"

    @property
    def model_cache_dir(self) -> Path:
        """Return the downloaded model cache directory."""
        return self.config_dir / "model_cache"

    @property
    def certificates_dir(self) -> Path:
        """Return the persistent native TLS certificate directory."""
        return self.config_dir / "certs"

    @property
    def clips_dir(self) -> Path:
        """Return the clips directory."""
        return self.media_dir / "clips"

    @property
    def exports_dir(self) -> Path:
        """Return the exports directory."""
        return self.media_dir / "exports"

    @property
    def faces_dir(self) -> Path:
        """Return the face training directory."""
        return self.clips_dir / "faces"

    @property
    def thumbnails_dir(self) -> Path:
        """Return the thumbnail directory."""
        return self.clips_dir / "thumbs"

    @property
    def recordings_dir(self) -> Path:
        """Return the recordings directory."""
        return self.media_dir / "recordings"

    @property
    def triggers_dir(self) -> Path:
        """Return the semantic trigger directory."""
        return self.clips_dir / "triggers"

    @property
    def replay_dir(self) -> Path:
        """Return the debug replay directory."""
        return self.clips_dir / "replay"

    @property
    def birdseye_pipe(self) -> Path:
        """Return the local Birdseye FIFO path."""
        return self.runtime_dir / "birdseye"

    def ipc_endpoint(self, name: str) -> str:
        """Return a validated ZeroMQ IPC endpoint in the runtime directory."""
        if not _IPC_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Invalid IPC endpoint name: {name}")

        socket_path = self.runtime_dir / name
        if len(os.fsencode(socket_path)) > _IPC_PATH_MAX_BYTES:
            raise ValueError(
                f"IPC socket path exceeds {_IPC_PATH_MAX_BYTES} bytes: {socket_path}"
            )

        return f"ipc://{socket_path}"

    def ensure_directories(
        self, additional_directories: Iterable[str | os.PathLike[str]] = ()
    ) -> None:
        """Create runtime directories with private permissions when absent."""
        directories = (
            self.config_dir,
            self.media_dir,
            self.cache_dir,
            self.log_dir,
            self.runtime_dir,
            *[Path(directory) for directory in additional_directories],
        )

        for directory in dict.fromkeys(directories):
            existed = directory.exists()
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not directory.is_dir():
                raise NotADirectoryError(directory)
            if not existed:
                directory.chmod(0o700)

    def as_environment(self) -> dict[str, str]:
        """Return environment overrides suitable for a supervised process."""
        return {
            "FRIGATE_INSTALL_DIR": str(self.install_dir),
            "FRIGATE_CONFIG_DIR": str(self.config_dir),
            "FRIGATE_MEDIA_DIR": str(self.media_dir),
            "FRIGATE_CACHE_DIR": str(self.cache_dir),
            "FRIGATE_LOG_DIR": str(self.log_dir),
            "FRIGATE_RUNTIME_DIR": str(self.runtime_dir),
            "FRIGATE_LABELMAP_PATH": str(self.labelmap_path),
            "FRIGATE_AUDIO_LABELMAP_PATH": str(self.audio_labelmap_path),
            "FRIGATE_LABELMAP_DIR": str(self.labelmap_dir),
        }

    @classmethod
    def native_defaults(
        cls,
        install_dir: Path,
        home: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> "RuntimePaths":
        """Create the default per-user layout for a native macOS process."""
        user_home = Path.home() if home is None else home
        values = {
            "FRIGATE_INSTALL_DIR": str(install_dir),
            "FRIGATE_CONFIG_DIR": str(
                user_home / "Library" / "Application Support" / "Frigate" / "config"
            ),
            "FRIGATE_MEDIA_DIR": str(
                user_home / "Library" / "Application Support" / "Frigate" / "media"
            ),
            "FRIGATE_CACHE_DIR": str(
                user_home / "Library" / "Caches" / "Frigate" / "cache"
            ),
            "FRIGATE_LOG_DIR": str(user_home / "Library" / "Logs" / "Frigate"),
            "FRIGATE_RUNTIME_DIR": str(
                user_home / "Library" / "Caches" / "Frigate" / "runtime"
            ),
        }
        if environment:
            values.update(environment)
        return cls.from_environment(values)
