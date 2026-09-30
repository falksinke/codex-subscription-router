#!/usr/bin/env python3
"""Create an independently signed ChatGPT.app copy with Codex multiplexing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from asar_integrity import (
    patch_framework_integrity_digest,
    raw_header_sha256,
    resolve_codex_framework,
    validated_source_integrity,
)
from framework_signing import (
    capture_framework_signing_plan,
    sign_framework_tree,
    verify_apple_signed_code,
    verify_untouched_service,
)
from source_validation import verify_source_app


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
DEFAULT_SOURCE = Path("/Applications/ChatGPT.app")
DEFAULT_DESTINATION = Path.home() / "Applications" / "Codex Subscription Router.app"
DEFAULT_STATE_ROOT = Path.home() / ".codex-mux"
DESKTOP_PROFILE_NAME = "Codex Subscription Router"
DESKTOP_BUNDLE_IDENTIFIER = "app.cdxmux.multi"
OPENAI_DESKTOP_CODE_IDENTIFIER = "com.openai.codex"
OPENAI_COMPUTER_USE_BUNDLE_IDENTIFIER = "com.openai.sky.CUAService"
COMPUTER_USE_BUNDLE_IDENTIFIER = "com.cdxmux.sky.CUAService"
COMPUTER_USE_DISPLAY_NAME = "Codex Subscription Router Computer Use"
COMPUTER_USE_APP_NAME = f"{COMPUTER_USE_DISPLAY_NAME}.app"
LAUNCH_SERVICES_REGISTER = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)
ASAR_UNPACK_DIRECTORIES = (
    "node_modules/{@worklouder,better-sqlite3,node-mac-permissions,node-pty,objc-js}"
)
PREFERRED_SIGNING_IDENTITY_PREFIXES = (
    "Developer ID Application:",
    "Apple Development:",
)
OPENAI_INTERNAL_TEAM_IDENTIFIER = "HX7739G8FX"
OPENAI_DISTRIBUTION_TEAM_IDENTIFIER = "2DC432GLL2"
TESTED_SOURCE_BUILDS = {
    (
        "26.928.21956",
        "12404",
    ): "3bda98f2265ad23677dfe0163d1cc7855beade6bef11d27f830f6663d7658406",
}
EXPECTED_CUA_IDENTIFIER_REPLACEMENTS = 49
EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS = 16

BUILD_FILES = {
    "app_server": ".vite/build/application-network-startup-D74LEWDz.js",
    "bootstrap": ".vite/build/bootstrap-B7ariqxX.js",
    "main": ".vite/build/main-BbeJ4AAR.js",
    "preload": ".vite/build/preload.js",
}
WEBVIEW_FILES = {
    "initial": "webview/assets/app-initial-135a4ef2552c.js",
    "modal": "webview/assets/modal-impl-f7bf9823112c.js",
    "plugin_settings": "webview/assets/plugins-settings-19529e3bd1ae.js",
    "profile": "webview/assets/profile-d988344ef180.js",
    "profile_dropdown": "webview/assets/profile-dropdown-items-e592b94308bb.js",
    "thread": "webview/assets/local-conversation-thread-d3f97538bfd2.js",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {PROJECT_VERSION}")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing destination after moving it to a timestamped backup.",
    )
    parser.add_argument(
        "--allow-signing-team-change",
        action="store_true",
        help="Replace an existing build signed by a different Apple team.",
    )
    return parser.parse_args()


def run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def output(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"required tool not found: {name}")


def signing_certificate_team_identifier(identity: str, fingerprint: str) -> str:
    certificates = subprocess.check_output(
        ["security", "find-certificate", "-a", "-c", identity, "-p"]
    )
    matched_subjects: list[str] = []
    for certificate_match in re.finditer(
        rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        certificates,
        re.DOTALL,
    ):
        certificate = certificate_match.group(0) + b"\n"
        details = subprocess.run(
            [
                "openssl",
                "x509",
                "-noout",
                "-fingerprint",
                "-sha1",
                "-subject",
                "-nameopt",
                "sep_multiline",
            ],
            input=certificate,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        ).stdout.decode("utf-8", errors="strict")
        fingerprint_match = re.search(
            r"^[^=]*Fingerprint=([0-9A-Fa-f:]+)$",
            details,
            re.MULTILINE,
        )
        if fingerprint_match is None:
            continue
        actual_fingerprint = fingerprint_match.group(1).replace(":", "").upper()
        if actual_fingerprint == fingerprint:
            matched_subjects.append(details)
    if len(matched_subjects) != 1:
        raise RuntimeError(
            "the selected code-signing identity's public certificate was not unique"
        )
    subject = matched_subjects[0]
    common_name_match = re.search(r"^\s*CN\s*=\s*(.+)$", subject, re.MULTILINE)
    team_match = re.search(
        r"^\s*OU\s*=\s*([A-Z0-9]{10})$",
        subject,
        re.MULTILINE,
    )
    if common_name_match is None or not common_name_match.group(1).startswith(
        PREFERRED_SIGNING_IDENTITY_PREFIXES
    ):
        raise RuntimeError(
            "the selected certificate is not Apple Development or "
            "Developer ID Application"
        )
    if team_match is None:
        raise RuntimeError(
            "the selected certificate has no 10-character Apple team identifier"
        )
    return team_match.group(1)


def resolve_signing_identity() -> tuple[str, str]:
    configured = os.environ.get("CODEX_MUX_SIGNING_IDENTITY", "").strip()
    identities = output(["security", "find-identity", "-v", "-p", "codesigning"])
    available = re.findall(
        r'^\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"([^"]+)"',
        identities,
        re.MULTILINE,
    )
    qualified = [
        (fingerprint.upper(), name)
        for fingerprint, name in available
        if name.startswith(PREFERRED_SIGNING_IDENTITY_PREFIXES)
    ]
    if configured:
        matches = [
            (fingerprint, name)
            for fingerprint, name in qualified
            if configured == name or configured.upper() == fingerprint
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "CODEX_MUX_SIGNING_IDENTITY must select one available Apple "
                "Development or Developer ID Application identity"
            )
        fingerprint, identity = matches[0]
    else:
        selected = next(
            (
                (fingerprint, name)
                for prefix in PREFERRED_SIGNING_IDENTITY_PREFIXES
                for fingerprint, name in qualified
                if name.startswith(prefix)
            ),
            None,
        )
        if selected is None:
            fingerprint, identity = "", ""
        else:
            fingerprint, identity = selected
    if not identity:
        raise RuntimeError(
            "no available Apple Development or Developer ID Application "
            "code-signing identity was found"
        )
    team_identifier = signing_certificate_team_identifier(identity, fingerprint)
    return fingerprint, team_identifier


def signed_code_metadata(path: Path) -> tuple[str | None, str | None]:
    result = subprocess.run(
        ["codesign", "--display", "--verbose=4", str(path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    details = result.stdout + result.stderr
    identifier_match = re.search(r"^Identifier=(.+)$", details, re.MULTILINE)
    team_match = re.search(r"^TeamIdentifier=(.+)$", details, re.MULTILINE)
    identifier = identifier_match.group(1).strip() if identifier_match else None
    team = team_match.group(1).strip() if team_match else None
    if team == "not set":
        team = None
    return identifier, team


def verify_signed_code(
    path: Path,
    expected_identifier: str,
    expected_team: str,
) -> None:
    verify_apple_signed_code(path, expected_identifier, expected_team)


def existing_signing_team(path: Path) -> str | None:
    if not path.exists():
        return None
    plist_path = path / "Contents" / "Info.plist"
    if plist_path.is_file():
        try:
            with plist_path.open("rb") as handle:
                recorded = plistlib.load(handle).get("CodexMuxSigningTeamIdentifier")
            if isinstance(recorded, str) and recorded != "":
                return None if recorded == "adhoc" else recorded
        except (OSError, plistlib.InvalidFileException):
            pass
    _, team = signed_code_metadata(path)
    return team


def ensure_components_are_stopped(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if not path.exists():
            continue
        result = subprocess.run(
            ["pgrep", "-f", str(path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            raise RuntimeError(
                f"quit the running component before replacing it: {path}"
            )


MACH_O_MAGICS = {
    b"\xfe\xed\xfa\xce",  # 32-bit, big endian
    b"\xfe\xed\xfa\xcf",  # 64-bit, big endian
    b"\xce\xfa\xed\xfe",  # 32-bit, little endian
    b"\xcf\xfa\xed\xfe",  # 64-bit, little endian
    b"\xca\xfe\xba\xbe",  # universal binary
    b"\xbe\xba\xfe\xca",  # universal binary, little endian
}


def is_mach_o(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(4) in MACH_O_MAGICS
    except OSError:
        return False


def arm64_swift_small_string(value: str) -> bytes:
    """Encode the instructions used to materialize a 10-byte Swift string."""
    encoded = value.encode("ascii")
    if len(encoded) != 10:
        raise ValueError("a signing team identifier must contain 10 ASCII bytes")

    def instruction(base: int, immediate: int, register: int, shift: int = 0) -> bytes:
        word = base | ((shift // 16) << 21) | (immediate << 5) | register
        return word.to_bytes(4, "little")

    chunks = [
        int.from_bytes(encoded[index : index + 2], "little")
        for index in range(0, len(encoded), 2)
    ]
    return b"".join(
        (
            instruction(0xD2800000, chunks[0], 0),
            instruction(0xF2800000, chunks[1], 0, 16),
            instruction(0xF2800000, chunks[2], 0, 32),
            instruction(0xF2800000, chunks[3], 0, 48),
            instruction(0xD2800000, chunks[4], 1),
            instruction(0xF2800000, 0xEA00, 1, 48),
        )
    )


def replace_same_length_identifier(
    path: Path, original: str, replacement: str
) -> int:
    """Replace an embedded identifier without changing binary or bundle offsets."""
    original_bytes = original.encode("ascii")
    replacement_bytes = replacement.encode("ascii")
    if len(original_bytes) != len(replacement_bytes):
        raise RuntimeError("replacement identifiers must have the same byte length")
    data = path.read_bytes()
    count = data.count(original_bytes)
    if count:
        path.write_bytes(data.replace(original_bytes, replacement_bytes))
    return count


def computer_use_package(app: Path) -> Path:
    return (
        app
        / "Contents"
        / "Resources"
        / "cua_node"
        / "lib"
        / "node_modules"
        / "@oai"
        / "sky"
    )


def retire_stale_cached_computer_use_app() -> None:
    """Move aside only a prior custom helper copied into the shared Codex home."""
    cached_app = (
        Path.home() / ".codex" / "computer-use" / "Codex Computer Use.app"
    )
    plist_path = cached_app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return
    try:
        with plist_path.open("rb") as handle:
            bundle_identifier = plistlib.load(handle).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException):
        return
    if bundle_identifier != COMPUTER_USE_BUNDLE_IDENTIFIER:
        return
    if LAUNCH_SERVICES_REGISTER.is_file():
        run([str(LAUNCH_SERVICES_REGISTER), "-u", str(cached_app)])
    backup = cached_app.with_name(
        f"Codex Computer Use backup-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    cached_app.rename(backup)
    print(f"Stale cached Computer Use helper moved to {backup}")


def patch_computer_use_identity(app: Path, team_identifier: str) -> None:
    """Give the copied CUA service an independent identity and trusted callers."""
    package = computer_use_package(app)
    service = package / "Codex Computer Use.app"
    executable = service / "Contents" / "MacOS" / "SkyComputerUseService"
    if not executable.is_file():
        raise RuntimeError("bundled Codex Computer Use service was not found")

    for profile in package.rglob("embedded.provisionprofile"):
        profile.unlink()

    identifier_replacements = 0
    for candidate in package.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            identifier_replacements += replace_same_length_identifier(
                candidate,
                OPENAI_COMPUTER_USE_BUNDLE_IDENTIFIER,
                COMPUTER_USE_BUNDLE_IDENTIFIER,
            )
    if identifier_replacements != EXPECTED_CUA_IDENTIFIER_REPLACEMENTS:
        raise RuntimeError(
            "expected "
            f"{EXPECTED_CUA_IDENTIFIER_REPLACEMENTS} Computer Use identity "
            f"references, found {identifier_replacements}"
        )

    plist_path = service / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    info["CFBundleIdentifier"] = COMPUTER_USE_BUNDLE_IDENTIFIER
    info["CFBundleDisplayName"] = COMPUTER_USE_DISPLAY_NAME
    info["CFBundleName"] = COMPUTER_USE_DISPLAY_NAME
    for key in list(info):
        if key.startswith("SU"):
            del info[key]
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)

    binary = executable.read_bytes()
    replacement = arm64_swift_small_string(team_identifier)
    for original_team, description in (
        (OPENAI_INTERNAL_TEAM_IDENTIFIER, "internal"),
        (OPENAI_DISTRIBUTION_TEAM_IDENTIFIER, "distribution"),
    ):
        original = arm64_swift_small_string(original_team)
        match_count = binary.count(original)
        if match_count != 2:
            raise RuntimeError(
                f"expected two Computer Use {description}-team checks, "
                f"found {match_count}; the official app layout may have changed"
            )
        binary = binary.replace(original, replacement)

        raw_original = original_team.encode("ascii")
        raw_replacement = team_identifier.encode("ascii")
        raw_match_count = binary.count(raw_original)
        expected_raw_matches = 1 if description == "internal" else 17
        if raw_match_count != expected_raw_matches:
            raise RuntimeError(
                f"expected {expected_raw_matches} Computer Use {description}-team "
                f"constants, found {raw_match_count}; the official app layout may have changed"
            )
        binary = binary.replace(raw_original, raw_replacement)

    original_bundle_id = b"com.openai.codex\0"
    replacement_bundle_id = DESKTOP_BUNDLE_IDENTIFIER.encode("ascii") + b"\0"
    if len(replacement_bundle_id) != len(original_bundle_id):
        raise RuntimeError(
            "the independent bundle identifier must match the CUA identifier length"
        )
    if binary.count(original_bundle_id) != 1:
        raise RuntimeError("could not find the Computer Use production bundle ID")
    executable.write_bytes(binary.replace(original_bundle_id, replacement_bundle_id))


def patch_asar_computer_use_identity(extracted: Path) -> None:
    """Keep desktop launch, temp-file, and service references on the new CUA ID."""
    replacements = 0
    for candidate in extracted.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            replacements += replace_same_length_identifier(
                candidate,
                OPENAI_COMPUTER_USE_BUNDLE_IDENTIFIER,
                COMPUTER_USE_BUNDLE_IDENTIFIER,
            )
    if replacements != EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS:
        raise RuntimeError(
            "expected "
            f"{EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS} Computer Use references "
            f"in app.asar, found {replacements}"
        )


def sign_native_code_tree(root: Path, identity: str) -> None:
    """Sign native modules before ASAR records their final sizes."""
    if not root.is_dir():
        return
    for candidate in root.rglob("*"):
        if not is_mach_o(candidate):
            continue
        run(
            [
                "codesign",
                "--force",
                "--sign",
                identity,
                "--timestamp=none",
                "--options",
                "runtime",
                str(candidate),
            ]
        )


TEAM_SCOPED_ENTITLEMENTS = (
    "com.apple.application-identifier",
    "com.apple.developer.team-identifier",
    "com.apple.security.application-groups",
    "keychain-access-groups",
)
OPENAI_PROVISIONED_DESKTOP_ENTITLEMENTS = (
    "com.apple.developer.aps-environment",
)


def sanitized_runtime_entitlements(executable: Path) -> dict[str, object] | None:
    """Keep runtime capabilities while removing the official app's team grants."""
    result = subprocess.run(
        ["codesign", "--display", "--entitlements", ":-", str(executable)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if not result.stdout.strip():
        return None
    try:
        entitlements = plistlib.loads(result.stdout)
    except plistlib.InvalidFileException as error:
        raise RuntimeError(
            f"could not read signing entitlements from {executable}"
        ) from error
    if not isinstance(entitlements, dict):
        raise RuntimeError(f"invalid signing entitlements on {executable}")
    for key in TEAM_SCOPED_ENTITLEMENTS:
        entitlements.pop(key, None)
    return entitlements or None


def independent_desktop_entitlements(executable: Path) -> dict[str, object] | None:
    """Keep Electron capabilities without claiming OpenAI's APNs provisioning."""
    entitlements = sanitized_runtime_entitlements(executable)
    if entitlements is None:
        return None
    for key in OPENAI_PROVISIONED_DESKTOP_ENTITLEMENTS:
        entitlements.pop(key, None)
    return entitlements or None


AUTO_ENTITLEMENTS = object()


def sign_runtime_executable(
    executable: Path,
    identity: str,
    identifier: str | None = None,
    entitlements: dict[str, object] | None | object = AUTO_ENTITLEMENTS,
    runtime: bool = True,
) -> None:
    """Re-sign an embedded runtime without breaking JIT-backed processes."""
    if entitlements is AUTO_ENTITLEMENTS:
        entitlements = sanitized_runtime_entitlements(executable)
    command = [
        "codesign",
        "--force",
        "--sign",
        identity,
        "--timestamp=none",
    ]
    if runtime:
        command.extend(("--options", "runtime"))
    if identifier is None:
        command.append("--preserve-metadata=identifier")
    else:
        command.extend(("--identifier", identifier))
    if entitlements is None:
        run([*command, str(executable)])
        return
    with tempfile.TemporaryDirectory(prefix=".codesign-entitlements-") as temporary:
        entitlements_path = Path(temporary) / "entitlements.plist"
        with entitlements_path.open("wb") as handle:
            plistlib.dump(
                entitlements,
                handle,
                fmt=plistlib.FMT_XML,
                sort_keys=True,
            )
        run([*command, "--entitlements", str(entitlements_path), str(executable)])


def bundle_main_executable(bundle: Path) -> Path | None:
    plist_path = bundle / "Contents" / "Info.plist"
    executable_root = bundle / "Contents" / "MacOS"
    if bundle.suffix == ".framework":
        plist_path = bundle / "Versions" / "Current" / "Resources" / "Info.plist"
        executable_root = bundle / "Versions" / "Current"
    if not plist_path.is_file():
        return None
    with plist_path.open("rb") as handle:
        executable_name = plistlib.load(handle).get("CFBundleExecutable")
    if not isinstance(executable_name, str) or executable_name == "":
        return None
    executable = executable_root / executable_name
    return executable if executable.is_file() else None


def sign_runtime_bundle(
    bundle: Path,
    identity: str,
    identifier: str | None = None,
    entitlements: dict[str, object] | None | object = AUTO_ENTITLEMENTS,
    runtime: bool = True,
) -> None:
    if entitlements is AUTO_ENTITLEMENTS:
        executable = bundle_main_executable(bundle)
        entitlements = (
            sanitized_runtime_entitlements(executable)
            if executable is not None
            else None
        )
    command = [
        "codesign",
        "--force",
        "--sign",
        identity,
        "--timestamp=none",
    ]
    if runtime:
        command.extend(("--options", "runtime"))
    if identifier is not None:
        command.extend(("--identifier", identifier))
    if entitlements is None:
        run([*command, str(bundle)])
        return
    with tempfile.TemporaryDirectory(prefix=".codesign-entitlements-") as temporary:
        entitlements_path = Path(temporary) / "entitlements.plist"
        with entitlements_path.open("wb") as handle:
            plistlib.dump(
                entitlements,
                handle,
                fmt=plistlib.FMT_XML,
                sort_keys=True,
            )
        run([*command, "--entitlements", str(entitlements_path), str(bundle)])


def capture_computer_use_entitlements(
    app: Path,
) -> dict[Path, dict[str, object] | None]:
    service = computer_use_package(app) / "Codex Computer Use.app"
    if not service.is_dir():
        raise RuntimeError("bundled Codex Computer Use service was not found")
    return {
        executable.relative_to(service): sanitized_runtime_entitlements(executable)
        for executable in service.rglob("*")
        if is_mach_o(executable)
    }


def sign_computer_use_code(
    app: Path,
    identity: str,
    preserved_entitlements: dict[Path, dict[str, object] | None],
) -> None:
    """Keep the Computer Use service and its callers on one signing team."""
    resources = app / "Contents" / "Resources"
    service = computer_use_package(app) / "Codex Computer Use.app"
    if not service.is_dir():
        raise RuntimeError("bundled Codex Computer Use service was not found")

    for executable in sorted(
        (candidate for candidate in service.rglob("*") if is_mach_o(candidate)),
        key=lambda candidate: len(candidate.parts),
        reverse=True,
    ):
        relative = executable.relative_to(service)
        sign_runtime_executable(
            executable,
            identity,
            entitlements=preserved_entitlements.get(relative),
        )

    bundle_suffixes = {".app", ".appex", ".bundle", ".framework", ".xpc"}
    bundles = [
        candidate
        for candidate in service.rglob("*")
        if candidate.is_dir() and candidate.suffix in bundle_suffixes
    ]
    bundles.append(service)
    for bundle in sorted(
        set(bundles),
        key=lambda candidate: len(candidate.parts),
        reverse=True,
    ):
        identifier = (
            COMPUTER_USE_BUNDLE_IDENTIFIER if bundle == service else None
        )
        executable = bundle_main_executable(bundle)
        entitlements = (
            preserved_entitlements.get(executable.relative_to(service))
            if executable is not None
            else None
        )
        sign_runtime_bundle(bundle, identity, identifier, entitlements)
        run(["codesign", "--verify", "--deep", "--strict", str(bundle)])

    for executable_name in ("node", "node_repl"):
        executable = resources / "cua_node" / "bin" / executable_name
        sign_runtime_executable(executable, identity)
    desktop_executable = app / "Contents" / "MacOS" / "ChatGPT"
    sign_runtime_executable(
        desktop_executable,
        identity,
        OPENAI_DESKTOP_CODE_IDENTIFIER,
        entitlements=independent_desktop_entitlements(desktop_executable),
        runtime=False,
    )


def sign_independent_app(
    app: Path, identity: str, team_identifier: str
) -> None:
    """Apply one stable identity throughout the modified Electron bundle."""
    computer_use_entitlements = capture_computer_use_entitlements(app)
    patch_computer_use_identity(app, team_identifier)
    sign_computer_use_code(app, identity, computer_use_entitlements)
    run(
        [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--timestamp=none",
            str(app / "Contents" / "Resources" / "codex-mux"),
        ]
    )
    run(
        [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--timestamp=none",
            str(app),
        ]
    )


def load_or_create_token() -> str:
    DEFAULT_STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    DEFAULT_STATE_ROOT.chmod(0o700)
    token_path = DEFAULT_STATE_ROOT / "control-token"
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{64}", token) is None:
            raise RuntimeError(f"invalid control token at {token_path}")
        token_path.chmod(0o600)
        return token
    token = secrets.token_hex(32)
    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


def build_proxy(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "go",
            "build",
            "-trimpath",
            "-ldflags=-s -w",
            "-o",
            str(destination),
            "./cmd/codex-mux",
        ],
        cwd=PROJECT_ROOT,
    )
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_launcher(app: Path) -> None:
    """Pass Chromium its isolated profile before Electron's main process starts."""
    launcher = app / "Contents" / "MacOS" / "CodexSubscriptionRouterLauncher"
    run(
        [
            "xcrun",
            "clang",
            "-Os",
            "-Wall",
            "-Wextra",
            "-o",
            str(launcher),
            str(PROJECT_ROOT / "native" / "launcher.c"),
        ]
    )


def ensure_asar_tool() -> Path:
    asar = PROJECT_ROOT / "node_modules" / ".bin" / "asar"
    package_manifest = PROJECT_ROOT / "node_modules" / "@electron" / "asar" / "package.json"
    expected = json.loads(
        (PROJECT_ROOT / "package.json").read_text(encoding="utf-8")
    )["devDependencies"]["@electron/asar"]
    if not asar.exists() or not package_manifest.is_file():
        raise RuntimeError("run `npm ci --ignore-scripts` before patching")
    actual = json.loads(package_manifest.read_text(encoding="utf-8")).get("version")
    if actual != expected:
        raise RuntimeError(
            f"installed @electron/asar is {actual!r}, expected {expected!r}; "
            "run `npm ci --ignore-scripts`"
        )
    return asar


def current_bundle_file(extracted: Path, relative_path: str, description: str) -> Path:
    path = extracted / relative_path
    if not path.is_file():
        raise RuntimeError(f"could not find the tested {description}: {relative_path}")
    return path


def replace_anchor(
    source: str,
    anchor: str,
    replacement: str,
    description: str,
    *,
    expected: int = 1,
) -> str:
    count = source.count(anchor)
    if count != expected:
        raise RuntimeError(
            f"expected {expected} {description} anchor(s), found {count}"
        )
    return source.replace(anchor, replacement, expected)


def patch_account_component(component: str) -> str:
    component = replace_anchor(component, "Lo(Q)", "Fe(Z)", "modal scope")
    component = replace_anchor(
        component,
        "CH.Separator",
        "CodexMuxMenuSeparator",
        "menu separator",
        expected=2,
    )
    for original, replacement, expected in (
        ("e7", "o$", 69),
        ("kXc", "$oo", 33),
        ("QLs", "QAi", 1),
        ("BW", "ff", 1),
        ("lt", "to", 1),
        ("_H", "CodexMuxMenuItem", 8),
        ("S2", "CodexMuxUsageIcon", 3),
        ("jLa", "codexMuxResolveImageUrl", 1),
        ("codexMuxOpenExternal", "eA", 1),
    ):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_$]){re.escape(original)}(?![A-Za-z0-9_$])"
        )
        component, count = pattern.subn(replacement, component)
        if count != expected:
            raise RuntimeError(
                f"expected {expected} {original} component bindings, found {count}"
            )

    prelude = r'''
function CodexMuxMenuSeparator() {
  return (0, o$.jsx)("div", {
    className: "w-full px-[var(--app-menu-separator-inset,var(--padding-row-x))] py-[var(--app-menu-separator-gutter,var(--spacing))]",
    role: "separator",
    children: (0, o$.jsx)("div", {
      className: "h-px w-full bg-border",
    }),
  });
}
function CodexMuxUsageIcon(props) {
  return (0, o$.jsx)("span", {
    ...props,
    className: `${props?.className || ""} flex items-center justify-center rounded-full border border-current text-[9px]`,
    "aria-hidden": true,
    children: "%",
  });
}
function CodexMuxMenuItem({
  LeftIcon,
  SubText,
  rightIcon,
  onSelect,
  onClick,
  children,
  className = "",
  tone,
}) {
  const handler = onSelect || onClick;
  const iconClass = SubText ? "icon-sm" : "icon-xs";
  const content = (0, o$.jsxs)(o$.Fragment, {
    children: [
      LeftIcon ? (0, o$.jsx)(LeftIcon, {
        className: `${iconClass} shrink-0 opacity-75 group-focus:opacity-100 group-hover:opacity-100`,
      }) : null,
      (0, o$.jsxs)("span", {
        className: "flex min-w-0 flex-1 flex-col text-left",
        children: [
          (0, o$.jsx)("span", {
            className: `min-w-0 truncate ${tone === "danger" ? "text-danger" : "text-default"}`,
            children,
          }),
          SubText ? (0, o$.jsx)("span", {
            className: "min-w-0 truncate text-xs leading-dense text-tertiary",
            children: SubText,
          }) : null,
        ],
      }),
      rightIcon || null,
    ],
  });
  const rowClass = `outline-hidden flex min-h-[var(--app-menu-item-height,0px)] w-full shrink-0 items-center justify-center gap-[var(--spacing-menu-item-content,calc(var(--spacing)*1.5))] rounded-xl p-[var(--app-menu-item-padding,var(--padding-row-y)_var(--padding-row-x))] text-(length:--app-menu-item-font-size,var(--text-sm)) leading-(--app-menu-item-line-height,var(--text-sm--line-height)) ${className}`;
  if (!handler) {
    return (0, o$.jsx)("div", {
      className: rowClass,
      children: content,
    });
  }
  return (0, o$.jsx)("button", {
    type: "button",
    className: `${rowClass} group cursor-interaction hover:bg-primary-ghost-hover focus:bg-primary-ghost-hover`,
    onClick: handler,
    children: content,
  });
}
function codexMuxResolveImageUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}
function codexMuxScopePluginRpcRequest(method, params) {
  const logicalMethod = {
    "app/list": "list-apps",
    "app/installed": "list-installed-apps",
    "app/read": "read-apps",
    "mcpServerStatus/list": "list-mcp-server-status",
    "mcpServer/oauth/login": "login-mcp-server",
  }[method];
  return logicalMethod ? codexMuxScopePluginRequest(logicalMethod, params) : params;
}
'''
    suffix = (
        "\nglobalThis.CodexMuxAccountMenu=()=>"
        "(0,o$.jsx)(CodexMuxAccountMenu,{});"
        "globalThis.codexMuxScopePluginRpcRequest="
        "codexMuxScopePluginRpcRequest;\n"
    )
    return prelude + component + suffix


