"""Translate known container paths for the native Frigate runtime."""

import copy
from dataclasses import dataclass
from typing import Any

from frigate.runtime.paths import RuntimePaths


@dataclass(frozen=True, slots=True)
class ConfigAdaptationResult:
    """An adapted config and the number of path values translated."""

    config: dict[str, Any]
    mapped_path_count: int


def adapt_container_paths(
    config: dict[str, Any], paths: RuntimePaths
) -> ConfigAdaptationResult:
    """Map exact container path prefixes without changing the source object."""
    mappings = (
        ("/media/frigate", str(paths.media_dir)),
        ("/tmp/cache", str(paths.cache_dir)),
        ("/opt/frigate", str(paths.install_dir)),
        ("/labelmap", str(paths.labelmap_dir)),
        ("/config", str(paths.config_dir)),
    )
    mapped_path_count = 0

    def adapt(value: Any) -> Any:
        nonlocal mapped_path_count

        if isinstance(value, dict):
            return {key: adapt(item) for key, item in value.items()}
        if isinstance(value, list):
            return [adapt(item) for item in value]
        if not isinstance(value, str):
            return value

        for container_prefix, native_prefix in mappings:
            if value == container_prefix or value.startswith(f"{container_prefix}/"):
                mapped_path_count += 1
                return f"{native_prefix}{value[len(container_prefix) :]}"
        return value

    return ConfigAdaptationResult(adapt(copy.deepcopy(config)), mapped_path_count)
