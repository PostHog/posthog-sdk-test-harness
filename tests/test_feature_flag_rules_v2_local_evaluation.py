"""Contract meta-tests only: these do not execute an SDK or an evaluator."""

import re
from collections import Counter
from typing import Any, Optional

import pytest

from tests.test_feature_flag_rules_v2_contract import (
    CONTRACT_ROOT,
    V2_EVALUATION_CORPUS_KINDS,
    _load_json,
    _manifest,
    _walk_json,
)
from tests.test_feature_flag_rules_v2_corpus import _hash01
from tests.test_feature_flag_rules_v2_experiment_evaluation import CONFIG, _evaluate, _json, _semantic_errors
from tests.test_feature_flag_rules_v2_value_evaluation import _legacy
from tests.test_feature_flag_rules_v2_wire import CALLED_SCHEMA, LITERALS, SCHEMAS, assert_failure, schema_errors

OUTCOMES = _load_json(CONTRACT_ROOT / "rules/local_outcomes.json")
FIXTURE = _load_json(CONTRACT_ROOT / "fixtures/wire/definitions_feed.json")
FEED = FIXTURE["feed"]
ENTRIES = list(zip(FEED["flags"], FIXTURE["expectations"]))
DISABLED = {"status": "success", "value": None, "reason": "flag_disabled"}
MISSING_CONTEXT = {"status": "error", "error": "missing_context"}
REGEX_OPERATORS = ("regex", "not_regex")
NUMERIC_OPERATORS = ("gt", "gte", "lt", "lte")
ENTRY_SCHEMA = "schemas/definitions_entry.schema.json"


def _cases(path: str) -> list[dict[str, Any]]:
    """Every case of a corpus with the flag and context it takes from the corpus defaults."""
    corpus = _load_json(CONTRACT_ROOT / path)
    defaults = corpus.get("defaults", {})
    return [
        {
            **case,
            "flag": case.get("flag", defaults.get("flag")),
            "context": defaults.get("context", {}) | case.get("context", {}),
        }
        for case in corpus["cases"]
    ]


CASES = [case for path in OUTCOMES["corpora"] for case in _cases(path)]
LOCAL_CASES = _cases("corpus/v2_local_evaluation.json")
BY_ID = {case["id"]: case for case in CASES}


