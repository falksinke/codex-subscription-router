"""Parse Electron ASAR integrity metadata without rewriting its JSON header."""

from __future__ import annotations

import hashlib
import json
import mmap
import re
import secrets
import struct
from collections.abc import Mapping
from pathlib import Path


_UINT32 = struct.Struct("<I")
_ASAR_SIZE_PICKLE_BYTES = 8
_PICKLE_HEADER_BYTES = 4
_STRING_LENGTH_BYTES = 4
_SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")
_INTEGRITY_SENTINEL = b"AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A"
_INTEGRITY_SLOT_VERSION = 1


def raw_header_bytes(asar_path: Path) -> bytes:
    """Return the exact UTF-8 JSON bytes stored in an ASAR header pickle."""
    archive_size = asar_path.stat().st_size
    if archive_size < 16:
        raise RuntimeError(f"ASAR header is truncated: {asar_path}")

    with asar_path.open("rb") as handle:
        prelude = handle.read(16)
        if len(prelude) != 16:
            raise RuntimeError(f"ASAR header is truncated: {asar_path}")
        (
            size_pickle_payload_size,
            header_pickle_size,
            header_pickle_payload_size,
            header_string_size,
        ) = struct.unpack("<IIII", prelude)

        if size_pickle_payload_size != _UINT32.size:
            raise RuntimeError(
                f"invalid ASAR size-pickle payload length in {asar_path}"
            )
        if header_pickle_size < 12 or header_pickle_size % 4 != 0:
            raise RuntimeError(f"invalid ASAR header-pickle length in {asar_path}")
        if header_pickle_payload_size != header_pickle_size - _PICKLE_HEADER_BYTES:
            raise RuntimeError(
                f"inconsistent ASAR header-pickle payload length in {asar_path}"
            )

        minimum_payload_size = _STRING_LENGTH_BYTES + header_string_size + 1
        if minimum_payload_size > header_pickle_payload_size:
            raise RuntimeError(f"ASAR JSON header exceeds its pickle in {asar_path}")
        padding_size = header_pickle_payload_size - minimum_payload_size
        if padding_size > 3:
            raise RuntimeError(f"invalid ASAR header-pickle padding in {asar_path}")

        header_end = _ASAR_SIZE_PICKLE_BYTES + header_pickle_size
        if header_end > archive_size:
            raise RuntimeError(f"ASAR header exceeds archive bounds: {asar_path}")

        raw_header = handle.read(header_string_size)
        terminator = handle.read(1)
        padding = handle.read(padding_size)
        if len(raw_header) != header_string_size:
            raise RuntimeError(f"ASAR JSON header is truncated: {asar_path}")
        if terminator != b"\0":
            raise RuntimeError(f"ASAR JSON header is not NUL-terminated: {asar_path}")
        if padding != b"\0" * padding_size:
            raise RuntimeError(f"ASAR header-pickle padding is invalid: {asar_path}")

    try:
        parsed_header = json.loads(raw_header.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"ASAR header is not valid UTF-8 JSON: {asar_path}"
        ) from error
    if not isinstance(parsed_header, dict) or not isinstance(
        parsed_header.get("files"), dict
    ):
        raise RuntimeError(
            f"ASAR header does not contain a files dictionary: {asar_path}"
        )
    return raw_header


def raw_header_sha256(asar_path: Path) -> str:
    """Hash the raw ASAR JSON header bytes required by Electron."""
    return hashlib.sha256(raw_header_bytes(asar_path)).hexdigest()


def integrity_dictionary_sha256(integrity: object) -> bytes:
    """Calculate Electron's v1 integrity-dictionary digest."""
    if not isinstance(integrity, Mapping) or not integrity:
        raise RuntimeError("ElectronAsarIntegrity must be a non-empty dictionary")

    digest = hashlib.sha256()
    keys = list(integrity)
    if not all(isinstance(key, str) and key for key in keys):
        raise RuntimeError("ElectronAsarIntegrity keys must be non-empty strings")
    for relative_path in sorted(keys):
        record = integrity[relative_path]
        if not isinstance(record, Mapping):
            raise RuntimeError(
                f"invalid ElectronAsarIntegrity record for {relative_path}"
            )
        algorithm = record.get("algorithm")
        header_hash = record.get("hash")
        if algorithm != "SHA256":
            raise RuntimeError(
                f"unsupported ElectronAsarIntegrity algorithm for {relative_path}"
            )
        if not isinstance(header_hash, str) or _SHA256_HEX.fullmatch(
            header_hash
        ) is None:
            raise RuntimeError(
                f"invalid ElectronAsarIntegrity hash for {relative_path}"
            )
        for value in (relative_path, algorithm, header_hash.lower()):
            digest.update(value.encode("utf-8"))
    return digest.digest()


