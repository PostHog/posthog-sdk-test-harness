"""Immutable packaged specification inputs; explicit checkout overrides stay local."""

import hashlib
import json
import re
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path, PurePosixPath

from .contracts import BoundaryError, Contracts, decode_json, require

MANIFEST = "bundle-manifest.json"
FORMAT = "posthog-specs-bundle-v1"


def identity(manifest):
    content = {key: value for key, value in manifest.items() if key != "bundle_sha256"}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_bundle(root):
    """Verify the whole bundle, including generated schemas, before discovery or RPC."""
    root = Path(root).resolve()
    try:
        manifest = decode_json((root / MANIFEST).read_bytes())
        require(manifest["format"] == FORMAT, "bundle_mismatch", "Unknown specs bundle format")
        require(manifest["bundle_sha256"] == identity(manifest), "bundle_mismatch", "Specs bundle identity differs")
        source = manifest["source"]
        require(
            type(source["dirty"]) is bool
            and isinstance(source["commit"], str)
            and re.fullmatch(r"[0-9a-f]{40}", source["commit"]),
            "bundle_mismatch",
            "Invalid specs source provenance",
        )
        expected = manifest["files"]
        require(isinstance(expected, dict) and bool(expected), "bundle_mismatch", "Empty specs bundle")
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        require(actual == set(expected) | {MANIFEST}, "bundle_mismatch", "Specs bundle file inventory differs")
        for name, digest in expected.items():
            path = PurePosixPath(name)
            require(
                not path.is_absolute() and ".." not in path.parts and path.as_posix() == name,
                "bundle_mismatch",
                "Invalid bundle resource path",
            )
            resource = root / name
            require(
                resource.resolve().is_relative_to(root) and not resource.is_symlink(),
                "bundle_mismatch",
                "Bundle resource escapes its package",
            )
            require(
                hashlib.sha256(resource.read_bytes()).hexdigest() == digest,
                "bundle_mismatch",
                f"Bundle resource differs: {name}",
            )
        contracts = Contracts(root / "contracts/v2")
        require(
            manifest["catalog_sha256"] == contracts.catalog_hash,
            "bundle_mismatch",
            "Bundle and contract identities differ",
        )
        return manifest
    except BoundaryError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise BoundaryError("invalid_bundle", "Missing or malformed packaged specs bundle") from error


@contextmanager
def specification_inputs(specs=None):
    """Yield an explicit developer checkout or the verified installed bundle."""
    if specs is not None:
        yield Path(specs), None
        return
    resource = files("posthog_test_harness.v2").joinpath("_bundle")
    if not resource.is_dir():
        raise BoundaryError("missing_bundle", "No packaged specs bundle; build the distribution or provide --specs")
    with as_file(resource) as root:
        yield root, validate_bundle(root)
