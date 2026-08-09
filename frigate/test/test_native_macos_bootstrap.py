"""Tests for the native Frigate bootstrap."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography import x509
from ruamel.yaml import YAML

from frigate.runtime.binaries import NativeBinaries
from frigate.runtime.bootstrap import (
    cleanup_stale_ipc,
    prepare_runtime,
    safe_validation_path,
)
from frigate.runtime.config_adapter import adapt_container_paths
from frigate.runtime.configuration import (
    load_environment_file,
    validate_config_file,
)
from frigate.runtime.nginx import build_nginx_config
from frigate.runtime.paths import RuntimePaths
from frigate.runtime.tls import ensure_native_tls_certificate

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_CONFIG = REPO_ROOT / "macos" / "tests" / "fixtures" / "config.yaml"
FIXTURE_MODEL = REPO_ROOT / "macos" / "tests" / "fixtures" / "native-test-model.bin"


class TestNativeMacOSBootstrap(unittest.TestCase):
    """Validate preparation without starting external services."""

    def test_environment_file_is_not_shell_evaluated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text(
                "FRIGATE_USER=camera\n"
                "FRIGATE_PASSWORD='literal $(touch /tmp/not-executed)'\n"
            )

            variables = load_environment_file(env_file)

        self.assertEqual(variables["FRIGATE_USER"], "camera")
        self.assertEqual(
            variables["FRIGATE_PASSWORD"], "literal $(touch /tmp/not-executed)"
        )

    def test_native_binary_directory_precedes_path_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            binary = Path(temporary_directory) / "go2rtc"
            binary.touch(mode=0o700)

            binaries = NativeBinaries.discover(
                {"FRIGATE_NATIVE_BIN_DIR": temporary_directory, "PATH": ""}
            )

        self.assertEqual(binaries.go2rtc, binary)

    def test_native_app_check_does_not_mutate_signed_python_runtime(self) -> None:
        check_script = REPO_ROOT / "macos" / "scripts" / "check-native-app.sh"
        content = check_script.read_text()

        self.assertEqual(content.count("python3 -I -B -c"), 2)

    def test_bundled_native_binary_precedes_path_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            install_dir = Path(temporary_directory)
            binary = install_dir / "macos" / ".runtime" / "bin" / "nginx"
            binary.parent.mkdir(parents=True)
            binary.touch(mode=0o700)

            binaries = NativeBinaries.discover(
                {"FRIGATE_INSTALL_DIR": str(install_dir), "PATH": ""}
            )

        self.assertEqual(binaries.nginx, binary)

    def test_unsupported_environment_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text("PATH=/untrusted\n")

            with self.assertRaisesRegex(ValueError, "Unsupported"):
                load_environment_file(env_file)

    def test_fixture_config_passes_full_validation(self) -> None:
        media = REPO_ROOT / "macos" / "tests" / "fixtures" / "native-macos-h264.mp4"
        with patch.dict(
            os.environ,
            {
                "FRIGATE_INSTALL_DIR": str(REPO_ROOT),
                "FRIGATE_TEST_MEDIA": str(media),
                "FRIGATE_TEST_MODEL": str(FIXTURE_MODEL),
            },
        ):
            result = validate_config_file(FIXTURE_CONFIG)

        self.assertEqual(result.camera_count, 1)
        self.assertEqual(result.detector_types, ("zmq",))
        self.assertEqual(result.hwaccel_types, ("videotoolbox",))
        self.assertFalse(result.migrated)
        self.assertEqual(result.source_version, "0.18-0")

    def test_container_paths_are_mapped_without_mutating_source(self) -> None:
        source = {
            "model": {"labelmap_path": "/labelmap/coco-80.txt"},
            "database": {"path": "/config/frigate.db"},
            "camera": {"path": "rtsp://camera.local/live"},
        }
        paths = self._paths(Path("/tmp/native-path-map"))

        result = adapt_container_paths(source, paths)

        self.assertEqual(source["database"]["path"], "/config/frigate.db")
        self.assertEqual(
            result.config["model"]["labelmap_path"],
            "/tmp/native-path-map/install/labelmap/coco-80.txt",
        )
        self.assertEqual(
            result.config["database"]["path"],
            "/tmp/native-path-map/config/frigate.db",
        )
        self.assertEqual(result.config["camera"]["path"], "rtsp://camera.local/live")
        self.assertEqual(result.mapped_path_count, 2)

    def test_migration_refuses_config_outside_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = self._paths(Path(temporary_directory))

            with self.assertRaisesRegex(ValueError, "FRIGATE_CONFIG_DIR"):
                validate_config_file(FIXTURE_CONFIG, paths=paths, migrate_copy=True)

    def test_validation_paths_redact_user_defined_names(self) -> None:
        self.assertEqual(
            safe_validation_path(("cameras", "private-camera-name", "motion")),
            ["cameras", "<camera>", "motion"],
        )
        self.assertEqual(
            safe_validation_path(("detectors", "private-detector-name", "type")),
            ["detectors", "<detector>", "type"],
        )
        self.assertEqual(
            safe_validation_path(("mqtt", "host")),
            ["mqtt", "host"],
        )

    def test_cleanup_removes_socket_but_refuses_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = self._paths(root)
            paths.ensure_directories()
            stale_socket = paths.runtime_dir / "comms"

            def fake_lstat(path: Path):
                if path == stale_socket:
                    return SimpleNamespace(st_mode=stat.S_IFSOCK)
                raise FileNotFoundError

            with (
                patch.object(Path, "lstat", autospec=True, side_effect=fake_lstat),
                patch.object(Path, "unlink", autospec=True) as unlink,
            ):
                cleanup_stale_ipc(paths)
                unlink.assert_called_once_with(stale_socket)

            regular_file = paths.runtime_dir / "config"
            regular_file.touch()
            with self.assertRaisesRegex(RuntimeError, "non-socket"):
                cleanup_stale_ipc(paths)

    def test_native_tls_certificate_is_reused_without_replacing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = self._paths(Path(temporary_directory))

            generated = ensure_native_tls_certificate(paths)
            certificate = generated.certificate.read_bytes()
            private_key = generated.private_key.read_bytes()
            reused = ensure_native_tls_certificate(paths)

            self.assertTrue(generated.generated)
            self.assertFalse(reused.generated)
            self.assertEqual(reused.certificate.read_bytes(), certificate)
            self.assertEqual(reused.private_key.read_bytes(), private_key)

    def test_native_tls_refuses_an_incomplete_certificate_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = self._paths(Path(temporary_directory))
            paths.certificates_dir.mkdir(parents=True)
            (paths.certificates_dir / "fullchain.pem").write_text("incomplete")

            with self.assertRaisesRegex(FileNotFoundError, "requires both"):
                ensure_native_tls_certificate(paths)

    def test_native_nginx_can_explicitly_disable_tls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = self._paths(Path(temporary_directory))

            nginx_config = build_nginx_config(
                paths,
                paths.install_dir / "web" / "dist",
                tls_enabled=False,
            )

            self.assertIn("listen 0.0.0.0:8971;", nginx_config)
            self.assertNotIn("ssl_certificate", nginx_config)

    def test_prepare_writes_private_go2rtc_and_external_tls_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = self._paths(root)
            web_root = paths.install_dir / "web" / "dist"
            web_root.mkdir(parents=True)
            (web_root / "index.html").write_text("fixture")
            paths.labelmap_path.write_text(
                (REPO_ROOT / "docker/main/rootfs/labelmap/coco-80.txt").read_text()
            )
            ffmpeg = root / "bin" / "ffmpeg"
            ffmpeg.parent.mkdir()
            ffmpeg.touch(mode=0o700)
            go2rtc = root / "bin" / "go2rtc"
            go2rtc.touch(mode=0o700)
            nginx = root / "bin" / "nginx"
            nginx.touch(mode=0o700)
            binaries = NativeBinaries(ffmpeg, None, go2rtc, nginx)

            media = REPO_ROOT / "macos" / "tests" / "fixtures" / "native-macos-h264.mp4"
            with patch.dict(
                os.environ,
                {
                    "FRIGATE_TEST_MEDIA": str(media),
                    "FRIGATE_TEST_MODEL": str(FIXTURE_MODEL),
                },
            ):
                manifest = prepare_runtime(FIXTURE_CONFIG, paths, binaries)

            go2rtc_path = paths.runtime_dir / "go2rtc.yaml"
            content = go2rtc_path.read_text()
            self.assertIn("127.0.0.1:1984", content)
            self.assertIn("127.0.0.1:8554", content)
            self.assertIn("127.0.0.1:8555", content)
            self.assertEqual(stat.S_IMODE(go2rtc_path.stat().st_mode), 0o600)
            self.assertEqual(manifest["go2rtc_config"], str(go2rtc_path))
            backend_config = paths.runtime_dir / "native-config.yaml"
            self.assertEqual(manifest["backend_config"], str(backend_config))
            self.assertEqual(stat.S_IMODE(backend_config.stat().st_mode), 0o600)
            backend = YAML(typ="safe").load(backend_config.read_text())
            self.assertTrue(backend["auth"]["cookie_secure"])

            certificate_path = paths.certificates_dir / "fullchain.pem"
            private_key_path = paths.certificates_dir / "privkey.pem"
            self.assertTrue(certificate_path.is_file())
            self.assertTrue(private_key_path.is_file())
            self.assertEqual(stat.S_IMODE(paths.certificates_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(certificate_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(private_key_path.stat().st_mode), 0o600)
            certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
            self.assertEqual(certificate.issuer, certificate.subject)
            alternative_names = certificate.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value
            self.assertIn(
                "localhost",
                alternative_names.get_values_for_type(x509.DNSName),
            )
            self.assertIn(
                "127.0.0.1",
                [
                    str(address)
                    for address in alternative_names.get_values_for_type(x509.IPAddress)
                ],
            )

            nginx_path = paths.runtime_dir / "nginx.conf"
            nginx_config = nginx_path.read_text()
            self.assertIn("listen 0.0.0.0:8971 ssl", nginx_config)
            self.assertIn(f'ssl_certificate "{certificate_path}"', nginx_config)
            self.assertIn(f'ssl_certificate_key "{private_key_path}"', nginx_config)
            self.assertIn("ssl_protocols TLSv1.2 TLSv1.3", nginx_config)
            self.assertIn("upstream jsmpeg { server 127.0.0.1:8082; }", nginx_config)
            self.assertIn("location /live/jsmpeg/ {", nginx_config)
            self.assertIn("proxy_pass http://jsmpeg/;", nginx_config)
            self.assertIn("location /cache/ {", nginx_config)
            self.assertIn("internal;", nginx_config)
            self.assertIn(
                f'alias "{paths.cache_dir}/";',
                nginx_config,
            )
            self.assertIn("location /vod/", nginx_config)
            self.assertIn("vod_hls_container_format fmp4", nginx_config)
            self.assertIn(
                "sub_filter '\"/BASE_PATH/assets/' '\"/assets/'", nginx_config
            )
            self.assertIn(
                "sub_filter '<body>' '<body><script>window.baseUrl=\"/\";</script>'",
                nginx_config,
            )
            self.assertIn(str(paths.media_dir), nginx_config)
            self.assertNotIn("/media/frigate", nginx_config)
            self.assertEqual(stat.S_IMODE(nginx_path.stat().st_mode), 0o600)
            self.assertEqual(manifest["web_url"], "https://127.0.0.1:8971")
            self.assertEqual(manifest["listen"], "0.0.0.0:8971")
            self.assertTrue(manifest["tls"]["enabled"])
            self.assertTrue(manifest["tls"]["generated"])
            self.assertIn("127.0.0.1", manifest["tls"]["ip_addresses"])

            runtime_manifest = json.loads(
                (paths.runtime_dir / "native-runtime.json").read_text()
            )
            self.assertEqual(runtime_manifest["architecture"], "arm64")

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
