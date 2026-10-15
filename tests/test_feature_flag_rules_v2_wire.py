"""Contract meta-tests only: these do not execute an SDK, evaluator or harness action."""

import copy
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from tests.test_feature_flag_rules_v2_contract import (
    CONTRACT_ROOT,
    CORPUS_KINDS,
    REGISTRY_PATH,
    SCHEMA_DIALECT,
    _all_errors,
    _bin_module,
    _json_pointer,
    _load_json,
    _manifest,
)
from tests.test_feature_flag_rules_v2_corpus import CASE_ID_PATTERN, _case_ids

MANIFEST = _manifest()
ARTIFACTS = [a for a in MANIFEST["artifacts"] if a["kind"] == "fixture_set"]
SETS = {Path(a["path"]).stem: _load_json(CONTRACT_ROOT / a["path"]) for a in ARTIFACTS}
SCHEMAS = {a["path"]: _load_json(CONTRACT_ROOT / a["path"]) for a in MANIFEST["artifacts"] if a["kind"] == "schema"}
REGISTRY = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in SCHEMAS.values())
LITERALS = _load_json(REGISTRY_PATH)
MATRIX = _load_json(CONTRACT_ROOT / "rules/response_presence.json")["rows"]
ROWS_PER_REASON = Counter(row["reason"] for row in MATRIX)
CASES = [(name, case) for name, data in SETS.items() for case in data["cases"]]
LAYERS = ["schema", "presence", "seed", "semantic"]
PRESENCE = _bin_module("update-feature-flag-rules-v2-presence-schemas")
# Keys of the response flags map are customer-chosen flag keys, not protocol fields.
NAMED_MAPS = [("flags",)]


def validator(schema: str | dict[str, Any]) -> Draft202012Validator:
    if isinstance(schema, str):
        schema = SCHEMAS[schema]
    return Draft202012Validator(schema, registry=REGISTRY, format_checker=FormatChecker())


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


def schema_errors(schema: str | dict[str, Any], value: Any, layer: str = "schema") -> list[dict[str, Any]]:
    return [
        {"layer": layer, "keyword": e.validator, "instance_path": _json_pointer(e.absolute_path), "message": e.message}
        for error in validator(schema).iter_errors(value)
        for e in _all_errors(error)
    ]


