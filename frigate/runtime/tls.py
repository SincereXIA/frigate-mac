"""TLS certificate management for the native macOS runtime."""

import ipaddress
import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from frigate.runtime.paths import RuntimePaths


@dataclass(frozen=True, slots=True)
class NativeTlsMaterial:
    """Paths and metadata for the native HTTPS certificate."""

    certificate: Path
    private_key: Path
    generated: bool
    dns_names: tuple[str, ...]
    ip_addresses: tuple[str, ...]


def _write_private(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _network_identities() -> tuple[tuple[str, ...], tuple[str, ...]]:
    host_name = socket.gethostname().strip().lower()
    dns_names = {"localhost"}
    if host_name:
        dns_names.add(host_name)
        if "." not in host_name:
            dns_names.add(f"{host_name}.local")

    ip_addresses = {"127.0.0.1", "::1"}
    for addresses in psutil.net_if_addrs().values():
        for address in addresses:
            if address.family not in (socket.AF_INET, socket.AF_INET6):
                continue
            value = address.address.split("%", maxsplit=1)[0]
            try:
                parsed = ipaddress.ip_address(value)
            except ValueError:
                continue
            if parsed.is_unspecified or parsed.is_multicast or parsed.is_link_local:
                continue
            ip_addresses.add(str(parsed))

    return tuple(sorted(dns_names)), tuple(
        sorted(
            ip_addresses, key=lambda value: (ipaddress.ip_address(value).version, value)
        )
    )


def _validate_pair(certificate_path: Path, private_key_path: Path) -> x509.Certificate:
    try:
        certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
        private_key = serialization.load_pem_private_key(
            private_key_path.read_bytes(),
            password=None,
        )
    except (OSError, ValueError) as error:
        raise ValueError("Unable to load the native TLS certificate pair") from error

    encoding = serialization.Encoding.DER
    public_format = serialization.PublicFormat.SubjectPublicKeyInfo
    certificate_key = certificate.public_key().public_bytes(encoding, public_format)
    private_public_key = private_key.public_key().public_bytes(encoding, public_format)
    if certificate_key != private_public_key:
        raise ValueError("The native TLS certificate and private key do not match")

    return certificate


def ensure_native_tls_certificate(paths: RuntimePaths) -> NativeTlsMaterial:
    """Create or validate the persistent native HTTPS certificate pair."""
    certificate_directory = paths.config_dir / "certs"
    certificate_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    certificate_directory.chmod(0o700)
    certificate_path = certificate_directory / "fullchain.pem"
    private_key_path = certificate_directory / "privkey.pem"

    certificate_exists = certificate_path.is_file()
    private_key_exists = private_key_path.is_file()
    if certificate_exists != private_key_exists:
        raise FileNotFoundError(
            "Native TLS requires both certs/fullchain.pem and certs/privkey.pem"
        )

    if certificate_exists:
        certificate = _validate_pair(certificate_path, private_key_path)
        certificate_path.chmod(0o600)
        private_key_path.chmod(0o600)
        try:
            subject_alternative_names = certificate.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value
        except x509.ExtensionNotFound:
            dns_names: tuple[str, ...] = ()
            ip_addresses: tuple[str, ...] = ()
        else:
            dns_names = tuple(
                subject_alternative_names.get_values_for_type(x509.DNSName)
            )
            ip_addresses = tuple(
                str(address)
                for address in subject_alternative_names.get_values_for_type(
                    x509.IPAddress
                )
            )
        return NativeTlsMaterial(
            certificate_path,
            private_key_path,
            False,
            dns_names,
            ip_addresses,
        )

    dns_names, ip_addresses = _network_identities()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    common_name = next((name for name in dns_names if name != "localhost"), "localhost")
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Frigate Native macOS"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    now = datetime.now(UTC)
    alternative_names: list[x509.GeneralName] = [
        x509.DNSName(name) for name in dns_names
    ]
    alternative_names.extend(
        x509.IPAddress(ipaddress.ip_address(address)) for address in ip_addresses
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(alternative_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    _write_private(
        private_key_path,
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    _write_private(
        certificate_path, certificate.public_bytes(serialization.Encoding.PEM)
    )
    _validate_pair(certificate_path, private_key_path)

    return NativeTlsMaterial(
        certificate_path,
        private_key_path,
        True,
        dns_names,
        ip_addresses,
    )
