#!/usr/bin/env python3
"""Regenerate the Rules v2 checksum index from raw contract file bytes."""

import hashlib
from pathlib import Path

CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "contracts" / "feature_flag_rules_v2"


def main() -> None:
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