def patch_renderer(extracted: Path) -> None:
    bundle_path = current_bundle_file(
        extracted, WEBVIEW_FILES["initial"], "initial renderer bundle"
    )
    bundle = bundle_path.read_text(encoding="utf-8")
    if "function CodexMuxAccountMenu(" in bundle:
        raise RuntimeError("source app already contains the Codex multiplexer menu")

    external_open_helper_anchor = (
        "function eA({clickModifiers:e,disposition:t,externalReturnSource:n,href:r,"
        "hostId:i,initiator:a,openTarget:o,openTargetIntent:s,originHostId:c,"
        "presentationIntent:l,source:u=`manual`,targetChromeTabId:d,"
        "useExternalBrowser:f}){return Di(r)?(Nd.dispatchMessage(`open-in-browser`,"
        "{...e==null?{}:{clickModifiers:e},disposition:t,externalReturnSource:n,"
        "hostId:i,initiator:a,openTarget:o,openTargetIntent:s,originHostId:c,"
        "presentationIntent:l,source:u,targetChromeTabId:d,useExternalBrowser:f,"
        "url:zo(r)}),!0):!1}"
    )
    external_open_source_call_anchor = (
        "n?.stage===`gateway`&&n.authUrl!=null){eA({href:n.authUrl,"
        "initiator:`open_in_browser_bridge`,openTarget:`external-browser`});return}"
    )
    component_anchor = "function Xoo(e){let t=(0,Qoo.c)(40)"
    for anchor, description in (
        (external_open_helper_anchor, "native external-browser helper"),
        (external_open_source_call_anchor, "native OAuth external-browser call"),
        (component_anchor, "native profile menu component"),
    ):
        count = bundle.count(anchor)
        if count != 1:
            raise RuntimeError(f"expected 1 {description} anchor(s), found {count}")
    if bundle.index(external_open_helper_anchor) >= bundle.index(component_anchor):
        raise RuntimeError(
            "native external-browser helper must be defined before menu injection"
        )

    # This independently signed copy is rebuilt from reviewed source; it cannot
    # apply official in-app updates. Suppress only the updater presentation,
    # leaving the mandatory startup requirements and version checks intact.
    bundle = replace_anchor(
        bundle,
        "function RXo(e){let t=(0,zXo.c)(85),n;",
        "function RXo(e){return null;let t=(0,zXo.c)(85),n;",
        "independent-copy updater presentation",
    )

    component = patch_account_component(
        (PROJECT_ROOT / "ui" / "account-menu.js").read_text(encoding="utf-8")
    )
    bundle = replace_anchor(
        bundle,
        component_anchor,
        component + "\n" + component_anchor,
        "native profile menu component",
    )

    for anchor, replacement, description in (
        (
            "t(`app/list`,{cursor:n,limit:Azt,forceRefetch:e},{trace:r})",
            "t(`app/list`,codexMuxScopePluginRpcRequest(`app/list`,"
            "{cursor:n,limit:Azt,forceRefetch:e}),{trace:r})",
            "plugin app-list request",
        ),
        (
            "t(`app/installed`,e?{forceRefresh:!0}:{})",
            "t(`app/installed`,codexMuxScopePluginRpcRequest(`app/installed`,"
            "e?{forceRefresh:!0}:{}))",
            "plugin installed-app request",
        ),
        (
            "t(`app/read`,{appIds:e})",
            "t(`app/read`,codexMuxScopePluginRpcRequest(`app/read`,{appIds:e}))",
            "plugin app-read request",
        ),
        (
            "function dQs(e,t,n){return e.sendRequest(t,n,{timeoutMs:hQs})}",
            "function dQs(e,t,n){return e.sendRequest(t,"
            "codexMuxScopePluginRpcRequest(t,n),{timeoutMs:hQs})}",
            "plugin status request bridge",
        ),
    ):
        bundle = replace_anchor(bundle, anchor, replacement, description)

    bundle = replace_anchor(
        bundle,
        "let e=await vf.safeGet(`/wham/profiles/me`)",
        "let e=await codexMuxProfileData("
        "globalThis.__codexMuxSelectedProfileAccountId??null)",
        "native profile stats request",
    )
    reset_query_anchor = (
        "function ber(){let e=(0,xI.c)(1);Th(),W(null);let t;return "
        "e[0]===Symbol.for(`react.memo_cache_sentinel`)?"
        "(t={queryKey:[`rate-limit-reset-credits`],queryFn:Ser,select:xer,"
        "refetchInterval:Wd.ONE_MINUTE,staleTime:Wd.FIVE_SECONDS},e[0]=t):"
        "t=e[0],wf(t)}"
    )
    bundle = replace_anchor(
        bundle,
        reset_query_anchor,
        "function ber(){let e=globalThis.__codexMuxResetAccountId;return wf({"
        "queryKey:[`rate-limit-reset-credits`,e??`primary`],"
        "queryFn:e?()=>codexMuxRateLimitResets(e):Ser,select:xer,"
        "refetchInterval:Wd.ONE_MINUTE,staleTime:Wd.FIVE_SECONDS})}",
        "native reset-credit query",
    )
    reset_mutation_anchor = (
        "function Cer(){let e=(0,xI.c)(3),t=to(),n=jm(),r;return "
        "e[0]!==n||e[1]!==t?(r={mutationFn:wer,onSuccess:(e,r)=>{"
        "let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){"
        "let n=e.code===`reset`?e.credit?.id??i:i;"
        "t.setQueryData([`rate-limit-reset-credits`],e=>q9n(e,a,n))}"
        "Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},"
        "e[0]=n,e[1]=t,e[2]=r):r=e[2],Ih(r)}"
    )
    bundle = replace_anchor(
        bundle,
        reset_mutation_anchor,
        "function Cer(){let e=to(),t=jm(),n=globalThis.__codexMuxResetAccountId,"
        "r=[`rate-limit-reset-credits`,n??`primary`];return Ih({"
        "mutationFn:n?e=>codexMuxConsumeRateLimitReset(n,e):wer,"
        "onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;"
        "if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?"
        "n.credit?.id??a:a;e.setQueryData(r,e=>q9n(e,o,t))}"
        "Promise.all([t([`rate-limit-status`]),t(r)])}})}",
        "native reset-credit mutation",
    )

    for anchor in (
        "triggerButton:w,onOpenChange:b,children:D",
        "open:c,onOpenChange:b,contentWidth:`panel`,triggerButton:w,children:D",
    ):
        bundle = replace_anchor(
            bundle,
            anchor,
            anchor.replace(
                "onOpenChange:b",
                "onOpenChange:CodexMuxProfileMenuOpenChange(b)",
            ),
            "native profile menu open-state hook",
        )
    bundle = replace_anchor(
        bundle,
        "defaultMessage:`You’re out of usage`",
        "defaultMessage:`All connected subscriptions are depleted`",
        "native subscription depletion alert",
    )
    bundle_path.write_text(bundle, encoding="utf-8")

    dropdown_path = current_bundle_file(
        extracted, WEBVIEW_FILES["profile_dropdown"], "profile menu bundle"
    )
    dropdown = dropdown_path.read_text(encoding="utf-8")
    dropdown = replace_anchor(
        dropdown,
        "usageItems:qn",
        "usageItems:globalThis.CodexMuxAccountMenu?.()??qn",
        "native profile usage menu slot",
    )
    dropdown_path.write_text(dropdown, encoding="utf-8")

    profile_path = current_bundle_file(
        extracted, WEBVIEW_FILES["profile"], "profile settings bundle"
    )
    profile = profile_path.read_text(encoding="utf-8")
    profile = replace_anchor(
        profile,
        "avatar:(0,$.jsxs)($.Fragment,{children:[",
        "avatar:(0,$.jsxs)($.Fragment,{children:["
        "globalThis.CodexMuxProfileAvatarStack?.({onSelect:()=>bt.refetch()})??null,",
        "native profile avatar",
    )
    profile_path.write_text(profile, encoding="utf-8")

    modal_path = current_bundle_file(
        extracted, WEBVIEW_FILES["modal"], "Usage modal bundle"
    )
    modal = modal_path.read_text(encoding="utf-8")
    modal = replace_anchor(
        modal,
        "t[5]===y?b=t[6]:(b=ye(y),t[5]=y,t[6]=b);let x=b;",
        "t[5]===y?b=t[6]:(b=ye(y),t[5]=y,t[6]=b);"
        "let x=globalThis.__codexMuxSelectedUsageWindows??b;",
        "native usage-window selection",
    )
    modal = replace_anchor(
        modal,
        "children:[Me,Le,Re,ze]",
        "children:[globalThis.__codexMuxResetAccountSelector??null,Me,Le,Re,ze]",
        "native Usage modal body",
    )
    modal_path.write_text(modal, encoding="utf-8")

    plugin_path = current_bundle_file(
        extracted, WEBVIEW_FILES["plugin_settings"], "Plugins settings bundle"
    )
    plugin = plugin_path.read_text(encoding="utf-8")
    plugin = replace_anchor(
        plugin,
        "C=(0,ao.jsx)(Xn,{title:h,subtitle:g,action:S,children:m})",
        "C=(0,ao.jsx)(Xn,{title:h,subtitle:g,action:S,children:["
        "globalThis.CodexMuxPluginScope?.()??null,m]})",
        "native Plugins settings content",
    )
    oauth_anchor = (
        "st(u,i).sendRequest(`mcpServer/oauth/login`,"
        "{name:e,...pe&&t!==`auto`?{clientRegistration:t}:{}})"
    )
    plugin = replace_anchor(
        plugin,
        oauth_anchor,
        "st(u,i).sendRequest(`mcpServer/oauth/login`,"
        "globalThis.codexMuxScopePluginRpcRequest(`mcpServer/oauth/login`,"
        "{name:e,...pe&&t!==`auto`?{clientRegistration:t}:{}}))",
        "native Plugins OAuth request",
    )
    plugin_path.write_text(plugin, encoding="utf-8")

    thread_path = current_bundle_file(
        extracted, WEBVIEW_FILES["thread"], "local conversation bundle"
    )
    thread = thread_path.read_text(encoding="utf-8")
    thread_component = (PROJECT_ROOT / "ui" / "thread-subscription.js").read_text(
        encoding="utf-8"
    )
    route_header = (
        "function CodexMuxThreadSubscription() {\n"
        "  const route = $n(sr);\n"
        "  const threadId =\n"
        "    route.value.routeKind === \"local-thread\" ? "
        "route.value.conversationId : null;\n"
    )
    thread_component = replace_anchor(
        thread_component,
        route_header,
        "function CodexMuxThreadSubscription({ threadId }) {\n",
        "thread route binding",
    )
    thread_component = replace_anchor(
        thread_component, "TE.", "Dw.", "thread React binding", expected=2
    )
    thread_component = replace_anchor(
        thread_component, "zE.", "bE.", "thread JSX binding", expected=6
    )
    thread_component = replace_anchor(
        thread_component, "K.Section", "Q.Section", "thread section binding"
    )
    thread_anchor = "function hE(e){let t=(0,vE.c)(60)"
    thread = replace_anchor(
        thread,
        thread_anchor,
        thread_component + "\n" + thread_anchor,
        "native thread summary component",
    )
    thread = replace_anchor(
        thread,
        "let A=k;if(p&&d!=null){",
        "let A=[(0,bE.jsx)(CodexMuxThreadSubscription,{threadId:d},"
        "`codex-mux-subscription`),k];if(p&&d!=null){",
        "native thread summary section list",
    )
    thread_path.write_text(thread, encoding="utf-8")


