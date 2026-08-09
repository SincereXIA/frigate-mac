"""Command-line bootstrap for a native Frigate development runtime."""

import argparse
import json
import os
import platform
import stat
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from frigate.runtime.binaries import NativeBinaries
from frigate.runtime.config_adapter import adapt_container_paths
from frigate.runtime.configuration import (
    apply_environment,
    load_environment_file,
    load_raw_config,
    validate_config_file,
)
from frigate.runtime.go2rtc import build_go2rtc_config, write_private_yaml
from frigate.runtime.nginx import build_nginx_config, write_native_nginx_files
from frigate.runtime.paths import RuntimePaths
from frigate.runtime.supervisor import NativeProcessSupervisor, build_process_specs
from frigate.runtime.tls import ensure_native_tls_certificate

_IPC_ENDPOINT_NAMES = (
    "config",
    "proxy_pub",
    "proxy_sub",
    "embeddings",
    "detector_pub",
    "detector_sub",
    "comms",
    "zmq_detector",
)

_NAMED_CONFIG_SECTIONS = {
    "cameras": "camera",
    "camera_groups": "camera_group",
    "detectors": "detector",
    "profiles": "profile",
}


def safe_validation_path(location: tuple[Any, ...]) -> list[str]:
    """Redact user-defined config object names from an error location."""
    parts = [str(part) for part in location]
    if len(parts) > 1 and parts[0] in _NAMED_CONFIG_SECTIONS:
        parts[1] = f"<{_NAMED_CONFIG_SECTIONS[parts[0]]}>"
    return parts


def cleanup_stale_ipc(paths: RuntimePaths) -> None:
    """Remove only known stale socket nodes from the runtime directory."""
    for name in _IPC_ENDPOINT_NAMES:
        socket_path = paths.runtime_dir / name
        try:
            mode = socket_path.lstat().st_mode
        except FileNotFoundError:
            continue

        if not stat.S_ISSOCK(mode):
            raise RuntimeError(f"Refusing to remove non-socket IPC path: {socket_path}")
        socket_path.unlink()


def _write_private_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def prepare_runtime(
    config_path: Path,
    paths: RuntimePaths,
    binaries: NativeBinaries,
    environment_file: Path | None = None,
) -> dict[str, Any]:
    """Prepare directories and generated configs for native process startup."""
    if environment_file:
        apply_environment(load_environment_file(environment_file))

    runtime_environment = {
        key: value for key, value in os.environ.items() if key.startswith("FRIGATE_")
    }
    runtime_environment.update(paths.as_environment())
    apply_environment(runtime_environment)

    paths.ensure_directories(
        [
            paths.model_cache_dir,
            paths.clips_dir,
            paths.exports_dir,
            paths.recordings_dir,
            paths.thumbnails_dir,
        ]
    )
    cleanup_stale_ipc(paths)

    required_binaries = {
        "FFmpeg": binaries.ffmpeg,
        "go2rtc": binaries.go2rtc,
        "nginx": binaries.nginx,
    }
    for name, binary in required_binaries.items():
        if not binary or not binary.is_file():
            raise FileNotFoundError(f"A native {name} executable is required")

    raw_config = load_raw_config(config_path)
    validation = validate_config_file(config_path, paths=paths)
    adaptation = adapt_container_paths(raw_config, paths)
    tls_config = raw_config.get("tls") or {}
    tls_enabled = bool(tls_config.get("enabled", True))
    if tls_enabled:
        auth_config = adaptation.config.get("auth")
        if not isinstance(auth_config, dict):
            auth_config = {}
            adaptation.config["auth"] = auth_config
        auth_config.setdefault("cookie_secure", True)
    backend_config_path = paths.runtime_dir / "native-config.yaml"
    write_private_yaml(adaptation.config, backend_config_path)
    go2rtc_config = build_go2rtc_config(
        raw_config, binaries.ffmpeg, paths.birdseye_pipe
    )
    go2rtc_path = paths.runtime_dir / "go2rtc.yaml"
    write_private_yaml(go2rtc_config, go2rtc_path)

    web_root = paths.install_dir / "web" / "dist"
    if not (web_root / "index.html").is_file():
        raise FileNotFoundError("The native web build is missing; run npm run build")
    try:
        native_port = int(os.environ.get("FRIGATE_NATIVE_PORT", "8971"))
    except ValueError as error:
        raise ValueError("FRIGATE_NATIVE_PORT must be an integer") from error
    tls_material = ensure_native_tls_certificate(paths) if tls_enabled else None
    nginx_config = build_nginx_config(
        paths,
        web_root,
        listen_port=native_port,
        listen_host="0.0.0.0",
        tls_certificate=tls_material.certificate if tls_material else None,
        tls_private_key=tls_material.private_key if tls_material else None,
        tls_enabled=tls_enabled,
    )
    nginx_path = write_native_nginx_files(nginx_config, paths)

    homekit_path = paths.config_dir / "go2rtc_homekit.yml"
    if not homekit_path.exists():
        homekit_path.touch(mode=0o600)

    manifest = {
        "architecture": platform.machine(),
        "config": str(config_path),
        "backend_config": str(backend_config_path),
        "config_validation": validation.as_dict(),
        "go2rtc_config": str(go2rtc_path),
        "nginx_config": str(nginx_path),
        "web_url": f"{'https' if tls_enabled else 'http'}://127.0.0.1:{native_port}",
        "listen": f"0.0.0.0:{native_port}",
        "tls": {
            "enabled": tls_enabled,
            "certificate": str(tls_material.certificate) if tls_material else None,
            "generated": tls_material.generated if tls_material else False,
            "dns_names": list(tls_material.dns_names) if tls_material else [],
            "ip_addresses": list(tls_material.ip_addresses) if tls_material else [],
        },
        "paths": paths.as_environment(),
        "binaries": binaries.status(),
    }
    _write_private_json(manifest, paths.runtime_dir / "native-runtime.json")
    return manifest


