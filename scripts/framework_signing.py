"""Re-sign the minimum Codex Framework code needed for library validation."""

from __future__ import annotations

import plistlib
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


CODE_SIGNATURE_RUNTIME_FLAG = 0x10000
EXPECTED_RUNTIME_VERSION = "26.0.0"
DISABLE_LIBRARY_VALIDATION = "com.apple.security.cs.disable-library-validation"
TEAM_SCOPED_ENTITLEMENTS = (
    "com.apple.application-identifier",
    "com.apple.developer.team-identifier",
    "com.apple.security.application-groups",
    "keychain-access-groups",
)
PROTECTED_HELPER_IDENTIFIERS = {
    "Codex (Alerts).app": "com.openai.codex.framework.AlertNotificationService",
    "Codex (Aperitif Alerts).app": (
        "com.openai.codex.framework.AlertNotificationService"
    ),
    "Codex (Renderer).app": "com.openai.codex.helper.renderer",
    "Codex (Aperitif Renderer).app": "com.openai.codex.helper.renderer",
    "Codex (GPU).app": "com.openai.codex.helper",
    "Codex (Aperitif).app": "com.openai.codex.helper",
    "Codex (Aperitif GPU).app": "com.openai.codex.helper",
}
SERVICE_HELPER_NAME = "Codex (Service).app"
EXPECTED_HELPER_NAMES = set(PROTECTED_HELPER_IDENTIFIERS) | {SERVICE_HELPER_NAME}


@dataclass(frozen=True)
class SigningMetadata:
    identifier: str
    flags: int
    runtime_version: str | None
    entitlements: dict[str, object] | None


@dataclass(frozen=True)
class SigningTarget:
    path: Path
    executable: Path
    metadata: SigningMetadata


