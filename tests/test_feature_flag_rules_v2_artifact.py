"""Exercise the same archive verifier used after building a source distribution."""

import io
import tarfile
from pathlib import Path

import pytest

from tests.test_feature_flag_rules_v2_contract import CONTRACT_ROOT as CONTRACT
from tests.test_feature_flag_rules_v2_contract import _bin_module

CHECKSUMS = _bin_module("update-feature-flag-rules-v2-checksums")


@pytest.mark.parametrize("damage", [None, "missing", "tampered", "extra", "duplicate"])
def test_source_archive_integrity(tmp_path: Path, damage: str | None) -> None:
    path = tmp_path / "contract.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for source in CONTRACT.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(CONTRACT).as_posix()
            if relative == "schemas/definitions_entry.schema.json" and damage == "missing":
                continue
            data = source.read_bytes()
            if relative == "schemas/definitions_entry.schema.json" and damage == "tampered":
                data += b"\n"
            info = tarfile.TarInfo("harness/contracts/feature_flag_rules_v2/" + relative)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            if relative == "schemas/definitions_entry.schema.json" and damage == "duplicate":
                archive.addfile(info, io.BytesIO(data))
        if damage == "extra":
            archive.addfile(tarfile.TarInfo("harness/contracts/feature_flag_rules_v2/unindexed.json"), io.BytesIO(b""))
    if damage:
        with pytest.raises(ValueError, match="coverage differ|Checksum mismatch|Duplicate"):
            CHECKSUMS.verify_sdist(path)
    else:
        count, digest = CHECKSUMS.verify_sdist(path)
        assert count == len((CONTRACT / "SHA256SUMS").read_text().splitlines())
        assert len(digest) == 64
