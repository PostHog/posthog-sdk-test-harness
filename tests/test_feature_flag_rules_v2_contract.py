import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

CONTRACT_ROOT = Path(__file__).parents[1] / "contracts" / "feature_flag_rules_v2"
MANIFEST_PATH = CONTRACT_ROOT / "manifest.json"
SCHEMA_PATH = CONTRACT_ROOT / "schemas" / "config.schema.json"
REGISTRY_PATH = CONTRACT_ROOT / "registries" / "literals.json"
CHECKSUMS_PATH = CONTRACT_ROOT / "SHA256SUMS"


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _manifest() -> dict[str, Any]:
    return _load_json(MANIFEST_PATH)


def _artifact_paths(manifest: dict[str, Any]) -> set[str]:
    return {artifact["path"] for artifact in manifest["artifacts"]}


def _fixture_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [artifact for artifact in manifest["artifacts"] if artifact["kind"] == "fixture"]


def _walk_json(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _all_errors(error: ValidationError) -> Iterator[ValidationError]:
    yield error
    for child in error.context:
        yield from _all_errors(child)


def _json_pointer(path: Any) -> str:
    parts = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
    return "" if not parts else "/" + "/".join(parts)


def _property_literals(schema: dict[str, Any], name: str) -> set[Any]:
    literals: set[Any] = set()
    for node in _walk_json(schema):
        if not isinstance(node, dict):
            continue
        properties = node.get("properties")
        if not isinstance(properties, dict):
            continue
        property_schema = properties.get(name)
        if not isinstance(property_schema, dict):
            continue
        if "const" in property_schema:
            literals.add(property_schema["const"])
        literals.update(property_schema.get("enum", []))
        for child in _walk_json(property_schema):
            if isinstance(child, dict):
                literals.update(child.get("enum", []))
    return literals


def _schema_fields(value: Any) -> set[str]:
    fields: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            fields.update(properties)
            for property_schema in properties.values():
                fields.update(_schema_fields(property_schema))
        for key, child in value.items():
            if key != "properties":
                fields.update(_schema_fields(child))
    elif isinstance(value, list):
        for child in value:
            fields.update(_schema_fields(child))
    return fields


def test_manifest_references_every_contract_file_once() -> None:
    manifest = _manifest()
    artifact_paths = [artifact["path"] for artifact in manifest["artifacts"]]
    assert len(artifact_paths) == len(set(artifact_paths))

    actual_paths = {
        path.relative_to(CONTRACT_ROOT).as_posix() for path in CONTRACT_ROOT.rglob("*") if path.is_file()
    }
    expected_paths = _artifact_paths(manifest) | {"manifest.json", "SHA256SUMS"}
    assert actual_paths == expected_paths


def test_fixture_ids_are_unique_and_match_fixture_files() -> None:
    fixtures = _fixture_entries(_manifest())
    fixture_ids = [fixture["fixture_id"] for fixture in fixtures]
    assert len(fixture_ids) == len(set(fixture_ids))

    manifest_fixture_paths = {fixture["path"] for fixture in fixtures}
    actual_fixture_paths = {
        path.relative_to(CONTRACT_ROOT).as_posix()
        for path in (CONTRACT_ROOT / "fixtures").rglob("*.json")
    }
    assert manifest_fixture_paths == actual_fixture_paths


def test_schema_is_valid_draft_2020_12_and_has_no_wire_defaults() -> None:
    manifest = _manifest()
    schema = _load_json(SCHEMA_PATH)

    assert schema["$schema"] == manifest["schema_dialect"]
    Draft202012Validator.check_schema(schema)
    assert all("default" not in node for node in _walk_json(schema) if isinstance(node, dict))


def test_semantic_objects_are_closed() -> None:
    schema = _load_json(SCHEMA_PATH)
    for node in _walk_json(schema):
        if not isinstance(node, dict) or node.get("type") != "object":
            continue
        if "x-posthog-open-object" in node:
            assert node.get("additionalProperties") is not False
        else:
            assert node.get("additionalProperties") is False


def test_literal_registry_matches_config_schema() -> None:
    manifest = _manifest()
    schema = _load_json(SCHEMA_PATH)
    registry = _load_json(REGISTRY_PATH)

    component_versions = {artifact["path"]: artifact.get("version") for artifact in manifest["artifacts"]}
    assert registry["registry_version"] == component_versions["registries/literals.json"]
    assert schema["$id"].endswith(":" + component_versions["schemas/config.schema.json"])
    assert registry["config_versions"]["v2"] == manifest["contract"]["config_version"]
    assert _property_literals(schema, "version") == {registry["config_versions"]["v2"]}
    assert _property_literals(schema, "release_type") == {
        item["value"] for item in registry["release_types"]
    }
    assert _property_literals(schema, "return_type") == {
        item["value"] for item in registry["return_types"]
    }
    assert _property_literals(schema, "on_rollout_miss") == set(registry["rollout_miss_policies"])
    assert _property_literals(schema, "assignment_algorithm") == set(registry["assignment_algorithms"])
    assert _property_literals(schema, "assign_variant_by") == set(registry["assignment_targets"])
    assert _property_literals(schema, "type") == set(registry["property_types"])
    assert _property_literals(schema, "operator") == set(registry["property_operators"])

    assert _schema_fields(schema) == set(registry["fields"])


@pytest.mark.parametrize(
    "registry_key, pattern",
    [("reason_codes", r"^[a-z][a-z0-9_]*$"), ("warning_codes", r"^[A-Z][A-Z0-9_]*$")],
)
def test_registry_codes_are_unique_and_machine_readable(registry_key: str, pattern: str) -> None:
    values = _load_json(REGISTRY_PATH)[registry_key]
    assert len(values) == len(set(values))
    assert all(re.fullmatch(pattern, value) for value in values)


def test_valid_config_fixtures_match_the_schema() -> None:
    validator = Draft202012Validator(_load_json(SCHEMA_PATH), format_checker=FormatChecker())
    valid_fixtures = [entry for entry in _fixture_entries(_manifest()) if entry["expected"] == "valid"]

    for fixture in valid_fixtures:
        errors = list(validator.iter_errors(_load_json(CONTRACT_ROOT / fixture["path"])))
        assert not errors, f"{fixture['fixture_id']}: {errors}"


def test_invalid_config_fixtures_fail_for_the_declared_reason() -> None:
    validator = Draft202012Validator(_load_json(SCHEMA_PATH), format_checker=FormatChecker())
    invalid_fixtures = [entry for entry in _fixture_entries(_manifest()) if entry["expected"] == "invalid"]

    for fixture in invalid_fixtures:
        errors = list(validator.iter_errors(_load_json(CONTRACT_ROOT / fixture["path"])))
        assert errors, f"{fixture['fixture_id']} unexpectedly passed"

        expected = fixture["expected_failure"]
        flattened_errors = [nested for error in errors for nested in _all_errors(error)]
        matches = [
            error
            for error in flattened_errors
            if error.validator == expected["keyword"]
            and _json_pointer(error.absolute_path) == expected["instance_path"]
            and expected.get("message_contains", "") in error.message
        ]
        assert matches, (
            f"{fixture['fixture_id']} did not fail as declared. "
            f"Actual errors: "
            f"{[(error.validator, _json_pointer(error.absolute_path), error.message) for error in flattened_errors]}"
        )


def test_checksum_index_is_complete_and_valid() -> None:
    manifest = _manifest()
    checksum_text = CHECKSUMS_PATH.read_text(encoding="utf-8")
    assert checksum_text.endswith("\n")

    entries: dict[str, str] = {}
    for line in checksum_text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\n]+)", line)
        assert match is not None
        digest, relative_path = match.groups()
        assert relative_path not in entries
        entries[relative_path] = digest

    expected_paths = _artifact_paths(manifest) | {"manifest.json"}
    assert set(entries) == expected_paths
    assert list(entries) == sorted(entries)

    for relative_path, expected_digest in entries.items():
        actual_digest = hashlib.sha256((CONTRACT_ROOT / relative_path).read_bytes()).hexdigest()
        assert actual_digest == expected_digest, relative_path
