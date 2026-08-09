"""Discovery of native executable dependencies."""

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def _resolve_binary(
    environment: Mapping[str, str], variable: str, name: str
) -> Path | None:
    override = environment.get(variable)
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_dir():
            candidate /= name
        return candidate

    native_bin_dir = environment.get("FRIGATE_NATIVE_BIN_DIR")
    if native_bin_dir:
        candidate = Path(native_bin_dir).expanduser() / name
        if candidate.exists():
            return candidate

    install_dir = environment.get("FRIGATE_INSTALL_DIR")
    if install_dir:
        candidate = Path(install_dir).expanduser() / "macos" / ".runtime" / "bin" / name
        if candidate.exists():
            return candidate

    discovered = shutil.which(name)
    if discovered:
        return Path(discovered)

    homebrew_candidate = Path("/opt/homebrew/bin") / name
    return homebrew_candidate if homebrew_candidate.exists() else None


@dataclass(frozen=True, slots=True)
class NativeBinaries:
    """Executable paths used by the native runtime."""

    ffmpeg: Path | None
    ffprobe: Path | None
    go2rtc: Path | None
    nginx: Path | None

    @classmethod
    def discover(
        cls, environment: Mapping[str, str] | None = None
    ) -> "NativeBinaries":
        """Discover bundled, overridden, or PATH-provided executables."""
        values = os.environ if environment is None else environment
        return cls(
            ffmpeg=_resolve_binary(values, "FRIGATE_FFMPEG_PATH", "ffmpeg"),
            ffprobe=_resolve_binary(values, "FRIGATE_FFPROBE_PATH", "ffprobe"),
            go2rtc=_resolve_binary(values, "FRIGATE_GO2RTC_PATH", "go2rtc"),
            nginx=_resolve_binary(values, "FRIGATE_NGINX_PATH", "nginx"),
        )

    def status(self) -> dict[str, dict[str, str | bool | None]]:
        """Return a diagnostics-safe executable availability summary."""
        return {
            name: {
                "path": str(path) if path else None,
                "available": bool(path and path.is_file() and os.access(path, os.X_OK)),
            }
            for name, path in (
                ("ffmpeg", self.ffmpeg),
                ("ffprobe", self.ffprobe),
                ("go2rtc", self.go2rtc),
                ("nginx", self.nginx),
            )
        }