def validated_source_integrity(
    info: dict[str, object],
    asar_path: Path,
) -> dict[str, object]:
    """Validate and return the exact source integrity dictionary."""
    integrity = info.get("ElectronAsarIntegrity")
    integrity_dictionary_sha256(integrity)
    if not isinstance(integrity, dict) or set(integrity) != {"Resources/app.asar"}:
        raise RuntimeError(
            "the verified source must contain one app.asar integrity record"
        )
    record = integrity["Resources/app.asar"]
    if not isinstance(record, dict):
        raise RuntimeError(
            "the verified source app.asar integrity record is invalid"
        )
    recorded_hash = record.get("hash")
    actual_hash = raw_header_sha256(asar_path)
    if not isinstance(recorded_hash, str) or not secrets.compare_digest(
        recorded_hash.lower(), actual_hash
    ):
        raise RuntimeError(
            "the verified source app.asar raw-header hash does not match its plist"
        )
    return integrity


def resolve_codex_framework(app: Path) -> tuple[Path, Path]:
    """Resolve the one concrete versioned Codex Framework binary."""
    framework = app / "Contents" / "Frameworks" / "Codex Framework.framework"
    versions = framework / "Versions"
    if not versions.is_dir():
        raise RuntimeError("Codex Framework versions directory was not found")
    candidates: list[Path] = []
    for version in versions.iterdir():
        if version.is_symlink() or not version.is_dir():
            continue
        binary = version / "Codex Framework"
        if binary.is_file() and not binary.is_symlink():
            candidates.append(binary)
    if len(candidates) != 1:
        raise RuntimeError(
            "expected one concrete Codex Framework version binary, "
            f"found {len(candidates)}"
        )
    return framework, candidates[0]


def patch_framework_integrity_digest(
    binary: Path,
    source_integrity: object,
    final_integrity: object,
) -> None:
    """Replace only the enabled v1 ASAR integrity dictionary digest."""
    expected_digest = integrity_dictionary_sha256(source_integrity)
    replacement_digest = integrity_dictionary_sha256(final_integrity)
    with binary.open("r+b") as handle, mmap.mmap(handle.fileno(), 0) as contents:
        sentinel_offset = contents.find(_INTEGRITY_SENTINEL)
        if sentinel_offset < 0 or contents.find(
            _INTEGRITY_SENTINEL, sentinel_offset + 1
        ) >= 0:
            raise RuntimeError(
                "expected exactly one Codex Framework ASAR integrity sentinel"
            )
        controls_offset = sentinel_offset + len(_INTEGRITY_SENTINEL)
        digest_offset = controls_offset + 2
        digest_end = digest_offset + hashlib.sha256().digest_size
        if digest_end > len(contents):
            raise RuntimeError("Codex Framework ASAR integrity slot is truncated")
        if contents[controls_offset] != 1:
            raise RuntimeError("Codex Framework ASAR integrity slot is disabled")
        if contents[controls_offset + 1] != _INTEGRITY_SLOT_VERSION:
            raise RuntimeError(
                "Codex Framework ASAR integrity slot version is unsupported"
            )
        if contents[digest_offset:digest_end] != expected_digest:
            raise RuntimeError(
                "Codex Framework ASAR integrity digest does not match "
                "the verified source dictionary"
            )
        contents[digest_offset:digest_end] = replacement_digest
        contents.flush()
        if contents[digest_offset:digest_end] != replacement_digest:
            raise RuntimeError("Codex Framework ASAR integrity digest write failed")