def patch_desktop_profile(
    extracted: Path, installed_computer_use_app: Path
) -> None:
    """Give the copied Electron app its own user-data and single-instance scope."""
    bootstrap_path = current_bundle_file(
        extracted, BUILD_FILES["bootstrap"], "desktop bootstrap bundle"
    )
    bootstrap = bootstrap_path.read_text(encoding="utf-8")
    profile_pattern = re.compile(
        r"(?P<electron>[A-Za-z_$][\w$]*)\.app\.setPath\("
        r"`userData`,[A-Za-z_$][\w$]*\(\{"
        r"appDataPath:(?P=electron)\.app\.getPath\(`appData`\),"
        r"buildFlavor:[^,}]+,env:process\.env\}\)\)"
    )

    def replacement(match: re.Match[str]) -> str:
        electron = match.group("electron")
        computer_use_pipe = json.dumps(str(DEFAULT_STATE_ROOT / "computer-use.sock"))
        computer_use_app = json.dumps(str(installed_computer_use_app))
        return (
            f"process.env.SKY_CUA_SERVICE_NATIVE_PIPE_PATH={computer_use_pipe};"
            f"process.env.SKY_CUA_SERVICE_PATH={computer_use_app};"
            f"process.env.CODEX_ELECTRON_COMPUTER_USE_APP_PATH={computer_use_app};"
            "process.env.CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH=`1`;"
            f"{electron}.app.setPath(`userData`,"
            f"{electron}.app.getPath(`appData`)+`/{DESKTOP_PROFILE_NAME}`)"
        )

    bootstrap, replacements = profile_pattern.subn(replacement, bootstrap, count=1)
    if replacements != 1:
        raise RuntimeError("could not isolate the copied ChatGPT desktop profile")

    # Disable both normal startup and startup-failure updater entry points.
    bootstrap = replace_anchor(
        bootstrap,
        "await n.initialize(),",
        "",
        "normal updater startup",
    )
    bootstrap = replace_anchor(
        bootstrap,
        "await n.startUpdaterAfterStartupFailure(),",
        "",
        "failure-path updater startup",
    )
    bootstrap_path.write_text(bootstrap, encoding="utf-8")

    main_path = current_bundle_file(
        extracted, BUILD_FILES["main"], "desktop main bundle"
    )
    main = main_path.read_text(encoding="utf-8")
    managed_service_pattern = re.compile(
        r"(?P<prefix>[A-Za-z_$][\w$]*=new [A-Za-z_$][\w$]*\()"
        r"[A-Za-z_$][\w$]*\([A-Za-z_$][\w$]*\.codexHome\)"
        r"(?P<suffix>,\{onServiceAvailable:)"
    )
    main, managed_service_replacements = managed_service_pattern.subn(
        lambda match: (
            match.group("prefix")
            + json.dumps(str(installed_computer_use_app))
            + match.group("suffix")
        ),
        main,
        count=1,
    )
    if managed_service_replacements != 1:
        raise RuntimeError(
            "could not pin the managed Computer Use service to its installed app"
        )

    computer_use_instruction = (
        "Control desktop apps on macOS through Computer Use."
    )
    strict_computer_use_instruction = (
        "Control desktop apps on macOS through Computer Use via node_repl and "
        "@oai/sky only. Never use shell commands, open, AppleScript, osascript, "
        "JXA, System Events, or CGEvent synthesis for computer interactions or "
        "as a fallback. If Computer Use is unavailable, report the failure "
        "instead of using another automation method."
    )
    if main.count(computer_use_instruction) != 1:
        raise RuntimeError("could not find the Computer Use tool instruction")
    main = main.replace(
        computer_use_instruction,
        strict_computer_use_instruction,
        1,
    )
    build_directory = extracted / ".vite" / "build"
    ui_test_bridge = build_directory / "ui-test-bridge.cjs"
    control_main = build_directory / "control-main.cjs"
    if control_main.exists():
        raise RuntimeError("source app already contains the control main helper")
    shutil.copy2(PROJECT_ROOT / "ui" / "ui-test-bridge.cjs", ui_test_bridge)
    shutil.copy2(PROJECT_ROOT / "ui" / "control-main.cjs", control_main)
    if control_main.read_bytes() != (PROJECT_ROOT / "ui" / "control-main.cjs").read_bytes():
        raise RuntimeError("copied control main helper does not match its source")
    source_map_anchor = "//# sourceMappingURL=main-BbeJ4AAR.js.map"
    main_injection = (
        "require(require(`node:path`).join(__dirname,`control-main.cjs`));\n"
        ";if(process.env.CODEX_MUX_UI_TESTS===`1`)"
        "require(require(`node:path`).join(__dirname,`ui-test-bridge.cjs`)).start();\n"
    )
    main = replace_anchor(
        main,
        source_map_anchor,
        main_injection + source_map_anchor,
        "desktop main source-map trailer",
    )
    main_path.write_text(main, encoding="utf-8")

    preload_path = current_bundle_file(
        extracted, BUILD_FILES["preload"], "main-window preload bundle"
    )
    preload = preload_path.read_text(encoding="utf-8")
    preload_helper = (PROJECT_ROOT / "ui" / "control-preload.cjs").read_text(
        encoding="utf-8"
    )
    if "codex-mux:control:request:v1" in preload:
        raise RuntimeError("source app already contains the control preload helper")
    preload_source_map = "//# sourceMappingURL=preload.js.map"
    preload = replace_anchor(
        preload,
        preload_source_map,
        preload_helper.rstrip() + "\n" + preload_source_map,
        "main-window preload source-map trailer",
    )
    preload_path.write_text(preload, encoding="utf-8")


