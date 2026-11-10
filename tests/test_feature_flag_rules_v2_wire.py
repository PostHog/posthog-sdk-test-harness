"""Contract meta-tests only: these do not execute an SDK, evaluator or harness action."""

import copy
import json
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
    errors = schema_errors("schemas/flags_response_v4.schema.json", value)
    # Keep the schema copy and the additional presence constraints independently visible.
    errors += schema_errors("schemas/flags_response_v4_presence.schema.json", value, "presence")
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
        shape = SCHEMAS["schemas/flags_response_v4.schema.json"]["$defs"][shape["$ref"].rsplit("/", 1)[-1]]
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
    shape = SCHEMAS["schemas/flags_response_v4.schema.json"]["$defs"]["record"]
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


def legacy_payload(response: dict[str, Any], key: str) -> Any:
    """The legacy payload accessor's result: the decoded payload, or None when there is none.

    A failed record reads as disabled, so it yields no payload. A payload that is not valid JSON yields none.
    """
    record = response["flags"].get(key)
    if record is None or record.get("failed") is True:
        return None
    payload = record.get("metadata", {}).get("payload")
    if payload is None:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


@pytest.mark.parametrize("case", SETS["readers"]["cases"], ids=lambda c: c["id"])
def test_reader_legacy_payload_expectations(case: dict[str, Any]) -> None:
    value = materialize("readers", case)
    reader = case["reader"]
    record = value["flags"].get(reader["requested_key"]) or {}
    if record.get("failed") is True and record.get("metadata", {}).get("payload") is not None:
        # A failed record that still carries a payload must pin that the accessor yields none.
        assert "legacy_payload" in reader
    if "legacy_payload" in reader:
        assert reader["legacy_payload"] == legacy_payload(value, reader["requested_key"])


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
    producer = SCHEMAS["schemas/flags_response_v4.schema.json"]
    assert producer["$defs"]["v2_reason_code"]["enum"] == [r for r in LITERALS["reason_codes"] if r != "flag_disabled"]
    assert producer["$defs"]["metadata"]["properties"]["rule_type"]["enum"] == [
        r["value"] for r in LITERALS["rule_types"]
    ]
    filters = SCHEMAS["schemas/definitions_entry.schema.json"]["properties"]["filters"]
    assert filters["oneOf"][1]["$ref"] == SCHEMAS["schemas/config.schema.json"]["$id"]
    # Event property shapes are copies of the producer metadata shapes.
    metadata = producer["$defs"]["metadata"]["properties"]
    called = SCHEMAS["schemas/feature_flag_called_context.schema.json"]["properties"]
    exposure = SCHEMAS["schemas/experiment_exposure_properties.schema.json"]["properties"]
    shared = ["variant_key", "rule_id", "forced_variant"]
    # The called context keeps the version 1 experiment ID; an exposure has none.
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
    presence = SCHEMAS["schemas/flags_response_v4_presence.schema.json"]
    called = SCHEMAS["schemas/feature_flag_called_context.schema.json"]
    branches = presence["allOf"][1]["properties"]["flags"]["additionalProperties"]["then"]["oneOf"]
    # The hand-written called branch (local-only flag_disabled) carries a description.
    generated = [branch for branch in called["allOf"][0]["then"]["oneOf"] if "description" not in branch]
    assert len(branches) == len(MATRIX)
    # The last generated branch takes every reason code that has no branch of its own, or none.
    reasons = generated[-1]["properties"]["$feature_flag_reason_code"]["not"]["enum"]
    assert sorted(reasons) == sorted(LITERALS["reason_codes"])
    assert generated == PRESENCE.called_branches(MATRIX, reasons)
    for row, response, call in zip(MATRIX, branches, generated):
        assert response == PRESENCE.response_branch(row), row["reason"]
        assert call == PRESENCE.called_branch(row), row["reason"]
    # One dropped-attribution branch per reason that names a rule, after the matrix rows.
    dropped = [branch["properties"]["$feature_flag_reason_code"]["const"] for branch in generated[len(MATRIX) : -1]]
    assert dropped == list(dict.fromkeys(row["reason"] for row in MATRIX if row["rule_type"]))
    regenerated = copy.deepcopy((presence, called))
    PRESENCE.regenerate(MATRIX, *regenerated)
    assert regenerated == (presence, called), "run bin/update-feature-flag-rules-v2-presence-schemas.py"


