"""Generate a native go2rtc configuration from Frigate YAML."""

import copy
import os
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


def _substitute_stream(value: str) -> str:
    from frigate.config.env import substitute_frigate_vars

    return substitute_frigate_vars(value)


def _is_restricted(value: str) -> bool:
    from frigate.util.services import is_restricted_go2rtc_source

    return is_restricted_go2rtc_source(value)


def build_go2rtc_config(
    config: dict[str, Any], ffmpeg_path: Path, birdseye_pipe: Path
) -> dict[str, Any]:
    """Build a go2rtc config with secure native listener defaults."""
    go2rtc_config = copy.deepcopy(config.get("go2rtc", {}))

    api = go2rtc_config.setdefault("api", {})
    api.setdefault("listen", "127.0.0.1:1984")
    api.setdefault("origin", "*")

    rtsp = go2rtc_config.setdefault("rtsp", {})
    rtsp.setdefault("listen", "127.0.0.1:8554")
    if rtsp.get("username") is not None:
        rtsp["username"] = _substitute_stream(rtsp["username"])
    if rtsp.get("password") is not None:
        rtsp["password"] = _substitute_stream(rtsp["password"])

    webrtc = go2rtc_config.setdefault("webrtc", {})
    webrtc.setdefault("listen", "127.0.0.1:8555")
    webrtc.setdefault("candidates", ["stun:8555"])

    go2rtc_config.setdefault("log", {}).setdefault("format", "text")
    go2rtc_config.setdefault("ffmpeg", {}).setdefault("bin", str(ffmpeg_path))

    streams = go2rtc_config.get("streams", {})
    for name in list(streams):
        stream = streams[name]
        candidates = [stream] if isinstance(stream, str) else stream
        if not isinstance(candidates, list):
            from frigate.util.services import is_go2rtc_arbitrary_exec_allowed

            if not is_go2rtc_arbitrary_exec_allowed():
                del streams[name]
            continue

        filtered = []
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            substituted = _substitute_stream(candidate)
            if not _is_restricted(substituted):
                filtered.append(substituted)

        if not filtered:
            del streams[name]
        elif isinstance(stream, str):
            streams[name] = filtered[0]
        else:
            streams[name] = filtered

    if config.get("birdseye", {}).get("restream", False):
        from frigate.ffmpeg_presets import parse_preset_hardware_acceleration_encode

        birdseye = config["birdseye"]
        input_args = (
            "-f rawvideo -pix_fmt yuv420p "
            f"-video_size {birdseye.get('width', 1280)}x"
            f"{birdseye.get('height', 720)} -r 10 -i {birdseye_pipe}"
        )
        output_args = "-rtsp_transport tcp -f rtsp {output}"
        command = parse_preset_hardware_acceleration_encode(
            str(ffmpeg_path),
            config.get("ffmpeg", {}).get("hwaccel_args", ""),
            input_args,
            output_args,
        )
        streams["birdseye"] = f"exec:{command}"

    return go2rtc_config


def write_private_yaml(config: dict[str, Any], path: Path) -> None:
    """Atomically write YAML with permissions restricted to the current user."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)

    try:
        with temporary_path.open("w") as config_file:
            yaml.dump(config, config_file)
        temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