def _rows(expected: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows with the status, and the error or kind, of an expected result."""
    return [row for row in OUTCOMES["rows"] if all(row.get(k) == expected.get(k) for k in ("status", "error", "kind"))]


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compiles(pattern: str) -> bool:
    try:
        re.compile(pattern)
    except re.error:
        return False
    return True


def _unresolved_reason(case: dict[str, Any]) -> str:
    """Who can fix an inconclusive case, from its inputs alone: the flag's author or the caller."""
    expected = case["expected"]
    config = case["config"]
    if expected["status"] == "parse_error":
        assert list(CONFIG.iter_errors(config)) or _semantic_errors(config)
        return "unsupported_definition"
    predicates = [p for rule in config["rules"] for p in rule["targeting"]["properties"]]
    properties = case["context"]["properties"]
    if expected["error"] == "invalid_regex":
        assert any(p["operator"] in REGEX_OPERATORS and not _compiles(p["value"]) for p in predicates)
        return "unsupported_definition"
    if expected["error"] == "missing_context":
        # The map is unavailable, or it is partial and lacks a key that a rule reads.
        partial = not case["context"]["properties_complete"]
        assert properties is None or (partial and any(p["key"] not in properties for p in predicates))
        return "missing_context"
    # invalid_property: every key is supplied and every rule value is one its operator reads,
    # so the operand that cannot be read is the supplied property.
    numeric = [p for p in predicates if p["operator"] in NUMERIC_OPERATORS]
    assert all(p["key"] in properties for p in predicates)
    assert numeric and all(_number(p["value"]) for p in numeric)
    assert any(not _number(properties[p["key"]]) for p in numeric)
    return "missing_context"


def _walk(config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return _evaluate(
        {"config": config, "context": context}, lambda _use, prefix, subject, salt: _hash01(prefix + subject + salt)
    )


def _local_result(active: bool, config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Independent local walk: an inactive flag is disabled, and a partial map stops at the first absent key."""
    if not active:
        return DISABLED
    assert context["properties_complete"] is False
    properties = context["properties"]
    for index, rule in enumerate(config["rules"]):
        for predicate in rule["targeting"]["properties"]:
            if predicate["key"] not in properties:
                # The evaluation stops here unless an earlier rule already ended it.
                earlier = _walk({**config, "rules": config["rules"][:index]}, context)
                return MISSING_CONTEXT if earlier["reason"] == "no_rule_match" else earlier
            if properties[predicate["key"]] not in predicate["value"]:
                break  # a conclusive miss; the rule's later predicates are not read
    return _walk(config, context)


def _blocked_rule(case: dict[str, Any]) -> Optional[int]:
    """The index of the first rule whose first predicate reads a key that a partial map lacks, when a later rule
    targets everyone. A rule with an absent key behind another predicate is not decided here: an earlier
    conclusive miss in the same rule means the key is never read."""
    properties = case["context"]["properties"]
    if properties is None or case["context"]["properties_complete"]:
        return None
    rules = case["config"]["rules"]
    for index, rule in enumerate(rules):
        predicates = rule["targeting"]["properties"]
        if predicates and predicates[0]["key"] not in properties:
            later_match = any(not later["targeting"]["properties"] for later in rules[index + 1 :])
            return index if later_match else None
        if any(p["key"] not in properties for p in predicates):
            return None
    return None


def _evaluator(entry: dict[str, Any]) -> Optional[str]:
    """The registry's version rule, then the closed config schema for a version 2 document."""
    versions = LITERALS["config_versions"]
    version = entry["filters"].get("version", versions[versions["absent_version_selects"]])
    if not _number(version):
        return None
    if version == versions["v1"]:
        return "v1"
    filters = entry["filters"]
    if version == versions["v2"] and not list(CONFIG.iter_errors(filters)) and not _semantic_errors(filters):
        return "v2"
    return None


def test_local_outcome_files_match_their_closed_schemas() -> None:
    assert schema_errors("schemas/local_outcomes.schema.json", OUTCOMES) == []
    assert schema_errors("schemas/definitions_feed_fixture.schema.json", FIXTURE) == []
    for path in ("schemas/local_outcomes.schema.json", "schemas/definitions_feed_fixture.schema.json"):
        for node in _walk_json(SCHEMAS[path]):
            if isinstance(node, dict) and node.get("type") == "object" and "x-posthog-open-object" not in node:
                assert node.get("additionalProperties") is False, (path, node)


def test_local_outcomes_cover_every_v2_evaluation_corpus() -> None:
    corpora = [a["path"] for a in _manifest()["artifacts"] if a["kind"] in V2_EVALUATION_CORPUS_KINDS]
    assert sorted(OUTCOMES["corpora"]) == sorted(corpora)
    assert {row["local_evaluation"] for row in OUTCOMES["rows"]} == set(OUTCOMES["outcomes"])
    keys = [(row["status"], row.get("error"), row.get("kind")) for row in OUTCOMES["rows"]]
    assert len(keys) == len(set(keys))
    # No row is dead: each one is the outcome of at least one case.
    assert all(any(_rows(case["expected"]) == [row] for case in CASES) for row in OUTCOMES["rows"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_every_case_has_one_local_outcome_that_its_inputs_support(case: dict[str, Any]) -> None:
    [row] = _rows(case["expected"])
    outcome = row["local_evaluation"]
    assert (outcome == "conclusive") == (case["expected"]["status"] == "success")
    if outcome == "inconclusive":
        assert row["unresolved_reason"] == _unresolved_reason(case)
    if outcome == "not_applicable":
        # Only the request boundary leaves a flag out: it is inactive, deleted or not requested.
        flag = case["flag"]
        assert case["family"] == "eligibility"
        assert not flag["active"] or flag["deleted"] or not flag["requested"]


def test_local_outcome_counts_make_consumer_scope_explicit() -> None:
    outcomes: Counter[tuple[str, Optional[str]]] = Counter()
    for case in CASES:
        [row] = _rows(case["expected"])
        outcomes[(row["local_evaluation"], row.get("unresolved_reason"))] += 1
    assert outcomes == {
        ("conclusive", None): 226,
        ("inconclusive", "missing_context"): 7,
        ("inconclusive", "unsupported_definition"): 29,
        ("not_applicable", None): 3,
    }


def test_an_absent_property_stops_the_evaluation_before_a_later_rule_that_would_match() -> None:
    blocked = {case["id"]: index for case in CASES if (index := _blocked_rule(case)) is not None}
    for case_id in blocked:
        expected = BY_ID[case_id]["expected"]
        # A conclusive result can only come from a rule before the one that reads the absent key.
        assert expected == MISSING_CONTEXT or expected["rule"]["index"] < blocked[case_id], case_id
    stopped = {case_id: index for case_id, index in blocked.items() if BY_ID[case_id]["expected"] == MISSING_CONTEXT}
    # The absent key is read in the first rule, and in a rule after a conclusive targeting miss.
    assert stopped["v2_boolean.context.partial_exact"] == 0
    assert stopped["v2_local.context.miss_before_missing"] == 1
    for case_id in stopped:
        [row] = _rows(BY_ID[case_id]["expected"])
        assert (row["local_evaluation"], row["unresolved_reason"]) == ("inconclusive", "missing_context")
    # The same config is conclusive when an earlier rule matches first.
    terminal = BY_ID["v2_local.context.terminal_before_missing"]
    assert terminal["config"] == BY_ID["v2_local.context.miss_before_missing"]["config"]
    assert blocked[terminal["id"]] == 1 and terminal["expected"]["rule"]["index"] == 0


@pytest.mark.parametrize("case", LOCAL_CASES, ids=lambda case: case["id"])
def test_local_case_matches_an_independent_walk(case: dict[str, Any]) -> None:
    config = case["config"]
    assert not list(CONFIG.iter_errors(config)) and not _semantic_errors(config)
    assert case["id"].split(".")[1] == case["family"]
    assert _json(case["expected"]) == _json(_local_result(case["flag"]["active"], config, case["context"]))
    assert "seed" not in str(case["expected"])


def test_an_inactive_flag_returns_no_value_whatever_its_rules_say() -> None:
    disabled = [case for case in LOCAL_CASES if case["family"] == "flag_disabled"]
    assert {case["config"]["return_type"] for case in disabled} == {r["value"] for r in LITERALS["return_types"]}
    when_active = {}
    for case in disabled:
        assert case["flag"]["active"] is False and case["expected"] == DISABLED
        # Neither the flag default nor a rule value is returned: the same flag, when active, has a
        # non-null default and either returns a value or stops for missing context.
        assert case["config"]["default_value"] is not None
        active = when_active[case["id"]] = _local_result(True, case["config"], case["context"])
        assert active == MISSING_CONTEXT or active["value"] is not None, case["id"]
    assert when_active["v2_local.flag_disabled.experiment"]["reason"] == "experiment_split"
    assert when_active["v2_local.flag_disabled.before_missing_context"] == MISSING_CONTEXT
    # The legacy rendering of no value is what an inactive version 1 flag returns today: false, with no payload.
    assert _legacy(DISABLED["value"]) == {"enabled": False, "variant": None, "value": False, "payload": None}
    call = {
        "$feature_flag": disabled[0]["flag"]["key"],
        "$feature_flag_response": _legacy(DISABLED["value"])["value"],
        "$feature_flag_config_version": 2,
        "$feature_flag_reason_code": DISABLED["reason"],
    }
    assert schema_errors(CALLED_SCHEMA, call) == []
    # The remote corpora leave an inactive flag out; every return type they do so for has a local case.
    omitted_inactive = [c for c in CASES if c["expected"]["status"] == "omitted" and not c["flag"]["active"]]
    assert omitted_inactive
    assert {c["config"]["return_type"] for c in omitted_inactive} <= {c["config"]["return_type"] for c in disabled}


RULE = {"id": "00000000-0000-4000-8000-000000000001", "rule_type": "targeted_release", "index": 0}


@pytest.mark.parametrize(
    "active, expected, complete, valid",
    [
        (False, DISABLED, False, True),
        (False, {**DISABLED, "value": False}, False, False),
        (False, {**DISABLED, "rule": RULE}, False, False),
        (False, {"status": "success", "value": True, "reason": "targeting_match", "rule": RULE}, False, False),
        (False, MISSING_CONTEXT, False, False),
        (True, DISABLED, False, False),
        (False, DISABLED, True, False),
    ],
)
def test_local_schema_pins_the_disabled_result_and_the_partial_map(
    active: bool, expected: dict[str, Any], complete: bool, valid: bool
) -> None:
    corpus = _load_json(CONTRACT_ROOT / "corpus/v2_local_evaluation.json")
    template = corpus["cases"][0]
    case = {**template, "flag": {**template["flag"], "active": active}, "expected": expected}
    context = {**corpus["defaults"]["context"], "properties_complete": complete}
    mutated = {**corpus, "defaults": {"context": context}, "cases": [case]}
    assert (schema_errors("schemas/v2_local_evaluation.schema.json", mutated) == []) == valid


def test_local_family_counts_make_consumer_scope_explicit() -> None:
    assert Counter(case["family"] for case in LOCAL_CASES) == {"flag_disabled": 6, "context": 2}


def test_feed_expectations_follow_the_feed_entries() -> None:
    keys = [entry["key"] for entry in FEED["flags"]]
    assert keys == [expectation["key"] for expectation in FIXTURE["expectations"]]
    assert len(keys) == len(set(keys))
    ids = [entry["id"] for entry in FEED["flags"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("entry, expectation", ENTRIES, ids=[entry["key"] for entry, _ in ENTRIES])
def test_feed_entry_is_valid_or_fails_as_declared(entry: dict[str, Any], expectation: dict[str, Any]) -> None:
    errors = schema_errors(ENTRY_SCHEMA, entry)
    if expectation["producer_expected"] == "valid":
        assert errors == []
    else:
        assert_failure(errors, expectation["producer_failure"])


def test_feed_matches_the_feed_schema_without_its_unsupported_entries() -> None:
    supported = [entry for entry, expectation in ENTRIES if expectation["producer_expected"] == "valid"]
    assert 0 < len(supported) < len(ENTRIES)
    assert schema_errors(FIXTURE["schema"], {**FEED, "flags": supported}) == []
    assert schema_errors(FIXTURE["schema"], FEED) != []


@pytest.mark.parametrize("entry, expectation", ENTRIES, ids=[entry["key"] for entry, _ in ENTRIES])
def test_feed_reader_outcome_follows_the_entry_alone(entry: dict[str, Any], expectation: dict[str, Any]) -> None:
    evaluator = _evaluator(entry)
    assert expectation.get("evaluator") == evaluator
    # An entry that the producer schema rejects is one that no evaluator arm reads, and the reverse.
    assert (expectation["producer_expected"] == "invalid") == (evaluator is None)
    if evaluator is None:
        assert expectation["reader"] == {"auto": "remote_fallback", "local_only": "unsupported"}
    else:
        assert expectation["reader"] == {"auto": "local_evaluation", "local_only": "local_evaluation"}
    if evaluator == "v2":
        # The result depends on the entry and the context only, so no other entry can change it.
        expected = expectation["expected"]
        assert _json(expected) == _json(_local_result(entry["active"], entry["filters"], FIXTURE["context"]))
        [row] = _rows(expected)
        assert row["local_evaluation"] == "conclusive"


def test_feed_covers_both_config_versions_and_every_unsupported_kind() -> None:
    read = Counter((x.get("evaluator"), entry["active"]) for entry, x in ENTRIES if "evaluator" in x)
    assert set(read) == {("v1", True), ("v1", False), ("v2", True), ("v2", False)}
    v2 = [entry for entry, expectation in ENTRIES if expectation.get("evaluator") == "v2"]
    assert {entry["filters"]["return_type"] for entry in v2} == {r["value"] for r in LITERALS["return_types"]}
    assert {x["expected"]["reason"] for _, x in ENTRIES if "expected" in x} == {
        "targeting_match",
        "experiment_split",
        "flag_disabled",
    }
    # An absent version and an explicit 1 both select the version 1 evaluator.
    v1 = [entry for entry, expectation in ENTRIES if expectation.get("evaluator") == "v1"]
    assert {entry["filters"].get("version") for entry in v1} == {None, 1}
    # The cohort map and the group type mapping hold what the version 1 entries refer to.
    predicates = [p for entry in v1 for group in entry["filters"]["groups"] for p in group["properties"]]
    cohorts = {str(p["value"]) for p in predicates if p["type"] == "cohort"}
    assert cohorts and cohorts == set(FEED["cohorts"])
    indexes = [entry["filters"].get("aggregation_group_type_index") for entry in v1]
    groups = {str(index) for index in indexes if index is not None}
    assert groups and groups == set(FEED["group_type_mapping"])
    failures = [x["producer_failure"] for _, x in ENTRIES if "producer_failure" in x]
    assert {failure["instance_path"]: failure["keyword"] for failure in failures} == {
        "/filters/version": "const",
        "/filters/rules/1/rule_type": "const",
        "/filters/rules/1": "additionalProperties",
    }


def test_unsupported_feed_entries_sit_between_supported_ones_and_would_tempt_a_lenient_reader() -> None:
    supported = ["evaluator" in expectation for _, expectation in ENTRIES]
    for index, (entry, expectation) in enumerate(ENTRIES):
        if "evaluator" in expectation:
            continue
        assert 0 < index < len(ENTRIES) - 1 and supported[index - 1] and supported[index + 1], entry["key"]
        # Its first rule alone is a valid config version 2 rule that matches the context, so a reader
        # that ignored the unsupported part would have returned a value instead of falling back.
        filters = entry["filters"]
        first = {
            "version": 2,
            "return_type": filters["return_type"],
            "default_value": filters["default_value"],
            "rules": filters["rules"][:1],
        }
        assert not list(CONFIG.iter_errors(first))
        lenient = _local_result(entry["active"], first, FIXTURE["context"])
        assert lenient["status"] == "success" and lenient["rule"]["index"] == 0, entry["key"]