def patch_app_server_launcher(extracted: Path) -> None:
    """Route only the bundled local desktop connection through codex-mux."""
    path = current_bundle_file(
        extracted, BUILD_FILES["app_server"], "application network bundle"
    )
    source = path.read_text(encoding="utf-8")
    resolver_anchor = (
        "function Qt(e){if(process.platform===`darwin`){if(e==null)return null;"
        "let t=(0,c.join)(e,`codex-cli`,`CodexCLI.app`,`Contents`,`MacOS`,`codex`);"
        "return bn(t)?t:null}"
    )
    if source.count(resolver_anchor) != 1:
        raise RuntimeError("could not verify the bundled macOS Codex resolver")
    connect_anchor = (
        "t=await Gs(this.options,e);if(!t)throw Error(`Unable to locate the Codex "
        "CLI binary or required runtime components. Check the installation or "
        "explicit runtime overrides.`);let n="
    )
    connect_replacement = (
        "t=await Gs(this.options,e);if(!t)throw Error(`Unable to locate the Codex "
        "CLI binary or required runtime components. Check the installation or "
        "explicit runtime overrides.`);"
        "process.platform===`darwin`&&this.options.hostConfig.kind===`local`&&"
        "!process.env.CODEX_CLI_PATH?.trim()&&"
        "this.options.hostConfig.codex_cli_command==null&&"
        "t.executablePath===Qt(this.options.resourcesPath)&&"
        "(t={...t,executablePath:(0,c.join)(this.options.resourcesPath,`codex-mux`),"
        "env:{...t.env,CODEX_MUX_REAL_CODEX:t.executablePath}});let n="
    )
    source = replace_anchor(
        source,
        connect_anchor,
        connect_replacement,
        "bundled local desktop app-server connection",
    )
    path.write_text(source, encoding="utf-8")


