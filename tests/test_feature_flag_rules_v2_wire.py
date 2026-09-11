"""Contract meta-tests only: these do not execute an SDK, evaluator or harness action."""

import copy
import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

ROOT = Path(__file__).parents[1] / "contracts" / "feature_flag_rules_v2"
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
ARTIFACTS = [a for a in MANIFEST["artifacts"] if a["kind"] == "fixture_set"]
SETS = {Path(a["path"]).stem: json.loads((ROOT / a["path"]).read_text()) for a in ARTIFACTS}
SCHEMAS = {
    a["path"]: json.loads((ROOT / a["path"]).read_text()) for a in MANIFEST["artifacts"] if a["kind"] == "schema"
}
REGISTRY = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in SCHEMAS.values())
LITERALS = json.loads((ROOT / "registries/literals.json").read_text())
MATRIX = json.loads((ROOT / "rules/response_presence.json").read_text())["rows"]
CASES = [(name, case) for name, data in SETS.items() for case in data["cases"]]
PRODUCER_DIGEST = "1dd97730c746bc4534c4f47eebd82dac0cc67ceb1e4c53e7540960e7f74b00d9"


def validator(path: str) -> Draft202012Validator:
    return Draft202012Validator(SCHEMAS[path], registry=REGISTRY, format_checker=FormatChecker())


def pointer(path: Any) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in path)


def parent(value: Any, path: str) -> tuple[Any, Any]:
    assert path.startswith("/")
    parts = [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]
    for part in parts[:-1]:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value, int(parts[-1]) if isinstance(value, list) else parts[-1]


def materialize(group: str, case: dict[str, Any]) -> Any:
    value = copy.deepcopy(SETS[group]["templates"][case["template"]])
    for path in case.get("remove", []):
        container, member = parent(value, path)
        del container[member]
    for path, new_value in case.get("set", {}).items():
        container, member = parent(value, path)
        container[member] = copy.deepcopy(new_value)
    return value


def flattened(error: ValidationError) -> Iterator[ValidationError]:
    yield error
    for child in error.context:
        yield from flattened(child)


def schema_errors(path: str, value: Any, layer: str = "schema") -> list[dict[str, Any]]:
    return [
        {"layer": layer, "keyword": e.validator, "instance_path": pointer(e.absolute_path), "message": e.message}
        for error in validator(path).iter_errors(value)
        for e in flattened(error)
    ]


