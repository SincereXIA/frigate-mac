#!/usr/bin/env python3
"""Copy non-system Mach-O dependencies into an app runtime and rewrite paths."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
from collections import deque
from pathlib import Path


def run(*arguments: str, check: bool = True) -> str:
    """Run a local build command and return its standard output."""
    result = subprocess.run(
        arguments,
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout


def is_macho(path: Path) -> bool:
    """Return whether a file is a Mach-O binary."""
    if not path.is_file() or path.is_symlink():
        return False
    if path.suffix not in {".so", ".dylib"} and not os.access(path, os.X_OK):
        return False
    return "Mach-O" in run("file", "-b", str(path), check=False)


def dependencies(path: Path) -> list[Path]:
    """Return absolute non-system dependencies for a Mach-O file."""
    output = run("otool", "-L", str(path))
    identifiers = run("otool", "-D", str(path), check=False).splitlines()[1:]
    install_identifier = identifiers[0].strip() if identifiers else None
    values = []
    for line in output.splitlines()[1:]:
        value = line.strip().split(" (", 1)[0]
        if value == install_identifier:
            continue
        if not value.startswith("/"):
            continue
        if value.startswith(("/System/", "/usr/lib/")):
            continue
        values.append(Path(value))
    return values


def digest(path: Path) -> str:
    """Return a SHA-256 digest for collision detection."""
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def add_rpath(path: Path, value: str) -> None:
    """Add a loader rpath if it is not already present."""
    current = run("otool", "-l", str(path))
    if value in current:
        return
    subprocess.run(
        ["install_name_tool", "-add_rpath", value, str(path)],
        check=True,
    )


def relocate(runtime: Path) -> None:
    """Relocate all reachable Homebrew or other third-party libraries."""
    library_directory = runtime / "lib"
    library_directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    candidates = [path for path in runtime.rglob("*") if is_macho(path)]
    queue = deque(candidates)
    visited: set[Path] = set()
    copied_sources: dict[str, Path] = {}

    while queue:
        binary = queue.popleft()
        resolved = binary.resolve()
        if resolved in visited:
            continue
        visited.add(resolved)
        for dependency in dependencies(binary):
            try:
                dependency.resolve().relative_to(runtime)
            except ValueError:
                pass
            else:
                relative_dependency = Path(
                    os.path.relpath(dependency.resolve(), binary.parent.resolve())
                )
                run(
                    "install_name_tool",
                    "-change",
                    str(dependency),
                    f"@loader_path/{relative_dependency}",
                    str(binary),
                )
                continue
            source = dependency
            if not source.is_file() and str(dependency).startswith("/DLC/"):
                wheel_suffix = Path(*dependency.parts[2:])
                matches = [
                    path
                    for path in runtime.rglob(dependency.name)
                    if str(path).endswith(str(wheel_suffix))
                ]
                if len(matches) == 1:
                    source = matches[0]
            if not source.is_file():
                raise FileNotFoundError(f"Missing Mach-O dependency: {dependency}")
            destination = library_directory / source.name
            if destination.exists():
                previous_source = copied_sources.get(source.name)
                if previous_source is None:
                    raise RuntimeError(f"Untracked Mach-O library: {destination}")
                if previous_source.resolve() != source.resolve() and digest(
                    previous_source
                ) != digest(source):
                    destination = library_directory / (
                        f"{source.stem}-{digest(source)[:12]}{source.suffix}"
                    )
            if not destination.exists():
                shutil.copy2(source, destination)
                destination.chmod(0o755)
                copied_sources[destination.name] = source
                queue.append(destination)
            run(
                "install_name_tool",
                "-change",
                str(dependency),
                f"@rpath/{destination.name}",
                str(binary),
            )

    for library in library_directory.iterdir():
        if is_macho(library):
            run(
                "install_name_tool",
                "-id",
                f"@rpath/{library.name}",
                str(library),
            )

    for executable, rpath in (
        (runtime / "bin/ffmpeg", "@executable_path/../lib"),
        (runtime / "bin/ffprobe", "@executable_path/../lib"),
        (runtime / "bin/nginx", "@executable_path/../lib"),
        (runtime / "python/bin/python3", "@executable_path/../../lib"),
    ):
        add_rpath(executable, rpath)


def main() -> None:
    """Parse arguments and relocate one runtime directory."""
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime", type=Path)
    arguments = parser.parse_args()
    relocate(arguments.runtime.resolve())


if __name__ == "__main__":
    main()