def patch_info_plist(
    app: Path,
    asar_path: Path,
    team_identifier: str,
) -> dict[str, object]:
    plist_path = app / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    info["CFBundleDisplayName"] = "Codex Subscription Router"
    info["CFBundleName"] = "Codex Subscription Router"
    # A distinct identifier keeps Launch Services and external Computer Use from
    # confusing this independently signed copy with the official ChatGPT app.
    info["CFBundleIdentifier"] = DESKTOP_BUNDLE_IDENTIFIER
    info["CFBundleExecutable"] = "CodexSubscriptionRouterLauncher"
    info["BundleSigningBaseName"] = "CodexSubscriptionRouter"
    info["CodexMuxSigningTeamIdentifier"] = team_identifier
    info["CrProductDirName"] = DESKTOP_PROFILE_NAME
    for key in list(info):
        if key.startswith("SU"):
            del info[key]
    info["SUEnableAutomaticChecks"] = False
    info["SUAllowsAutomaticUpdates"] = False
    for url_type in info.get("CFBundleURLTypes", []):
        schemes = url_type.get("CFBundleURLSchemes", [])
        url_type["CFBundleURLSchemes"] = [
            "codex-subscription-router" if value == "codex" else value for value in schemes
        ]
    digest = raw_header_sha256(asar_path)
    integrity: dict[str, object] = {
        "Resources/app.asar": {"algorithm": "SHA256", "hash": digest}
    }
    info["ElectronAsarIntegrity"] = integrity
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)
    return integrity