def doctor(paths: RuntimePaths, binaries: NativeBinaries) -> dict[str, Any]:
    """Return a diagnostics-safe native runtime readiness report."""
    return {
        "platform": sys.platform,
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "paths": paths.as_environment(),
        "binaries": binaries.status(),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="frigate-native")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Report native runtime readiness")

    validate = subparsers.add_parser(
        "validate-config", help="Validate a Frigate config without modifying it"
    )
    validate.add_argument("--config", type=Path, required=True)
    validate.add_argument("--env-file", type=Path)
    validate.add_argument(
        "--migrate-copy",
        action="store_true",
        help="Migrate only when the config is inside FRIGATE_CONFIG_DIR",
    )

    prepare = subparsers.add_parser(
        "prepare", help="Prepare directories and generated service configs"
    )
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--env-file", type=Path)

    run = subparsers.add_parser(
        "run", help="Prepare and supervise the native Frigate services"
    )
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--env-file", type=Path)
    run.add_argument("--smoke-test-seconds", type=float)

    return parser


def main() -> int:
    """Run the native bootstrap command-line interface."""
    args = _build_parser().parse_args()
    install_dir = Path(__file__).resolve().parents[2]
    native_defaults = RuntimePaths.native_defaults(install_dir).as_environment()
    development_labelmap_dir = install_dir / "docker" / "main" / "rootfs" / "labelmap"
    if development_labelmap_dir.is_dir():
        native_defaults["FRIGATE_LABELMAP_DIR"] = str(development_labelmap_dir)
    development_bin_dir = install_dir / "macos" / ".runtime" / "bin"
    if development_bin_dir.is_dir():
        os.environ.setdefault("FRIGATE_NATIVE_BIN_DIR", str(development_bin_dir))
    native_defaults.update(
        {key: value for key, value in os.environ.items() if key.startswith("FRIGATE_")}
    )
    paths = RuntimePaths.from_environment(native_defaults)
    os.environ.update(paths.as_environment())
    os.environ.setdefault("HF_HOME", str(paths.cache_dir / "huggingface"))
    binaries = NativeBinaries.discover()

    try:
        if args.command == "doctor":
            result = doctor(paths, binaries)
        elif args.command == "validate-config":
            result = validate_config_file(
                args.config,
                args.env_file,
                paths,
                migrate_copy=args.migrate_copy,
            ).as_dict()
        elif args.command == "prepare":
            result = prepare_runtime(args.config, paths, binaries, args.env_file)
        else:
            manifest = prepare_runtime(args.config, paths, binaries, args.env_file)
            specs = build_process_specs(paths, binaries, manifest)
            result = NativeProcessSupervisor(paths, specs).run(args.smoke_test_seconds)
    except ValidationError as error:
        safe_errors = [
            {
                "path": safe_validation_path(item["loc"]),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in error.errors()
        ]
        print(json.dumps({"valid": False, "errors": safe_errors}, indent=2))
        return 2
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(json.dumps({"success": False, "error": str(error)}, indent=2))
        return 1

    print(json.dumps({"success": True, "result": result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
