"""Versioned YAML-parity sources and adapter-driven selection, not canonical IDs."""

import hashlib
from pathlib import Path

from .contracts import BoundaryError, decode_json, require

SUITE = "migration/yaml-parity-v1"


def migration_manifest(root):
    try:
        manifest = decode_json((Path(root) / SUITE / "manifest.json").read_bytes())
        require(manifest["format"] == "yaml-parity-v1", "invalid_source", "Unknown migration suite version")
        require(bool(manifest["sources"]), "invalid_source", "Empty migration suite")
        for source in manifest["sources"]:
            require(source["path"].startswith(SUITE + "/"), "invalid_source", "Source outside migration suite")
        return manifest
    except BoundaryError:
        raise
    except (OSError, KeyError, TypeError, AttributeError) as error:
        raise BoundaryError("invalid_source", "Missing or malformed migration manifest") from error


def migration_paths(root):
    return [source["path"] for source in migration_manifest(root)["sources"]]


def migration_cases(root, manifest, cases):
    """Attach a checked one-to-one legacy cross-reference without changing canonical IDs."""
    ledger = manifest["ledger"]
    require(ledger["path"] == SUITE + "/cases.json", "invalid_source", "Unexpected migration ledger path")
    data = (Path(root) / ledger["path"]).read_bytes()
    require(hashlib.sha256(data).hexdigest() == ledger["sha256"], "source_mismatch", "Migration ledger differs")
    rows = decode_json(data)
    require(len({r["id"] for r in rows}) == len(rows), "invalid_source", "Duplicate migration ID")
    require(len({r["legacy_id"] for r in rows}) == len(rows), "invalid_source", "Duplicate legacy mapping")
    paths = {c.source["path"] for c in cases}
    selected_rows = [r for r in rows if r["source"]["path"] in paths]
    require(len(selected_rows) == len(cases), "invalid_source", "Migration ledger/case count differs")
    for case in cases:
        matches = [r for r in selected_rows if r["source"] == case.source]
        require(len(matches) == 1, "invalid_source", "Missing or ambiguous migration source")
        row = matches[0]
        require(row["id"].startswith("migration:yaml-parity-v1:"), "invalid_source", "Invalid migration identity")
        case.id, case.migration = row["id"], row
    return cases


def selection(case, profile, supported_routes, explicit=False):
    """API candidates, then independent SDK features. Fixtures never filter candidates.

    Concrete API declarations claim only their own public capture operation.
    Explicit requests retain all prerequisites as gaps rather than exclusions.
    """
    requirements = case.migration
    routes = requirements["candidate_routes"]
    capabilities = requirements["sdk_capabilities"]
    missing_routes = sorted(set(routes) - set(supported_routes))
    missing_capabilities = sorted(set(capabilities) - set(profile.get("sdk_capabilities", [])))
    claims = {
        "capture_ai_v0": "/capture_ai",
        "capture_v1": "/capture",
        "capture_v0": "/capture",
        "flags_v2": "/get_feature_flag",
        "feature_flags_local_evaluation_v1": "/get_feature_flag",
    }
    claimed_routes = {claims[c] for c in capabilities if c in claims and c in profile.get("sdk_capabilities", [])}
    selected = explicit or (not missing_capabilities and set(missing_routes) <= claimed_routes)
    if missing_routes:
        reason = "Public candidate operation unavailable: " + ", ".join(missing_routes)
    elif missing_capabilities:
        state = "not declared" if "sdk_capabilities" not in profile else "unavailable"
        reason = f"SDK feature/API capability {state}: " + ", ".join(missing_capabilities)
    else:
        reason = "Public operation and independent SDK feature/API declarations match"
    return {
        "selected": selected,
        "reason": reason,
        "missing_routes": missing_routes,
        "missing_sdk_capabilities": missing_capabilities,
        "explicit": explicit,
    }