def test_frozen_producer_rules_agree_with_the_matrix() -> None:
    """The frozen producer copy restates the split, no-rule and failed rows; a matrix edit must not diverge from it."""
    defs = SCHEMAS["schemas/flags_response_v4.schema.json"]["$defs"]

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


EXPOSURE_SCHEMA = "schemas/experiment_exposure_properties.schema.json"
CALLED_SCHEMA = "schemas/feature_flag_called_context.schema.json"
# The event schemas have no fixture sets, so the event cases build their projections here.
EXPOSURE = {
    "$feature_flag": "garden-layout",
    "$feature_flag_response": True,
    "$feature_flag_variant_key": SAMPLE_VALUES["variant_key"],
    "$feature_flag_rule_id": SAMPLE_VALUES["rule_id"],
    "locally_evaluated": False,
}
# Sent only for a config version 2 experiment split, an exposure would carry the first four as constants.
EXPOSURE_FORBIDDEN = {
    "$feature_flag_config_version": 2,
    "$feature_flag_rule_type": "experiment",
    "$feature_flag_reason_code": "experiment_split",
    "$experiment_exposure_source": "sdk",
    "$feature_flag_experiment_id": SAMPLE_VALUES["experiment_id"],
}


def row_call(row: dict[str, Any]) -> dict[str, Any]:
    """The config version 2 $feature_flag_called projection of a matrix row, with every required field."""
    values = {**SAMPLE_VALUES, "rule_type": row["rule_type"]}
    return {
        "$feature_flag": "garden-layout",
        "$feature_flag_response": True,
        "$feature_flag_config_version": 2,
        "$feature_flag_reason_code": row["reason"],
        **{PRESENCE.event_property(field): values[field] for field in row["required"]},
    }


def test_event_projections_accept_complete_events() -> None:
    assert schema_errors(EXPOSURE_SCHEMA, EXPOSURE) == []
    # A version 1 call, or one without a config version, keeps the reason description.
    call = {"$feature_flag": "garden-layout", "$feature_flag_response": "compact", "$feature_flag_reason": "Matched"}
    assert schema_errors(CALLED_SCHEMA, call) == []
    assert schema_errors(CALLED_SCHEMA, {**call, "$feature_flag_config_version": 1}) == []


def event_negatives() -> list[Any]:
    """Each case fails for one reason only: the named keyword at the projection root."""
    forbidden = {**EXPOSURE_FORBIDDEN, "$feature_flag_forced_variant": True}
    params = [
        pytest.param(
            EXPOSURE_SCHEMA,
            {**EXPOSURE, field: value},
            "not" if field == "$feature_flag_forced_variant" else "additionalProperties",
            id=f"exposure/forbidden/{field}",
        )
        for field, value in forbidden.items()
    ]
    params += [
        pytest.param(
            EXPOSURE_SCHEMA,
            {k: v for k, v in EXPOSURE.items() if k != field},
            "required",
            id=f"exposure/missing/{field}",
        )
        for field in EXPOSURE
    ]
    # $feature_flag_reason_code replaces the reason description on every config version 2 call.
    params += [
        pytest.param(
            CALLED_SCHEMA,
            {**row_call(row), "$feature_flag_reason": "Matched"},
            "not",
            id=f"calls.{row_name(row)}/forbidden/$feature_flag_reason",
        )
        for row in MATRIX
    ]
    # Contract 10.0.0 renamed $feature_flag_variant to $feature_flag_variant_key; both closed projections reject it.
    split = next(row for row in MATRIX if row["reason"] == "experiment_split")
    params += [
        pytest.param(
            schema,
            {**value, "$feature_flag_variant": SAMPLE_VALUES["variant_key"]},
            "additionalProperties",
            id=f"{name}/renamed/$feature_flag_variant",
        )
        for schema, value, name in [
            (EXPOSURE_SCHEMA, EXPOSURE, "exposure"),
            (CALLED_SCHEMA, row_call(split), f"calls.{row_name(split)}"),
        ]
    ]
    return params


@pytest.mark.parametrize("schema, value, keyword", event_negatives())
def test_event_projection_negatives(schema: str, value: dict[str, Any], keyword: str) -> None:
    errors = schema_errors(schema, value)
    assert [(e["keyword"], e["instance_path"]) for e in errors] == [(keyword, "")], errors


# A tolerant reader drops the whole rule pair of a record that breaks its reason's rows, so these reasons
# also accept a config version 2 call with no attribution. A half pair or a disallowed rule type is rejected.
RULE_REASONS = list(dict.fromkeys(row["reason"] for row in MATRIX if row["rule_type"]))
RULE_TYPES = [rule_type["value"] for rule_type in LITERALS["rule_types"]]