def seed_errors(value: Any, path: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Inspect the whole document, including opaque objects and arrays."""
    errors = []
    if isinstance(value, dict):
        for field, child in value.items():
            # Cover canonical seed, holdout_seed, assignmentSeed, and event-property spellings.
            normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", field).lower().replace("-", "_").lstrip("$")
            if path not in NAMED_MAPS and (normalized == "seed" or normalized.endswith("_seed")):
                errors.append(
                    {"layer": "seed", "keyword": "assignment_seed", "instance_path": _json_pointer((*path, field))}
                )
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
    # JSON Schema cannot compare a record to its own map key; everything else lives in the schemas.
    for key, record in value["flags"].items():
        if record.get("key") != key:
            errors.append(
                {"layer": "semantic", "keyword": "map_key", "instance_path": _json_pointer(["flags", key, "key"])}
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
    check_producer_case(group, case)


def check_producer_case(group: str, case: dict[str, Any]) -> None:
    value = materialize(group, case)
    if group == "responses":
        errors = response_errors(value)
    else:
        errors = schema_errors(SETS[group]["schema"], value)
    if case["expected"] == "valid":
        assert not errors, errors
    else:
        assert case["expected"] == "invalid"
        assert_failure(errors, case["expected_failure"])
        # A case fails at its declared layer and none before it, so the exact schema keeps its deliberate gaps.
        earlier = LAYERS[: LAYERS.index(case["expected_failure"]["layer"])]
        assert not [e for e in errors if e["layer"] in earlier], errors


def has_split_context(record: dict[str, Any]) -> bool:
    """The direct-exposure predicate; wrongly typed known fields are routed to PARSE_ERROR before this."""
    metadata = record.get("metadata", {})
    return (
        metadata.get("config_version") == 2
        and record.get("reason", {}).get("code") == "experiment_split"
        and metadata.get("rule_type") == "experiment"
        and all(field in metadata for field in ["rule_id", "variant_key"])
    )


def known_type_error(value: Any, shape: dict[str, Any]) -> bool:
    """Only known JSON types: missing tuple members and unknown keys are handled separately."""
    if "$ref" in shape:
        shape = SCHEMAS["schemas/flags_response_v3.schema.json"]["$defs"][shape["$ref"].rsplit("/", 1)[-1]]
    expected = shape.get("type")
    if expected is None and "enum" in shape:
        # Enum-only fields still have a known JSON type, even if their future literals are unknown.
        expected = "integer" if type(shape["enum"][0]) is int else "string"
    if expected is None and shape.get("const") is True:
        expected = "boolean"
    if expected and not Draft202012Validator({"type": expected}).is_valid(value):
        return True
    return isinstance(value, dict) and any(
        k in shape.get("properties", {}) and known_type_error(v, shape["properties"][k]) for k, v in value.items()
    )


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


def test_fixture_and_case_ids_are_unique_and_well_formed() -> None:
    ids = [a["fixture_id"] for a in MANIFEST["artifacts"] if a["kind"] == "fixture"]
    for artifact in MANIFEST["artifacts"]:
        if artifact["kind"] in CORPUS_KINDS:
            ids.extend(_case_ids(artifact, _load_json(CONTRACT_ROOT / artifact["path"])))
    ids.extend(case["id"] for _, case in CASES)
    assert len(ids) == len(set(ids))
    assert all(CASE_ID_PATTERN.fullmatch(item) for item in ids)


def test_wire_fixture_sets_are_well_formed() -> None:
    for data in SETS.values():
        assert data["schema"] in SCHEMAS
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


def test_wire_schemas_and_literal_registry_agree() -> None:
    for artifact in MANIFEST["artifacts"]:
        if artifact["kind"] == "schema":
            schema = SCHEMAS[artifact["path"]]
            Draft202012Validator.check_schema(schema)
            assert schema["$schema"] == SCHEMA_DIALECT
    assert SCHEMAS["schemas/management_warning.schema.json"]["properties"]["code"]["enum"] == LITERALS["warning_codes"]
    producer = SCHEMAS["schemas/flags_response_v3.schema.json"]
    assert producer["$defs"]["v2_reason_code"]["enum"] == [r for r in LITERALS["reason_codes"] if r != "flag_disabled"]
    assert producer["$defs"]["metadata"]["properties"]["rule_type"]["enum"] == [
        r["value"] for r in LITERALS["rule_types"]
    ]
    filters = SCHEMAS["schemas/definitions_entry.schema.json"]["properties"]["filters"]
    assert filters["oneOf"][1]["$ref"] == SCHEMAS["schemas/config.schema.json"]["$id"]
    # Event property shapes are copies of the producer metadata shapes, except the narrowed exposure literals.
    metadata = producer["$defs"]["metadata"]["properties"]
    called = SCHEMAS["schemas/feature_flag_called_context.schema.json"]["properties"]
    exposure = SCHEMAS["schemas/experiment_exposure_properties.schema.json"]["properties"]
    shared = ["variant_key", "rule_id", "forced_variant"]
    # The called context keeps the version 1 experiment ID; a v2 exposure has none.
    for field in ["config_version", "rule_type", "experiment_id", *shared]:
        assert called[PRESENCE.event_property(field)] == metadata[field], field
    for field in shared:
        assert exposure[PRESENCE.event_property(field)] == metadata[field], field
    assert PRESENCE.event_property("experiment_id") not in exposure
    assert exposure["$feature_flag"] == called["$feature_flag"]
    assert exposure["$feature_flag_response"] == called["$feature_flag_response"]


def test_v1_variant_keys_are_carried_verbatim() -> None:
    # The PostHog API never restricted v1 variant keys; stored flags carry dots, slashes and spaces.
    response = copy.deepcopy(SETS["responses"]["templates"]["v1"])
    record = response["flags"]["garden-layout"]
    record["value"] = record["metadata"]["variant_key"] = "provider/model-1.2 beta"
    assert response_errors(response) == []


def row_name(row: dict[str, Any]) -> str:
    """Fixture ids name a row by reason, plus the rule type when a reason has more than one row."""
    return row["reason"] + ("_" + row["rule_type"] if ROWS_PER_REASON[row["reason"]] > 1 else "")


def required(row: dict[str, Any]) -> list[str]:
    # Every v2 record carries has_experiment, so every row requires it.
    return ["has_experiment", *row["required"]]


def another_row_accepts(row: dict[str, Any], present: set[str]) -> bool:
    """A mutation that a sibling row with the same reason and rule type accepts is not a negative."""
    return any(
        other is not row
        and (other["reason"], other["rule_type"]) == (row["reason"], row["rule_type"])
        and set(required(other)) <= present
        and not set(other["forbidden"]) & present
        for other in MATRIX
    )


def restated_by_exact_schema(row: dict[str, Any], field: str) -> bool:
    """The exact schema restates the split row and the no-rule context (see the frozen producer test below)."""
    if field == "experiment_id":
        # The exact schema forbids an experiment ID on every v2 record.
        return True
    if row["rule_type"] is None:
        return field in [*row["forbidden"], "condition_index"]
    return row["reason"] == "experiment_split" and field in [*row["required"], *row["forbidden"]]


# Fixed values for the generated forbidden-field cases.
SAMPLE_VALUES = {
    "rule_type": "experiment",
    "rule_id": "10000000-0000-4000-8000-000000000001",
    "experiment_id": 301,
    "variant_key": "arm_a",
}


def matrix_negatives() -> list[Any]:
    """Per row: a missing required field, a forbidden field and a wrong condition index, each a template mutation."""
    params = []
    for row in MATRIX:
        name = row_name(row)
        (key,) = SETS["responses"]["templates"][name]["flags"]
        record = _json_pointer(["flags", key])
        metadata, condition_index = f"{record}/metadata", f"{record}/reason/condition_index"
        mutations = [
            ("missing", field, {"remove": [f"{metadata}/{field}"]}, "required", metadata)
            for field in required(row)
            if not another_row_accepts(row, set(required(row)) - {field})
        ]
        mutations += [
            ("forbidden", field, {"set": {f"{metadata}/{field}": SAMPLE_VALUES[field]}}, "not", metadata)
            for field in row["forbidden"]
            if not another_row_accepts(row, set(required(row)) | {field})
        ]
        # A terminating rule has an integer condition index; the no-rule outcomes have a null one.
        index = None if row["rule_type"] else 0
        mutations.append(("wrong", "condition_index", {"set": {condition_index: index}}, "type", condition_index))
        for check, field, change, keyword, path in mutations:
            failure = {
                "layer": "schema" if restated_by_exact_schema(row, field) else "presence",
                "keyword": keyword,
                "instance_path": path,
                **({"message_contains": field} if check == "missing" else {}),
            }
            case = {"template": name, **change, "expected": "invalid", "expected_failure": failure}
            params.append(pytest.param(case, id=f"{name}/{check}/{field}"))
    return params


@pytest.mark.parametrize("case", matrix_negatives())
def test_presence_matrix_negatives(case: dict[str, Any]) -> None:
    check_producer_case("responses", case)


def test_every_terminal_reason_has_fixtures() -> None:
    ids = {c["id"] for c in SETS["responses"]["cases"]}
    assert {r["reason"] for r in MATRIX} == set(LITERALS["reason_codes"]) - {"flag_disabled"}
    for field in ["id", "version", "config_version"]:
        # The exact schema requires these on every record, so one case each is enough.
        assert f"responses.missing_{field}" in ids
    for row in MATRIX:
        assert "responses." + row_name(row) in ids
    for field in ["config_version", "rule_type", "rule_id", "variant_key"]:
        assert f"responses.split_wrong_type_{field}" in ids
        assert any(c["id"] == f"readers.partial_split_missing_{field}" for c in SETS["readers"]["cases"])


def test_equal_values_keep_distinct_analytical_identities() -> None:
    cases = {c["id"]: c for c in SETS["responses"]["cases"]}
    a, b = (
        materialize("responses", cases[name])["flags"]["garden-layout"]
        for name in ["responses.experiment_split", "responses.split_arm_b_equal_value"]
    )
    assert a["metadata"]["variant_key"] != b["metadata"]["variant_key"]
    assert a["value"] == b["value"]


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


def test_presence_schema_branches_are_generated_from_the_matrix() -> None:
    presence = SCHEMAS["schemas/flags_response_v3_presence.schema.json"]
    called = SCHEMAS["schemas/feature_flag_called_context.schema.json"]
    branches = presence["allOf"][1]["properties"]["flags"]["additionalProperties"]["then"]["oneOf"]
    # Hand-written called branches (local-only flag_disabled and the diagnostic split) carry a description.
    generated = [branch for branch in called["allOf"][0]["then"]["oneOf"] if "description" not in branch]
    assert len(branches) == len(generated) == len(MATRIX)
    for row, response, call in zip(MATRIX, branches, generated):
        assert response == PRESENCE.response_branch(row), row["reason"]
        assert call == PRESENCE.called_branch(row), row["reason"]
    regenerated = copy.deepcopy((presence, called))
    PRESENCE.regenerate(MATRIX, *regenerated)
    assert regenerated == (presence, called), "run bin/update-feature-flag-rules-v2-presence-schemas.py"


def test_frozen_producer_rules_agree_with_the_matrix() -> None:
    """The frozen producer copy restates the split, no-rule and failed rows; a matrix edit must not diverge from it."""
    defs = SCHEMAS["schemas/flags_response_v3.schema.json"]["$defs"]

    def forbidden(schema: dict[str, Any]) -> list[str]:
        if "not" not in schema:
            return []
        return [clause["required"][0] for clause in schema["not"].get("anyOf", [schema["not"]])]

    def reasons(condition: dict[str, Any]) -> list[str]:
        return condition["properties"]["reason"]["properties"]["code"]["enum"]

    # Every v2 record forbids an experiment ID, so the copy states that once for all rows.
    v2_forbidden = forbidden(defs["v2_rules"]["then"]["properties"]["metadata"])
    assert "experiment_id" in v2_forbidden
    assert all("experiment_id" in row["forbidden"] for row in MATRIX)
    split = next(row for row in MATRIX if row["reason"] == "experiment_split")
    metadata = defs["split_rules"]["then"]["properties"]["metadata"]
    assert metadata["required"] == split["required"]
    assert ["experiment_id", *forbidden(metadata)] == split["forbidden"]
    assert metadata["properties"]["rule_type"]["const"] == split["rule_type"]
    no_rule = [row for row in MATRIX if row["rule_type"] is None]
    assert reasons(defs["no_rule_rules"]["if"]) == [row["reason"] for row in no_rule]
    for row in no_rule:
        assert row["required"] == [], row["reason"]
        assert forbidden(defs["no_rule_context"]) == row["forbidden"], row["reason"]
    assert reasons(defs["v2_rules"]["then"]["allOf"][0]["if"]) == [row["reason"] for row in MATRIX if row["failed"]]
