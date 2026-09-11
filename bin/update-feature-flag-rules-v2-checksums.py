#!/usr/bin/env python3
"""Regenerate the Rules v2 checksum index from raw contract file bytes."""

import argparse
import hashlib
import re
import tarfile
from pathlib import Path

CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "contracts" / "feature_flag_rules_v2"


def verify_sdist(path: Path) -> tuple[int, str]:
    """Verify the entire contract inside a source archive without extracting it."""
    with tarfile.open(path, "r:gz") as archive:
        files: dict[str, bytes] = {}
        roots: set[str] = set()
        marker = "/contracts/feature_flag_rules_v2/"
        for member in archive.getmembers():
            if marker not in member.name or member.isdir():
                continue
            root, relative = member.name.split(marker, 1)
            roots.add(root)
            if not member.isfile() or relative in files:
                raise ValueError(f"Duplicate or non-regular contract member: {member.name}")
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError(f"Unreadable contract member: {member.name}")
            files[relative] = handle.read()
    if len(roots) != 1 or "SHA256SUMS" not in files:
        raise ValueError("Archive must contain one complete contract package")
    index = files["SHA256SUMS"]
    if index != (CONTRACT_ROOT / "SHA256SUMS").read_bytes():
        raise ValueError("Archive checksum index differs from this checkout")
    # The contract tests validate the checkout index itself; the archive only has to match it exactly.
    entries = {path: digest for digest, path in re.findall(r"^([0-9a-f]{64})  (.+)$", index.decode("utf-8"), re.M)}
    if set(files) != set(entries) | {"SHA256SUMS"}:
        raise ValueError("Archive and checksum file coverage differ")
    for relative, digest in entries.items():
        if hashlib.sha256(files[relative]).hexdigest() != digest:
            raise ValueError(f"Checksum mismatch: {relative}")
    return len(entries), hashlib.sha256(index).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-sdist", type=Path, help="Verify the built source archive against this checkout")
    args = parser.parse_args()
    if args.verify_sdist:
        count, digest = verify_sdist(args.verify_sdist)
        print(f"Verified {count} indexed contract files; SHA256SUMS sha256={digest}")
        return
    checksum_path = CONTRACT_ROOT / "SHA256SUMS"
    paths = sorted(
        (path for path in CONTRACT_ROOT.rglob("*") if path.is_file() and path != checksum_path),
        key=lambda path: path.relative_to(CONTRACT_ROOT).as_posix().encode("utf-8"),
    )
    contents = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(CONTRACT_ROOT).as_posix()}\n"
        for path in paths
    )
    checksum_path.write_bytes(contents.encode("utf-8"))


if __name__ == "__main__":
    main()
