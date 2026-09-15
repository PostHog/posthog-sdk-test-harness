"""Exercise the same archive verifier used after building a source distribution."""

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from tests.test_feature_flag_rules_v2_contract import CONTRACT_ROOT as CONTRACT
from tests.test_feature_flag_rules_v2_contract import _bin_module

CHECKSUMS = _bin_module("update-feature-flag-rules-v2-checksums")
TARGET = "schemas/definitions_entry.schema.json"


@pytest.mark.parametrize(
    "damage, message",
    [
        (None, ""),
        ("missing", "coverage differ"),
        ("tampered", "Checksum mismatch"),
        ("extra", "coverage differ"),
        ("duplicate", "Duplicate"),
        ("restamped", "index differs from this checkout"),
    ],
)
def test_source_archive_integrity(tmp_path: Path, damage: str | None, message: str) -> None:
    files = {p.relative_to(CONTRACT).as_posix(): p.read_bytes() for p in sorted(CONTRACT.rglob("*")) if p.is_file()}
    if damage == "missing":
        del files[TARGET]
    if damage in ["tampered", "restamped"]:
        files[TARGET] += b"\n"
    if damage == "restamped":
        # An archive from another checkout indexes its own bytes; only this checkout's index is trusted.
        files["SHA256SUMS"] = "".join(
            f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in files.items() if name != "SHA256SUMS"
        ).encode("utf-8")
    if damage == "extra":
        files["unindexed.json"] = b""
    path = tmp_path / "contract.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo("harness/contracts/feature_flag_rules_v2/" + name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            if name == TARGET and damage == "duplicate":
                archive.addfile(info, io.BytesIO(data))
    if damage:
        with pytest.raises(ValueError, match=message):
            CHECKSUMS.verify_sdist(path)
    else:
        count, digest = CHECKSUMS.verify_sdist(path)
        assert count == len(files) - 1
        assert digest == hashlib.sha256((CONTRACT / "SHA256SUMS").read_bytes()).hexdigest()