def patch_app(
    source: Path,
    destination: Path,
    force: bool,
    allow_signing_team_change: bool,
) -> None:
    source = source.expanduser()
    destination = destination.expanduser().resolve()
    verify_source_app(source)
    source = source.resolve(strict=True)
    if source == destination:
        raise RuntimeError(
            "source and destination must be different; "
            "the original app is never patched in place"
        )
    if destination.exists() and not force:
        raise RuntimeError(
            f"destination exists: {destination} "
            "(pass --force to create a recoverable backup)"
        )

    source_plist = source / "Contents" / "Info.plist"
    with source_plist.open("rb") as handle:
        source_info = plistlib.load(handle)
    source_version = str(source_info.get("CFBundleShortVersionString", "unknown"))
    source_build = str(source_info.get("CFBundleVersion", "unknown"))
    source_asar = source / "Contents" / "Resources" / "app.asar"
    source_asar_hash = hashlib.sha256(source_asar.read_bytes()).hexdigest()
    expected_asar_hash = TESTED_SOURCE_BUILDS.get((source_version, source_build))
    print(
        f"Source ChatGPT version: {source_version} ({source_build}), "
        f"app.asar {source_asar_hash}"
    )
    if expected_asar_hash != source_asar_hash:
        raise RuntimeError(
            "the source version, build, or app.asar hash is not approved; "
            "review and port the upstream change before patching"
        )
    source_asar_integrity = validated_source_integrity(
        source_info,
        source_asar,
    )

    for tool in (
        "codesign",
        "ditto",
        "go",
        "npm",
        "openssl",
        "security",
        "xcrun",
    ):
        require_tool(tool)
    signing_identity, team_identifier = resolve_signing_identity()
    if destination.exists():
        installed_team = existing_signing_team(destination)
        if installed_team != team_identifier and not allow_signing_team_change:
            raise RuntimeError(
                "the selected signing team differs from the installed build; "
                "reuse the prior identity or pass --allow-signing-team-change"
            )
    asar = ensure_asar_tool()
    load_or_create_token()
    destination.parent.mkdir(parents=True, exist_ok=True)
    installed_computer_use_app = destination.parent / COMPUTER_USE_APP_NAME
    if force:
        ensure_components_are_stopped((destination, installed_computer_use_app))

    with tempfile.TemporaryDirectory(prefix=".codex-subscription-router-", dir=destination.parent) as temporary:
        temporary_path = Path(temporary)
        staged_app = temporary_path / destination.name
        staged_computer_use_app = temporary_path / COMPUTER_USE_APP_NAME
        extracted = temporary_path / "asar"
        proxy = temporary_path / "codex-mux"

        print("Building multiplexer…")
        build_proxy(proxy)
        print("Copying ChatGPT.app…")
        run(["ditto", str(source), str(staged_app)])
        verify_source_app(staged_app)
        staged_app.chmod(0o700)
        codex_framework, codex_framework_binary = resolve_codex_framework(
            staged_app
        )
        framework_signing_plan = capture_framework_signing_plan(
            codex_framework,
            codex_framework_binary,
        )
        staged_asar = staged_app / "Contents" / "Resources" / "app.asar"
        staged_asar_hash = hashlib.sha256(staged_asar.read_bytes()).hexdigest()
        if staged_asar_hash != source_asar_hash:
            raise RuntimeError("the staged app.asar does not match the approved source")
        install_launcher(staged_app)

        resources = staged_app / "Contents" / "Resources"
        original_asar = resources / "app.asar"
        print("Patching desktop profile and renderer…")
        run([str(asar), "extract", str(original_asar), str(extracted)])
        patch_asar_computer_use_identity(extracted)
        patch_desktop_profile(extracted, installed_computer_use_app)
        patch_app_server_launcher(extracted)
        patch_renderer(extracted)
        sign_native_code_tree(extracted, signing_identity)
        repacked_asar = temporary_path / "app.asar"
        run(
            [
                str(asar),
                "pack",
                "--unpack-dir",
                ASAR_UNPACK_DIRECTORIES,
                str(extracted),
                str(repacked_asar),
            ]
        )
        asar_listing = output([str(asar), "list", "--is-pack", str(repacked_asar)])
        required_unpacked_module = (
            "unpack : /node_modules/better-sqlite3/build/Release/"
            "better_sqlite3.node"
        )
        if required_unpacked_module not in asar_listing:
            raise RuntimeError("native ASAR modules were not kept unpacked")
        shutil.copy2(repacked_asar, original_asar)
        original_asar.chmod(0o600)
        repacked_unpacked = temporary_path / "app.asar.unpacked"
        if not repacked_unpacked.is_dir():
            raise RuntimeError("ASAR pack did not produce its unpacked native tree")
        shutil.copytree(
            repacked_unpacked,
            resources / "app.asar.unpacked",
            dirs_exist_ok=True,
        )

        bundled_mux = resources / "codex-mux"
        if bundled_mux.exists():
            raise RuntimeError("source app already contains codex-mux")
        shutil.copy2(proxy, bundled_mux)
        bundled_mux.chmod(0o755)

        final_asar_integrity = patch_info_plist(
            staged_app,
            original_asar,
            team_identifier,
        )
        patch_framework_integrity_digest(
            codex_framework_binary,
            source_asar_integrity,
            final_asar_integrity,
        )
        sign_framework_tree(
            framework_signing_plan,
            signing_identity,
            team_identifier,
        )
        print(f"Signing independent app copy with {signing_identity}…")
        sign_independent_app(staged_app, signing_identity, team_identifier)
        verify_untouched_service(framework_signing_plan)
        verify_signed_code(
            staged_app,
            DESKTOP_BUNDLE_IDENTIFIER,
            team_identifier,
        )
        verify_signed_code(
            staged_app / "Contents" / "MacOS" / "ChatGPT",
            OPENAI_DESKTOP_CODE_IDENTIFIER,
            team_identifier,
        )
        bundled_computer_use_app = (
            computer_use_package(staged_app) / "Codex Computer Use.app"
        )
        run(
            [
                "ditto",
                str(bundled_computer_use_app),
                str(staged_computer_use_app),
            ]
        )
        staged_computer_use_app.chmod(0o700)
        verify_signed_code(
            staged_computer_use_app,
            COMPUTER_USE_BUNDLE_IDENTIFIER,
            team_identifier,
        )

        backup_suffix = time.strftime("%Y%m%d-%H%M%S")
        backup_directory = DEFAULT_STATE_ROOT / "backups" / backup_suffix
        app_backup = backup_directory / destination.name
        helper_backup = backup_directory / installed_computer_use_app.name
        had_app = destination.exists()
        had_helper = installed_computer_use_app.exists()
        if had_app or had_helper:
            backup_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup_directory.parent.chmod(0o700)
            backup_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        try:
            if had_app:
                destination.rename(app_backup)
                print(f"Existing copy moved to {app_backup}")
            if had_helper:
                installed_computer_use_app.rename(helper_backup)
                print(f"Existing Computer Use helper moved to {helper_backup}")
            staged_app.rename(destination)
            staged_computer_use_app.rename(installed_computer_use_app)
        except OSError:
            failed_directory = backup_directory / "failed-install"
            failed_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if destination.exists():
                destination.rename(failed_directory / destination.name)
            if installed_computer_use_app.exists():
                installed_computer_use_app.rename(
                    failed_directory / installed_computer_use_app.name
                )
            if app_backup.exists():
                app_backup.rename(destination)
            if helper_backup.exists():
                helper_backup.rename(installed_computer_use_app)
            raise

    if LAUNCH_SERVICES_REGISTER.is_file():
        run(
            [
                str(LAUNCH_SERVICES_REGISTER),
                "-f",
                str(destination),
                str(installed_computer_use_app),
            ]
        )
    retire_stale_cached_computer_use_app()

    print(destination)
    print(installed_computer_use_app)


def main() -> int:
    args = parse_args()
    try:
        patch_app(
            args.source,
            args.destination,
            args.force,
            args.allow_signing_team_change,
        )
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"patch failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
