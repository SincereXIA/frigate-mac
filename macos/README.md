# Frigate for macOS

This directory contains the native Apple Silicon runtime, packaging metadata,
test fixtures, and eventually the Swift supervisor.

## Baseline

`support.json` records the pinned upstream commit, the native support matrix,
and a redacted structural inventory of the container configuration used for
compatibility testing. It intentionally contains no camera names, network
addresses, account names, passwords, tokens, or API keys.

`patches.json` is the machine-readable macOS patch manifest. Every patch must
identify its phase, owner, affected paths, state, and acceptance tests. Update
the entry in the same change that implements or revises a platform patch.

Run the phase 0 checks on an Apple Silicon Mac with:

```console
python3.13 -m venv .venv-macos
.venv-macos/bin/python -m pip install -r macos/requirements-dev.txt
macos/scripts/setup-native-python.sh
macos/scripts/check-native-baseline.sh
```

The setup script installs Norfair separately because Norfair 2.3 declares
`numpy < 2`, while Python 3.13 requires a newer NumPy wheel. This is an explicit
native compatibility exception and must remain covered by the import and local
media acceptance tests.

The stopped production container resolves `/usr/bin/python3` to CPython 3.11.2.
The repository development environment uses Python 3.13 per `AGENTS.md`, but
the native runtime code must remain compatible with both 3.11 and 3.13. CoreML
does not require Python 3.13.

The checked-in H.264 fixture is generated from FFmpeg's `testsrc` filter. It
contains synthetic video only and has no audio, metadata copied from a camera,
or external network dependency.

## CoreML hardware acceptance

Phase 3 uses ONNX Runtime 1.22.1 and its public CoreML execution provider. Run
the hardware acceptance with an absolute path to a static 320 by 320 YOLO ONNX
model:

```console
macos/scripts/check-coreml-hardware.sh /absolute/path/to/model.onnx
```

The check compares CoreML output with the CPU provider, rejects unexpected CPU
operations, and records the process with the Xcode Instruments Core ML
template. A passing run must contain Apple Neural Engine prediction intervals.
The model is read only, and all compiled caches, profiles, and test output are
written to a private temporary directory that is removed afterward.

## VideoToolbox hardware acceptance

Phase 4 adds strict VideoToolbox decode and encode presets. Run the acceptance
on Apple Silicon with:

```console
macos/scripts/check-videotoolbox-hardware.sh
```

The check requires FFmpeg to link VideoToolbox, AVFoundation, CoreMedia,
CoreVideo, and AudioToolbox. It hardware-decodes H.264 and HEVC, then exercises
the Birdseye, preview, and timelapse export H.264 encoders. Encoder commands set
`-allow_sw 0`, so a software fallback fails the check instead of being hidden.

## Swift application acceptance

Phase 5 provides the menu bar application and the process supervisor. Its app
builder downloads a pinned, checksum-verified ARM64 CPython 3.11 distribution,
installs the native runtime, and copies FFmpeg, go2rtc, nginx, Frigate, and the
Web UI into the application. Non-system Mach-O dependencies are relocated into
the bundle. The acceptance scan rejects Homebrew paths, user paths, temporary
build paths, non-ARM64 binaries, and symlinks that escape the application.

```console
swift test --package-path macos
macos/scripts/build-native-app.sh "$PWD/macos/build/Frigate.app"
macos/scripts/check-native-app.sh "$PWD/macos/build/Frigate.app"
```

The local phase 5 build is ad hoc signed for development. Developer ID signing,
notarization, a DMG, and the update feed belong to phase 6.

## Upstream synchronization

The `upstream` Git remote points to `blakeblackshear/frigate`. Synchronization
happens only at an explicit integration point:

1. Fetch `upstream` without changing the worktree.
2. Review upstream changes since the commit in `support.json`.
3. Rebase or merge into a dedicated `codex/` integration branch.
4. Run Linux compatibility tests and every macOS acceptance test in
   `patches.json`.
5. Update the pinned commit after the integration result is accepted.

Do not mix a broad upstream synchronization with a release stabilization
change.
