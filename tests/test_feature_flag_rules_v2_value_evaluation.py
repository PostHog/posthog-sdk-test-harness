import json
from collections import Counter
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from tests.test_feature_flag_rules_v2_contract import CONTRACT_ROOT, _load_json

CORPUS = _load_json(CONTRACT_ROOT / "corpus/v2_value_evaluation.json")
CASES = CORPUS["cases"]
CONFIG = Draft202012Validator(_load_json(CONTRACT_ROOT / "schemas/config.schema.json"), format_checker=FormatChecker())
ORDERING_SCENARIOS = {
    "target",
    "rollout_hit",
    "miss_return_default",
    "miss_return_default_null",
    "miss_continue",
    "no_match_default",
    "no_match_null",
    "order_first_match",
    "order_targeting_miss",
}


def _json(value: Any) -> str:
    # Python treats True == 1 and 1 == 1.0; the encoded form keeps JSON types apart.
    return json.dumps(value, sort_keys=True)


def _depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max(map(_depth, value.values()), default=0)
    if isinstance(value, list):
        return 1 + max(map(_depth, value), default=0)
    return 0


def _legacy(value: Any) -> dict[str, Any]:
    """The legacy table: null is false-like, a string is a variant, numbers and objects become a payload."""
    if value is None:
        return {"enabled": False, "variant": None, "value": False, "payload": None}
    if isinstance(value, str):
        return {"enabled": True, "variant": value, "value": value, "payload": None}
    return {"enabled": True, "variant": None, "value": True, "payload": value}


def _targets(rule: dict[str, Any], properties: dict[str, Any]) -> bool:
    """Every predicate here is a non-negated person `exact` against a list, so matching is list membership."""
    predicates = rule["targeting"]["properties"]
    assert all(p["operator"] == "exact" and p["type"] == "person" and not p["negation"] for p in predicates)
    return all(properties.get(p["key"]) in p["value"] for p in predicates)


def _evaluate(case: dict) -> dict[str, Any]:
    """Independent first-match walk; rollouts are 0 or 100 percent so no hashing is needed."""
    config = case["config"]
    for index, rule in enumerate(config["rules"]):
        if not _targets(rule, case["context"]["properties"]):
            continue
        matched = {"id": rule["id"], "rule_type": rule["rule_type"], "index": index}
        if rule["rule_type"] == "percentage_rollout":
            assert rule["rollout_percentage"] in (0, 100) and case["context"]["identifier"]
            if rule["rollout_percentage"] == 0:
                if rule["on_rollout_miss"] == "continue":
                    continue
                return {
                    "status": "success",
                    "value": config["default_value"],
                    "reason": "rollout_miss",
                    "rule": matched,
                }
        return {"status": "success", "value": rule["value"], "reason": "targeting_match", "rule": matched}
    return {"status": "success", "value": config["default_value"], "reason": "no_rule_match"}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_value_case_has_valid_inputs_and_complete_terminal_context(case: dict) -> None:
    expected = case["expected"]
    config = case["config"]
    errors = list(CONFIG.iter_errors(config))
    assert bool(errors) == (expected["status"] == "parse_error"), errors
    assert (case["family"] == "parser") == (expected["status"] == "parse_error")
    assert case["id"].split(".")[1] in (config["return_type"], "parser")
    if expected["status"] == "parse_error":
        return
    assert _json(expected) == _json(_evaluate(case))
    assert _json(case["legacy"]) == _json(_legacy(expected["value"]))


def test_every_return_type_covers_every_ordering_scenario() -> None:
    ordering = Counter(tuple(case["id"].split(".")[1:]) for case in CASES if case["family"] == "ordering")
    assert set(ordering) == {(t, s) for t in ("string", "number", "object") for s in ORDERING_SCENARIOS}


def test_rules_that_share_a_config_return_distinct_values() -> None:
    for case in CASES:
        if len(case["config"]["rules"]) > 1:
            values = [_json(rule["value"]) for rule in case["config"]["rules"]]
            assert len(values) == len(set(values)), case["id"]


def test_value_edges_are_the_ones_the_readme_names() -> None:
    by_id = {case["id"]: case for case in CASES}
    value = lambda case_id: by_id[case_id]["config"]["rules"][0]["value"]  # noqa: E731
    assert _depth(value("v2_value.object.max_depth")) == 20
    assert _depth(value("v2_value.parser.object_too_deep")) == 21
    assert value("v2_value.number.max_safe_integer") == 2**53 - 1
    assert value("v2_value.parser.number_above_safe_integer") == 2**53
    assert value("v2_value.number.zero") == 0 and by_id["v2_value.number.zero"]["legacy"]["enabled"] is True
    assert value("v2_value.object.empty") == {} and by_id["v2_value.object.empty"]["legacy"]["enabled"] is True


def test_value_family_counts_make_consumer_scope_explicit() -> None:
    assert Counter(case["family"] for case in CASES) == {"ordering": 27, "values": 11, "parser": 11}