def seed_errors(value: Any, path: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Inspect the full response/event, including opaque objects and arrays, before projection."""
    errors = []
    if isinstance(value, dict):
        for field, child in value.items():
            # Cover canonical seed, holdout_seed, assignmentSeed, and event-property spellings.
            normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", field).lower().replace("-", "_").lstrip("$")
            if normalized == "seed" or normalized.endswith("_seed"):
                errors.append({"layer": "seed", "keyword": "assignment_seed", "instance_path": pointer((*path, field))})
            errors.extend(seed_errors(child, (*path, field)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(seed_errors(child, (*path, index)))
    return errors


def response_errors(value: dict[str, Any]) -> list[dict[str, Any]]:
    errors = schema_errors("schemas/flags_response_v3.schema.json", value)
    # Keep the schema copy and the additional presence constraints independently visible.
    errors += schema_errors("schemas/flags_response_v3_presence.schema.json", value, "presence")
    errors += seed_errors(value)
    for key, record in value["flags"].items():
        if record.get("key") != key:
            errors.append({"layer": "semantic", "keyword": "map_key", "instance_path": pointer(["flags", key, "key"])})
        if record.get("failed") is True and value["errorsWhileComputingFlags"] is not True:
            errors.append(
                {"layer": "semantic", "keyword": "failure_envelope", "instance_path": "/errorsWhileComputingFlags"}
            )
    return errors


def event_errors(event: dict[str, Any], path: str) -> list[dict[str, Any]]:
    errors = seed_errors(event)
    direct = path == "schemas/experiment_exposure_properties.schema.json"
    assert event["event"] == ("$experiment_exposure" if direct else "$feature_flag_called")
    props = event["properties"]
    fields = SCHEMAS[path]["properties"]
    context = {k: v for k, v in props.items() if k in fields}
    errors += schema_errors(path, context)
    if direct:
        for field in ["$feature_flag_holdout_id", "$feature_flag_forced_variant"]:
            if field in props:
                errors.append(
                    {
                        "layer": "semantic",
                        "keyword": "forbidden_context",
                        "instance_path": pointer(["properties", field]),
                    }
                )
    return errors


def assert_failure(errors: list[dict[str, Any]], expected: dict[str, Any]) -> None:
    assert any(
        all(e.get(k) == expected[k] for k in ["layer", "keyword", "instance_path"])
        and expected.get("message_contains", "") in e.get("message", "")
        for e in errors
    ), (expected, errors)


@pytest.mark.parametrize(
    "group, case", [(g, c) for g, c in CASES if g != "readers"], ids=[c["id"] for g, c in CASES if g != "readers"]
)
def test_wire_producer_fixtures(group: str, case: dict[str, Any]) -> None:
    value = materialize(group, case)
    if group == "responses":
        errors = response_errors(value)
    elif group.endswith("_transport"):
        errors = event_errors(value, SETS[group]["schema"])
    else:
        errors = schema_errors(SETS[group]["schema"], value)
        if group in ["calls", "exposures"]:
            errors += seed_errors(value)
    if case["expected"] == "valid":
        assert not errors, errors
    else:
        assert case["expected"] == "invalid"
        assert_failure(errors, case["expected_failure"])
        # Matrix-only and leak cases must demonstrate the exact schema's deliberate gaps.
        if case["expected_failure"]["layer"] in ["presence", "seed", "semantic"] and group == "responses":
            # A forbidden failed result with terminal metadata also violates failed_rules.
            if "forbidden_failed" not in case["id"]:
                assert not schema_errors(SETS[group]["schema"], value)


def has_split_context(record: dict[str, Any]) -> bool:
    metadata = record.get("metadata", {})
    return (
        metadata.get("config_version") == 2
        and record.get("reason", {}).get("code") == "experiment_split"
        and metadata.get("rule_type") == "experiment"
        and isinstance(metadata.get("rule_id"), str)
        and FormatChecker().conforms(metadata["rule_id"], "uuid")
        and Draft202012Validator({"type": "integer"}).is_valid(metadata.get("experiment_id"))
        and isinstance(metadata.get("variant_key"), str)
        and re.fullmatch(r"[a-zA-Z0-9_-]+", metadata["variant_key"]) is not None
        and "holdout_id" not in metadata
        and not record.get("failed", False)
    )


def known_type_error(value: Any, shape: dict[str, Any]) -> bool:
    """Only known JSON types: missing tuple members and unknown keys are handled separately."""
    if "$ref" in shape:
        shape = SCHEMAS["schemas/flags_response_v3.schema.json"]["$defs"][shape["$ref"].rsplit("/", 1)[-1]]
    expected_type = shape.get("type")
    if expected_type and not Draft202012Validator({"type": expected_type}).is_valid(value):
        return True
    if isinstance(value, dict):
        return any(
            k in shape.get("properties", {}) and known_type_error(v, shape["properties"][k]) for k, v in value.items()
        )
    # Enum-only fields still have a known JSON type, even if their future literals are unknown.
    if "enum" in shape:
        members = shape["enum"]
        if all(type(v) is int for v in members):
            return not Draft202012Validator({"type": "integer"}).is_valid(value)
        if all(isinstance(v, str) for v in members):
            return not isinstance(value, str)
    if shape.get("const") is True:
        return type(value) is not bool
    return False


def check_reader_expectation(response: dict[str, Any], expected: dict[str, Any]) -> None:
    """Check the fixture's declared interpretation; this is not an SDK compliance run."""
    record = response["flags"].get(expected["requested_key"])
    if record is None:
        assert expected["error_code"] == "FLAG_NOT_FOUND"
        assert expected["value"] == expected["caller_default"]
        assert expected["config_version"] is None
        assert expected["experiment_exposure"] is False
        return
    if "value" not in record:
        # No context is carried forward from the old-server branch, even when fields look complete.
        assert expected["value"] == (record.get("variant") if record.get("variant") is not None else record["enabled"])
        assert expected["config_version"] == 1
        assert expected["experiment_exposure"] is False
        assert expected["error_code"] is None
        return
    shape = SCHEMAS["schemas/flags_response_v3.schema.json"]["$defs"]["record"]
    if known_type_error(record, shape):
        assert expected["error_code"] == "PARSE_ERROR"
        assert expected["value"] == expected["caller_default"]
        assert expected["experiment_exposure"] is False
        assert expected["config_version"] is None
        return
    assert expected["config_version"] == record["metadata"].get("config_version")
    value = record["value"]
    assert expected["value"] == (expected["caller_default"] if value is None or record.get("failed") else value)
    assert expected["experiment_exposure"] is has_split_context(record)
    if record["metadata"].get("config_version") == 2:
        mapping = LITERALS["openfeature_mapping"]["reason_codes"]
        assert expected["error_code"] == mapping[record["reason"]["code"]].get("error_code")
    else:
        assert expected["error_code"] == ("GENERAL" if record.get("failed") else None)


@pytest.mark.parametrize("case", SETS["readers"]["cases"], ids=lambda c: c["id"])
def test_tolerant_reader_fixture_expectations(case: dict[str, Any]) -> None:
    value = materialize("readers", case)
    assert not seed_errors(value)
    check_reader_expectation(value, case["reader"])
    if case["producer_expected"] == "invalid":
        assert_failure(response_errors(value), case["producer_failure"])
    else:
        assert case["producer_expected"] == "valid"
        assert not response_errors(value)
    for key, sibling in case.get("sibling_expectations", {}).items():
        check_reader_expectation(
            value, {"requested_key": key, "caller_default": False, "value": False, "config_version": None, **sibling}
        )


def test_wire_ids_versions_and_file_coverage() -> None:
    ids = [a["fixture_id"] for a in MANIFEST["artifacts"] if a["kind"] == "fixture"]
    for artifact in MANIFEST["artifacts"]:
        if artifact["kind"] in ["corpus", "fixture_set"]:
            ids.extend(artifact["case_ids"])
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+", item) for item in ids)
    assert {a["path"] for a in ARTIFACTS} == {
        p.relative_to(ROOT).as_posix() for p in (ROOT / "fixtures/wire").glob("*.json")
    }
    for artifact in ARTIFACTS:
        data = json.loads((ROOT / artifact["path"]).read_text())
        assert artifact["version"] == data["fixture_version"] == MANIFEST["wire_contract"]["fixture_version"]
        assert artifact["schema"] == data["schema"]
        assert artifact["case_ids"] == [c["id"] for c in data["cases"]]
        assert set(data["templates"]) == {c["template"] for c in data["cases"]}
        for case in data["cases"]:
            assert set(case) <= {
                "id",
                "template",
                "set",
                "remove",
                "expected",
                "expected_failure",
                "reader",
                "producer_failure",
                "producer_expected",
                "sibling_expectations",
            }
            assert case["expected"] in ["valid", "invalid", "reader"]
            assert ("expected_failure" in case) == (case["expected"] == "invalid")
    assert MANIFEST["contract"]["version"] == "1.3.0"
    assert MANIFEST["corpus"]["version"] == "1.1.0"
    assert MANIFEST["wire_contract"]["version"] == "1.0.0"


def test_wire_schemas_and_literal_registry_agree() -> None:
    for artifact in MANIFEST["artifacts"]:
        if artifact["kind"] == "schema":
            schema = SCHEMAS[artifact["path"]]
            Draft202012Validator.check_schema(schema)
            assert schema["$schema"] == MANIFEST["schema_dialect"]
    assert SCHEMAS["schemas/management_warning.schema.json"]["properties"]["code"]["enum"] == LITERALS["warning_codes"]
    producer = SCHEMAS["schemas/flags_response_v3.schema.json"]
    assert producer["$defs"]["v2_reason_code"]["enum"] == [r for r in LITERALS["reason_codes"] if r != "flag_disabled"]
    assert producer["$defs"]["metadata"]["properties"]["rule_type"]["enum"] == [
        r["value"] for r in LITERALS["rule_types"]
    ]
    filters = SCHEMAS["schemas/definitions_entry.schema.json"]["properties"]["filters"]
    assert filters["oneOf"][1]["$ref"] == SCHEMAS["schemas/config.schema.json"]["$id"]


def test_published_component_bytes_and_producer_copy_are_pinned() -> None:
    frozen = {
        "schemas/config.schema.json": "74e43ed13dbfd578bcb8eedc5a247917788b320829b888eab84ce1126841d63b",
        "registries/literals.json": "aef3dd6aaca0a6ba2535f3781d569bd159a39ee988be1f1379671f80fbf10ea6",
        "corpus/hash_sha1_60_v1.json": "e9bea9cff58bac0c6c1021e9c8842c53594055b6874a2c58bc4350d0c28a93ca",
        "corpus/v1_evaluation.json": "752ff88dcb943ab1e21f9552b0c4ec606029393fca2b565b01a497da89cd8675",
        "corpus/legacy_projection.json": "536c8386eefa75721d113bd8718ddeb7fb1aad3b4f2cce1fbcf516a346a16e26",
    }
    versions = {a["path"]: a.get("version") for a in MANIFEST["artifacts"]}
    for path, digest in frozen.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest, path
        assert versions[path] == ("1.1.0" if path.startswith("corpus/") else "1.0.0"), path
    assert hashlib.sha256((ROOT / "schemas/flags_response_v3.schema.json").read_bytes()).hexdigest() == PRODUCER_DIGEST
    assert MANIFEST["wire_contract"]["producer_schema_sha256"] == PRODUCER_DIGEST


def test_every_terminal_reason_has_required_and_forbidden_metadata_fixtures() -> None:
    ids = {c["id"] for c in SETS["responses"]["cases"]}
    assert {r["reason"] for r in MATRIX} == set(LITERALS["reason_codes"]) - {"flag_disabled"}
    for row in MATRIX:
        name = row["reason"] + ("_" + row["rule_type"] if row["reason"] in ["targeting_match", "rollout_miss"] else "")
        assert "responses." + name in ids
        for field in ["id", "version", "config_version", "has_experiment", *row["required"]]:
            assert f"responses.{name}_missing_{field}" in ids
        for field in row["forbidden"]:
            assert f"responses.{name}_forbidden_{field}" in ids
        assert f"responses.{name}_wrong_condition_index" in ids
    for field in ["config_version", "rule_type", "rule_id", "experiment_id", "variant_key"]:
        assert f"responses.split_wrong_type_{field}" in ids
        assert any(c["id"] == f"readers.partial_split_missing_{field}" for c in SETS["readers"]["cases"])


def test_equal_values_keep_distinct_analytical_identities() -> None:
    for group, first, second, value_key, identity_key in [
        ("responses", "experiment_split", "split_arm_b_equal_value", "value", "variant_key"),
        ("calls", "experiment_split", "arm_b_equal_value", "$feature_flag_response", "$feature_flag_variant"),
        ("exposures", "remote_split", "arm_b_equal_value", "$feature_flag_response", "$feature_flag_variant"),
    ]:
        cases = {c["id"].split(".", 1)[1]: c for c in SETS[group]["cases"]}
        a, b = (materialize(group, cases[name]) for name in [first, second])
        if group == "responses":
            a, b = a["flags"]["garden-layout"], b["flags"]["garden-layout"]
            assert a["metadata"][identity_key] != b["metadata"][identity_key]
        else:
            assert a[identity_key] != b[identity_key]
        assert a[value_key] == b[value_key]


def test_definitions_preserve_required_assignment_data_at_the_separate_boundary() -> None:
    value = SETS["definitions"]["templates"]["v2"]
    assert validator("schemas/definitions_entry.schema.json").is_valid(value)
    assert seed_errors(value), "Definitions must retain canonical assignment data for local evaluation"
    for rule in value["filters"]["rules"]:
        if rule["rule_type"] in ["percentage_rollout", "experiment"]:
            assert rule["seed"]
            assert rule["assignment_algorithm"] in LITERALS["assignment_algorithms"]


@pytest.mark.parametrize("field", ["seed", "assignment_seed", "holdout_seed", "assignmentSeed", "$feature_flag_seed"])
def test_seed_scanner_reaches_opaque_objects_and_arrays(field: str) -> None:
    errors = seed_errors({"opaque": [None, {"nested": [{field: "invented-seed"}]}]})
    assert len(errors) == 1
    assert errors[0]["instance_path"] == f"/opaque/1/nested/0/{field}"