@dataclass(frozen=True)
class FrameworkSigningPlan:
    framework: SigningTarget
    library: SigningTarget
    helpers: tuple[SigningTarget, ...]
    service: Path
    service_cdhash: str


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _codesign_details(path: Path) -> str:
    result = subprocess.run(
        ["codesign", "--display", "--verbose=4", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout + result.stderr


def _metadata_value(details: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}=(.+)$", details, re.MULTILINE)
    return match.group(1).strip() if match else None


def _signature_identifier(path: Path) -> str:
    identifier = _metadata_value(_codesign_details(path), "Identifier")
    if identifier is None:
        raise RuntimeError(f"could not read code-signing identifier from {path}")
    return identifier


def _signature_flags_and_runtime(path: Path) -> tuple[int, str | None]:
    details = _codesign_details(path)
    flags_match = re.search(
        r"^CodeDirectory .* flags=0x([0-9a-fA-F]+)",
        details,
        re.MULTILINE,
    )
    if flags_match is None:
        raise RuntimeError(f"could not read code-signing flags from {path}")
    flags = int(flags_match.group(1), 16)
    unsupported_flags = flags & ~CODE_SIGNATURE_RUNTIME_FLAG
    if unsupported_flags:
        raise RuntimeError(
            f"{path} uses unsupported code-signing flags: 0x{flags:x}"
        )
    runtime_version = _metadata_value(details, "Runtime Version")
    if bool(flags & CODE_SIGNATURE_RUNTIME_FLAG) != (runtime_version is not None):
        raise RuntimeError(f"inconsistent hardened-runtime metadata on {path}")
    return flags, runtime_version


def _signature_cdhash(path: Path) -> str:
    cdhash = _metadata_value(_codesign_details(path), "CDHash")
    if cdhash is None:
        raise RuntimeError(f"could not read signature hash from {path}")
    return cdhash


def _sanitized_entitlements(executable: Path) -> dict[str, object] | None:
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


def _bundle_main_executable(bundle: Path) -> Path:
    plist_path = bundle / "Contents" / "Info.plist"
    executable_root = bundle / "Contents" / "MacOS"
    if bundle.suffix == ".framework":
        plist_path = bundle / "Versions" / "Current" / "Resources" / "Info.plist"
        executable_root = bundle / "Versions" / "Current"
    if not plist_path.is_file():
        raise RuntimeError(f"bundle has no Info.plist: {bundle}")
    with plist_path.open("rb") as handle:
        executable_name = plistlib.load(handle).get("CFBundleExecutable")
    if not isinstance(executable_name, str) or executable_name == "":
        raise RuntimeError(f"bundle has no main executable: {bundle}")
    executable = executable_root / executable_name
    if not executable.is_file():
        raise RuntimeError(f"bundle main executable was not found: {executable}")
    return executable


def _capture_target(path: Path, executable: Path) -> SigningTarget:
    flags, runtime_version = _signature_flags_and_runtime(path)
    return SigningTarget(
        path=path,
        executable=executable,
        metadata=SigningMetadata(
            identifier=_signature_identifier(path),
            flags=flags,
            runtime_version=runtime_version,
            entitlements=_sanitized_entitlements(executable),
        ),
    )


def _validate_pinned_runtime(target: SigningTarget) -> None:
    metadata = target.metadata
    if metadata.flags != CODE_SIGNATURE_RUNTIME_FLAG:
        raise RuntimeError(
            f"{target.path} has unexpected code-signing flags: 0x{metadata.flags:x}"
        )
    if metadata.runtime_version != EXPECTED_RUNTIME_VERSION:
        raise RuntimeError(
            f"{target.path} has unexpected runtime version: "
            f"{metadata.runtime_version!r}"
        )


def capture_framework_signing_plan(
    framework: Path,
    framework_binary: Path,
) -> FrameworkSigningPlan:
    """Validate and capture metadata from the authenticated pristine framework."""
    version_root = framework_binary.parent
    helpers_root = version_root / "Helpers"
    if not helpers_root.is_dir():
        raise RuntimeError("Codex Framework helpers directory was not found")
    helper_names = {
        candidate.name
        for candidate in helpers_root.iterdir()
        if candidate.is_dir() and candidate.suffix == ".app"
    }
    if helper_names != EXPECTED_HELPER_NAMES:
        raise RuntimeError(
            "unexpected Codex Framework helper scope: "
            f"expected {sorted(EXPECTED_HELPER_NAMES)!r}, found {sorted(helper_names)!r}"
        )

    protected_helpers: list[SigningTarget] = []
    for name, expected_identifier in PROTECTED_HELPER_IDENTIFIERS.items():
        bundle = helpers_root / name
        nested_bundles = [
            candidate
            for candidate in bundle.rglob("*")
            if candidate.is_dir()
            and candidate.suffix in {".app", ".appex", ".framework", ".xpc"}
        ]
        if nested_bundles:
            raise RuntimeError(f"protected helper contains nested code: {bundle}")
        target = _capture_target(bundle, _bundle_main_executable(bundle))
        if target.metadata.identifier != expected_identifier:
            raise RuntimeError(
                f"unexpected signing identifier on {bundle}: "
                f"{target.metadata.identifier!r}"
            )
        _validate_pinned_runtime(target)
        if target.metadata.entitlements and target.metadata.entitlements.get(
            DISABLE_LIBRARY_VALIDATION
        ):
            raise RuntimeError(
                f"protected helper unexpectedly disables library validation: {bundle}"
            )
        protected_helpers.append(target)

    service = helpers_root / SERVICE_HELPER_NAME
    service_entitlements = _sanitized_entitlements(_bundle_main_executable(service))
    if not service_entitlements or service_entitlements.get(
        DISABLE_LIBRARY_VALIDATION
    ) is not True:
        raise RuntimeError(
            "Codex (Service).app no longer has its upstream library-validation exception"
        )

    library_path = version_root / "Libraries" / "libaperitif.dylib"
    if not library_path.is_file():
        raise RuntimeError("Codex Framework libaperitif.dylib was not found")
    library = _capture_target(library_path, library_path)
    if library.metadata.identifier != "libaperitif":
        raise RuntimeError(
            "unexpected libaperitif signing identifier: "
            f"{library.metadata.identifier!r}"
        )
    _validate_pinned_runtime(library)
    if library.metadata.entitlements and library.metadata.entitlements.get(
        DISABLE_LIBRARY_VALIDATION
    ):
        raise RuntimeError("libaperitif unexpectedly disables library validation")

    framework_target = _capture_target(framework, framework_binary)
    return FrameworkSigningPlan(
        framework=framework_target,
        library=library,
        helpers=tuple(protected_helpers),
        service=service,
        service_cdhash=_signature_cdhash(service),
    )


def _sign_target(target: SigningTarget, identity: str) -> None:
    metadata = target.metadata
    command = [
        "codesign",
        "--force",
        "--sign",
        identity,
        "--timestamp=none",
        "--identifier",
        metadata.identifier,
    ]
    if metadata.flags & CODE_SIGNATURE_RUNTIME_FLAG:
        if metadata.runtime_version is None:
            raise RuntimeError(f"missing runtime version for {target.path}")
        command.extend(
            ("--options", "runtime", "--runtime-version", metadata.runtime_version)
        )
    if metadata.entitlements is None:
        _run([*command, str(target.path)])
        return
    with tempfile.TemporaryDirectory(prefix=".codesign-entitlements-") as temporary:
        entitlements_path = Path(temporary) / "entitlements.plist"
        with entitlements_path.open("wb") as handle:
            plistlib.dump(
                metadata.entitlements,
                handle,
                fmt=plistlib.FMT_XML,
                sort_keys=True,
            )
        _run(
            [*command, "--entitlements", str(entitlements_path), str(target.path)]
        )


def verify_apple_signed_code(
    path: Path,
    expected_identifier: str,
    expected_team: str,
) -> None:
    """Verify integrity plus the selected Team ID under an Apple trust anchor."""
    _run(["codesign", "--verify", "--deep", "--strict", str(path)])
    details = _codesign_details(path)
    identifier = _metadata_value(details, "Identifier")
    team = _metadata_value(details, "TeamIdentifier")
    if identifier != expected_identifier:
        raise RuntimeError(
            f"unexpected signing identifier on {path}: {identifier!r}"
        )
    if team != expected_team:
        raise RuntimeError(f"unexpected signing team on {path}: {team!r}")
    requirement = (
        f'=anchor apple generic and certificate leaf[subject.OU] = "{expected_team}"'
    )
    _run(
        [
            "codesign",
            "--verify",
            "--deep",
            "--strict",
            "--test-requirement",
            requirement,
            str(path),
        ]
    )


def _verify_target(target: SigningTarget, expected_team: str) -> None:
    verify_apple_signed_code(
        target.path,
        target.metadata.identifier,
        expected_team,
    )
    actual = _capture_target(target.path, target.executable)
    if actual.metadata != target.metadata:
        raise RuntimeError(f"signing metadata changed unexpectedly on {target.path}")


def verify_untouched_service(plan: FrameworkSigningPlan) -> None:
    if _signature_cdhash(plan.service) != plan.service_cdhash:
        raise RuntimeError("Codex (Service).app signature changed unexpectedly")
    entitlements = _sanitized_entitlements(_bundle_main_executable(plan.service))
    if not entitlements or entitlements.get(DISABLE_LIBRARY_VALIDATION) is not True:
        raise RuntimeError(
            "Codex (Service).app lost its upstream library-validation exception"
        )


def sign_framework_tree(
    plan: FrameworkSigningPlan,
    identity: str,
    expected_team: str,
) -> None:
    """Sign the explicit dependency boundary inside-out, leaving Service alone."""
    _sign_target(plan.library, identity)
    _verify_target(plan.library, expected_team)
    for helper in plan.helpers:
        _sign_target(helper, identity)
        _verify_target(helper, expected_team)
    verify_untouched_service(plan)
    _sign_target(plan.framework, identity)
    _verify_target(plan.framework, expected_team)
    verify_untouched_service(plan)
