"""Safe configuration loading for the native bootstrap."""

import logging
import os
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from frigate.runtime.config_adapter import adapt_container_paths
from frigate.runtime.paths import RuntimePaths

_ENVIRONMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_ENVIRONMENT_KEYS = {"PLUS_API_KEY"}


def load_environment_file(path: Path) -> dict[str, str]:
    """Load Frigate variables from a dotenv file without shell evaluation."""
    variables: dict[str, str] = {}

    for line_number, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()

        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _ENVIRONMENT_KEY.fullmatch(key):
            raise ValueError(f"Invalid environment entry on line {line_number}")
        if not (key.startswith("FRIGATE_") or key in _ALLOWED_ENVIRONMENT_KEYS):
            raise ValueError(
                f"Unsupported environment key on line {line_number}: {key}"
            )

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if "\0" in value:
            raise ValueError(f"Null byte in environment value on line {line_number}")
        variables[key] = value

    return variables


def apply_environment(variables: dict[str, str]) -> None:
    """Apply loaded variables before importing the Frigate config model."""
    os.environ.update(variables)

    config_environment = sys.modules.get("frigate.config.env")
    if config_environment is None:
        return

    config_environment.FRIGATE_ENV_VARS.update(
        {key: value for key, value in variables.items() if key.startswith("FRIGATE_")}
    )


def load_raw_config(path: Path) -> dict[str, Any]:
    """Load a YAML configuration mapping without mutating the source file."""
    yaml = YAML(typ="safe")
    with path.open() as config_file:
        config = yaml.load(config_file)

    if config is None:
        return {}
    if not isinstance(config, dict):
        raise ValueError("Frigate configuration must be a mapping")
    return config


@dataclass(frozen=True, slots=True)
class ConfigValidationResult:
    """Diagnostics-safe summary of a validated Frigate configuration."""

    version: str
    camera_count: int
    enabled_camera_count: int
    detector_types: tuple[str, ...]
    hwaccel_types: tuple[str, ...]
    mapped_path_count: int
    migrated: bool
    source_version: str

    def as_dict(self) -> dict[str, str | int | bool | tuple[str, ...]]:
        """Return the result as JSON-serializable values."""
        return {
            "version": self.version,
            "camera_count": self.camera_count,
            "enabled_camera_count": self.enabled_camera_count,
            "detector_types": self.detector_types,
            "hwaccel_types": self.hwaccel_types,
            "mapped_path_count": self.mapped_path_count,
            "migrated": self.migrated,
            "source_version": self.source_version,
        }


def validate_config_file(
    path: Path,
    environment_file: Path | None = None,
    paths: RuntimePaths | None = None,
    migrate_copy: bool = False,
) -> ConfigValidationResult:
    """Fully validate a config, optionally migrating a guarded working copy."""
    apply_environment(
        {key: value for key, value in os.environ.items() if key.startswith("FRIGATE_")}
    )
    if environment_file:
        apply_environment(load_environment_file(environment_file))

    runtime_paths = RuntimePaths.from_environment() if paths is None else paths
    source_config = load_raw_config(path)
    source_version = str(source_config.get("version", "0.13"))

    if migrate_copy:
        resolved_config = path.resolve()
        resolved_config_dir = runtime_paths.config_dir.resolve()
        if not resolved_config.is_relative_to(resolved_config_dir):
            raise ValueError(
                "Config migration is allowed only inside FRIGATE_CONFIG_DIR"
            )

        previous_logging_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                from frigate.util.config import migrate_frigate_config

                migrate_frigate_config(str(path))
        finally:
            logging.disable(previous_logging_disable)

    adaptation = adapt_container_paths(load_raw_config(path), runtime_paths)
    yaml = YAML()
    adapted_config = StringIO()
    yaml.dump(adaptation.config, adapted_config)
    adapted_config.seek(0)

    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            from frigate.config import FrigateConfig

            config = FrigateConfig.parse(adapted_config, install=True)
    finally:
        logging.disable(previous_logging_disable)

    detector_types = tuple(
        sorted(
            {
                str(
                    detector.type.value
                    if hasattr(detector.type, "value")
                    else detector.type
                )
                for detector in config.detectors.values()
            }
        )
    )
    hwaccel_types = set()
    for camera in config.cameras.values():
        value = camera.ffmpeg.hwaccel_args
        normalized = " ".join(value) if isinstance(value, list) else value
        if "videotoolbox" in normalized:
            hwaccel_types.add("videotoolbox")
        elif "vaapi" in normalized:
            hwaccel_types.add("vaapi")
        elif "nvidia" in normalized or "cuda" in normalized:
            hwaccel_types.add("nvidia")
        elif normalized:
            hwaccel_types.add("custom")
        else:
            hwaccel_types.add("none")
    return ConfigValidationResult(
        version=str(config.version),
        camera_count=len(config.cameras),
        enabled_camera_count=sum(
            camera.enabled_in_config for camera in config.cameras.values()
        ),
        detector_types=detector_types,
        hwaccel_types=tuple(sorted(hwaccel_types)),
        mapped_path_count=adaptation.mapped_path_count,
        migrated=migrate_copy and source_version != str(config.version),
        source_version=source_version,
    )
