"""Authenticity checks for the official app used as patch input."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path


EXPECTED_BUNDLE_ID = "com.openai.codex"
EXPECTED_TEAM_ID = "2DC432GLL2"
SOURCE_REQUIREMENT = (
    '=anchor apple generic and identifier "com.openai.codex" '
    'and certificate leaf[subject.OU] = "2DC432GLL2"'
)


def _run_checked(arguments: list[str], operation: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            arguments,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"source app {operation} failed") from error


def _display_fields(source: Path) -> dict[str, str]:
    result = _run_checked(
        ["/usr/bin/codesign", "--display", "--verbose=4", str(source)],
        "signature inspection",
    )
    fields: dict[str, str] = {}
    for line in result.stderr.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def verify_source_app(source: Path) -> None:
    """Reject source bundles that are not intact OpenAI arm64 Codex apps."""

    source = Path(source).expanduser()
    if source.is_symlink():
        raise RuntimeError("source app must not be a symlink")
    try:
        source = source.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("source app does not exist") from error
    if not source.is_dir():
        raise RuntimeError("source app is not a directory")

    _run_checked(
        [
            "/usr/bin/codesign",
            "--verify",
            "--deep",
            "--strict",
            "-R",
            SOURCE_REQUIREMENT,
            str(source),
        ],
        "signature verification",
    )
    signature = _display_fields(source)
    if signature.get("Identifier") != EXPECTED_BUNDLE_ID:
        raise RuntimeError("source app has an unexpected code identifier")
    if signature.get("TeamIdentifier") != EXPECTED_TEAM_ID:
        raise RuntimeError("source app has an unexpected signing team")
    if signature.get("Signature", "").lower() == "adhoc":
        raise RuntimeError("source app has an ad-hoc signature")

    plist_path = source / "Contents" / "Info.plist"
    try:
        with plist_path.open("rb") as plist_file:
            metadata = plistlib.load(plist_file)
    except (OSError, plistlib.InvalidFileException) as error:
        raise RuntimeError("source app metadata is invalid") from error
    if metadata.get("CFBundleIdentifier") != EXPECTED_BUNDLE_ID:
        raise RuntimeError("source app has an unexpected bundle identifier")

    executable_name = metadata.get("CFBundleExecutable")
    if (
        not isinstance(executable_name, str)
        or executable_name in {"", ".", ".."}
        or Path(executable_name).name != executable_name
    ):
        raise RuntimeError("source app executable metadata is invalid")
    executable = source / "Contents" / "MacOS" / executable_name
    if executable.is_symlink() or not executable.is_file():
        raise RuntimeError("source app executable is invalid")
    _run_checked(
        ["/usr/bin/lipo", "-verify_arch", "arm64", str(executable)],
        "arm64 architecture verification",
    )


__all__ = ["verify_source_app"]