def dropped_call(reason: str) -> dict[str, Any]:
    return {
        "$feature_flag": "garden-layout",
        "$feature_flag_response": True,
        "$feature_flag_config_version": 2,
        "$feature_flag_reason_code": reason,
    }


@pytest.mark.parametrize("reason", RULE_REASONS)
def test_called_context_accepts_a_call_without_attribution(reason: str) -> None:
    assert schema_errors(CALLED_SCHEMA, dropped_call(reason)) == []


@pytest.mark.parametrize("row", MATRIX, ids=row_name)
def test_called_context_accepts_every_complete_row(row: dict[str, Any]) -> None:
    assert schema_errors(CALLED_SCHEMA, row_call(row)) == []


def broken_attribution_calls() -> list[Any]:
    """Per reason that names a rule: each required field missing, then each rule type the reason does not allow."""
    params = []
    for row in MATRIX:
        if row["rule_type"]:
            params += [
                pytest.param(
                    {k: v for k, v in row_call(row).items() if k != PRESENCE.event_property(field)},
                    id=f"calls.{row_name(row)}/missing/{field}",
                )
                for field in row["required"]
            ]
    for reason in RULE_REASONS:
        rows = [row for row in MATRIX if row["reason"] == reason]
        allowed = {row["rule_type"] for row in rows}
        params += [
            pytest.param(
                {**row_call(rows[0]), "$feature_flag_rule_type": rule_type},
                id=f"calls.{reason}/disallowed/{rule_type}",
            )
            for rule_type in RULE_TYPES
            if rule_type not in allowed
        ]
    return params


def stray_attribution_calls() -> list[Any]:
    """Per reason that drops its attribution, and flag_disabled: a call without attribution plus one field."""
    params = []
    for reason in [*RULE_REASONS, "flag_disabled"]:
        rule_types = [row["rule_type"] for row in MATRIX if row["reason"] == reason] or ["targeted_release"]
        values = {**SAMPLE_VALUES, "rule_type": rule_types[0]}
        params += [
            pytest.param(
                {**dropped_call(reason), PRESENCE.event_property(field): value},
                id=f"calls.{reason}/dropped/{field}",
            )
            for field, value in values.items()
        ]
    return params


def test_called_context_accepts_a_local_flag_disabled_call() -> None:
    assert schema_errors(CALLED_SCHEMA, dropped_call("flag_disabled")) == []


# A config version 2 call whose reason code is missing or not registered keeps the raw code, if any, and carries
# no attribution. Registered codes still go through their own branches.
UNREGISTERED_REASONS = ["future_reason", None]


def unregistered_call(reason: str | None) -> dict[str, Any]:
    call = dropped_call(reason or "")
    if reason is None:
        del call["$feature_flag_reason_code"]
    return call


@pytest.mark.parametrize("reason", UNREGISTERED_REASONS, ids=lambda r: r or "missing")
def test_called_context_accepts_an_unregistered_reason_without_attribution(reason: str | None) -> None:
    assert schema_errors(CALLED_SCHEMA, unregistered_call(reason)) == []


def unregistered_attribution_calls() -> list[Any]:
    """A missing or unregistered reason code with any attribution field, or with a complete row's attribution."""
    params = []
    for reason in UNREGISTERED_REASONS:
        name = reason or "missing"
        params += [
            pytest.param(
                {**unregistered_call(reason), PRESENCE.event_property(field): value},
                id=f"calls.unregistered_{name}/{field}",
            )
            for field, value in SAMPLE_VALUES.items()
        ]
        params += [
            pytest.param(
                {
                    **{k: v for k, v in row_call(row).items() if k != "$feature_flag_reason_code"},
                    **unregistered_call(reason),
                },
                id=f"calls.unregistered_{name}/complete/{row_name(row)}",
            )
            for row in MATRIX
            if row["rule_type"]
        ]
    return params


@pytest.mark.parametrize(
    "value", broken_attribution_calls() + stray_attribution_calls() + unregistered_attribution_calls()
)
def test_called_context_rejects_a_half_or_mismatched_rule_pair(value: dict[str, Any]) -> None:
    errors = [(e.validator, _json_pointer(e.absolute_path)) for e in validator(CALLED_SCHEMA).iter_errors(value)]
    assert errors == [("oneOf", "")]
