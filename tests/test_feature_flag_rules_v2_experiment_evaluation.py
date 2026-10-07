import hashlib
import json
import re
import struct
from collections import Counter
from decimal import Decimal
from typing import Any, Callable, Optional

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from tests.test_feature_flag_rules_v2_contract import CONTRACT_ROOT, _load_json
from tests.test_feature_flag_rules_v2_corpus import MAX_IDENTIFIER_SCALAR_VALUES, SCALE, _binary64_hex

CORPUS = _load_json(CONTRACT_ROOT / "corpus/v2_experiment_evaluation.json")
CASES = CORPUS["cases"]
BY_ID = {case["id"]: case for case in CASES}
SCHEMA = _load_json(CONTRACT_ROOT / "schemas/v2_experiment_evaluation.schema.json")
CONFIG = Draft202012Validator(_load_json(CONTRACT_ROOT / "schemas/config.schema.json"), format_checker=FormatChecker())


def _json(value: Any) -> str:
    # Python treats True == 1 and 1 == 1.0; the encoded form keeps JSON types apart.
    return json.dumps(value, sort_keys=True)


def _from_hex(value: str) -> float:
    return struct.unpack(">d", bytes.fromhex(value))[0]


def _hundredths(value: float) -> Optional[int]:
    """Exact hundredths of a stored percentage, or None when it has more than two decimal places."""
    exact = Decimal(repr(value)) * 100
    return int(exact) if exact == exact.to_integral_value() else None


def _semantic_errors(config: dict[str, Any]) -> list[str]:
    """The registry's semantic constraints for experiment rules, which the config schema cannot express."""
    errors = []
    for rule in config["rules"]:
        if rule["rule_type"] != "experiment":
            continue
        keys = [variant["key"] for variant in rule["variants"]]
        if len(keys) != len(set(keys)):
            errors.append("variant_keys_unique")
        weights = [_hundredths(variant["weight"]) for variant in rule["variants"]]
        if None in weights:
            errors.append("percentage_two_decimals")
        elif sum(weights) != 10000:
            errors.append("variant_weights_sum_100")
        holdout = rule.get("holdout")
        if holdout is not None and _hundredths(holdout["exclusion_percentage"]) is None:
            errors.append("percentage_two_decimals")
    return errors


def _hash01(prefix: str, subject: str, salt: str) -> float:
    n = int(hashlib.sha1((prefix + subject + salt).encode("utf-8")).hexdigest()[:15], 16)
    return float(n) / float(SCALE)


def _targets(rule: dict[str, Any], properties: dict[str, Any]) -> bool:
    """Every predicate here is a non-negated person `exact` against a list, so matching is list membership."""
    predicates = rule["targeting"]["properties"]
    assert all(p["operator"] == "exact" and p["type"] == "person" and not p.get("negation") for p in predicates)
    return all(properties.get(p["key"]) in p["value"] for p in predicates)


# hash_at(use, prefix, subject, salt, rule) returns the hash01 drawn for one use of one rule.
HashAt = Callable[[str, str, str, str, dict[str, Any]], float]


def _evaluate(case: dict[str, Any], hash_at: HashAt) -> dict[str, Any]:
    """Independent walk of the README order: pause, holdout, rollout, then variant assignment."""
    config = case["config"]
    subject = case["context"]["identifier"][:MAX_IDENTIFIER_SCALAR_VALUES]
    for index, rule in enumerate(config["rules"]):
        if not _targets(rule, case["context"]["properties"]):
            continue
        claimed = {"id": rule["id"], "rule_type": rule["rule_type"], "index": index}
        default = {"status": "success", "value": config["default_value"], "rule": claimed}
        if rule["rule_type"] == "targeted_release":
            return {"status": "success", "value": rule["value"], "reason": "targeting_match", "rule": claimed}
        if rule["rule_type"] == "experiment" and rule["paused"]:
            return {**default, "reason": "experiment_paused"}
        holdout = rule.get("holdout")
        if rule["rule_type"] == "experiment" and holdout is not None and subject:
            excluded = holdout["exclusion_percentage"]
            if excluded == 100 or hash_at("holdout", holdout["seed"], subject, "", rule) <= excluded / 100:
                return {**default, "reason": "holdout"}
        percentage = rule["rollout_percentage"]
        if not subject or (
            percentage < 100 and hash_at("rollout", rule["seed"] + ".", subject, "", rule) > percentage / 100
        ):
            if rule["on_rollout_miss"] == "continue":
                continue
            return {**default, "reason": "rollout_miss"}
        if rule["rule_type"] == "percentage_rollout":
            return {"status": "success", "value": rule["value"], "reason": "targeting_match", "rule": claimed}
        draw = hash_at("variant", rule["seed"] + ".", subject, "variant", rule)
        boundary = 0.0
        chosen = rule["variants"][-1]
        for variant in rule["variants"]:
            boundary += variant["weight"] / 100
            if draw < boundary:
                chosen = variant
                break
        return {
            "status": "success",
            "value": chosen["value"],
            "reason": "targeting_match",
            "rule": claimed,
            "variant": chosen["key"],
        }
    return {"status": "success", "value": config["default_value"], "reason": "no_rule_match"}


def _legacy(value: Any) -> dict[str, Any]:
    """The legacy table: null and false are disabled, a string is a variant, numbers and objects become a payload."""
    if value is None or value is False:
        return {"enabled": False, "variant": None, "value": False, "payload": None}
    if value is True:
        return {"enabled": True, "variant": None, "value": True, "payload": None}
    if isinstance(value, str):
        return {"enabled": True, "variant": value, "value": value, "payload": None}
    return {"enabled": True, "variant": None, "value": True, "payload": value}


Draw = tuple[str, str, str, str, dict[str, Any]]


def _real_hashes(case: dict[str, Any]) -> tuple[dict[str, Any], list[Draw]]:
    drawn: list[Draw] = []

    def hash_at(use: str, prefix: str, subject: str, salt: str, rule: dict[str, Any]) -> float:
        drawn.append((use, prefix, subject, salt, rule))
        return _hash01(prefix, subject, salt)

    return _evaluate(case, hash_at), drawn


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_experiment_case_inputs_and_terminal_context(case: dict[str, Any]) -> None:
    expected = case["expected"]
    config = case["config"]
    invalid = bool(list(CONFIG.iter_errors(config))) or bool(_semantic_errors(config))
    assert invalid == (expected["status"] == "parse_error")
    assert (case["family"] == "parser") == (expected["status"] == "parse_error")
    if expected["status"] == "parse_error":
        return
    assert all(rule.get("experiment_id", None) is None for rule in config["rules"])
    if "white_box" in case:
        injected = _from_hex(case["white_box"]["hash01_binary64_hex"])
        actual = _evaluate(case, lambda *_: injected)
    else:
        actual, drawn = _real_hashes(case)
        assert bool(drawn) == ("hash_evidence" in case)
    assert _json(expected) == _json(actual)
    assert _json(case["legacy"]) == _json(_legacy(expected["value"]))
    assert "seed" not in str(expected)


@pytest.mark.parametrize("case", [case for case in CASES if "hash_evidence" in case], ids=lambda case: case["id"])
def test_experiment_hash_evidence_is_independent(case: dict[str, Any]) -> None:
    _, drawn = _real_hashes(case)
    assert len(drawn) == len(case["hash_evidence"])
    for (use, prefix, subject, salt, rule), evidence in zip(drawn, case["hash_evidence"]):
        text = prefix + subject + salt
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        n = int(digest[:15], 16)
        recomputed = {
            "use": use,
            "prefix": prefix,
            "identifier": subject,
            "salt": salt,
            "input_utf8_hex": text.encode("utf-8").hex(),
            "sha1_hex": digest,
            "first_15_hex": digest[:15],
            "n": str(n),
            "hash01_binary64_hex": _binary64_hex(float(n) / float(SCALE)),
        }
        if use == "variant":
            boundary, boundaries = 0.0, []
            for variant in rule["variants"]:
                boundary += variant["weight"] / 100
                boundaries.append(_binary64_hex(boundary))
            recomputed["boundaries_binary64_hex"] = boundaries
        elif use == "rollout":
            recomputed["threshold_binary64_hex"] = _binary64_hex(rule["rollout_percentage"] / 100)
        else:
            recomputed["threshold_binary64_hex"] = _binary64_hex(rule["holdout"]["exclusion_percentage"] / 100)
        assert evidence == recomputed


@pytest.mark.parametrize("case", [case for case in CASES if "white_box" in case], ids=lambda case: case["id"])
def test_experiment_white_box_edges_use_exact_binary64(case: dict[str, Any]) -> None:
    seam = case["white_box"]
    injected = _from_hex(seam["hash01_binary64_hex"])
    boundary = _from_hex(seam["boundary_binary64_hex"])
    relation = "equal" if injected == boundary else ("below" if injected < boundary else "above")
    assert seam["relation"] == relation
    [rule] = [rule for rule in case["config"]["rules"] if rule["rule_type"] == "experiment"]
    if seam["against"] == "holdout_threshold":
        assert boundary == rule["holdout"]["exclusion_percentage"] / 100
        assert (case["expected"]["reason"] == "holdout") == (injected <= boundary)
    elif seam["against"] == "rollout_threshold":
        assert boundary == rule["rollout_percentage"] / 100
        assert (case["expected"]["reason"] == "rollout_miss") == (injected > boundary)
    else:
        boundaries, total = [], 0.0
        for variant in rule["variants"]:
            total += variant["weight"] / 100
            boundaries.append(total)
        assert boundary in boundaries
        assert rule["rollout_percentage"] == 100 and "holdout" not in rule
        expected_key = case["expected"]["variant"]
        position = [variant["key"] for variant in rule["variants"]].index(expected_key)
        # The chosen variant's own boundary is the first one above the hash, or the last when none is.
        assert injected < boundaries[position] or position == len(boundaries) - 1
        assert all(injected >= b for b in boundaries[:position])


@pytest.mark.parametrize("reason", ["targeting_match", "rollout_miss", "experiment_paused", "holdout"])
@pytest.mark.parametrize("rule_type", ["percentage_rollout", "experiment"])
@pytest.mark.parametrize("include_variant", [False, True])
def test_schema_requires_variant_exactly_for_an_experiment_split(
    reason: str, rule_type: str, include_variant: bool
) -> None:
    expected: dict[str, Any] = {
        "status": "success",
        "value": "blue",
        "reason": reason,
        "rule": {"id": "00000000-0000-4000-8000-000000000001", "rule_type": rule_type, "index": 0},
    }
    if include_variant:
        expected["variant"] = "control"
    corpus = {**CORPUS, "cases": [{**BY_ID["v2_experiment.ordering.split"], "expected": expected}]}
    split = reason == "targeting_match" and rule_type == "experiment"
    assert Draft202012Validator(SCHEMA).is_valid(corpus) == (include_variant == split)


def test_variant_values_cover_every_return_type_and_equal_values_stay_distinct() -> None:
    splits = [case for case in CASES if "variant" in case["expected"]]
    assert {case["config"]["return_type"] for case in splits} == {"boolean", "string", "number", "object"}
    control, test = (
        BY_ID["v2_experiment.assignment.equal_values_control"],
        BY_ID["v2_experiment.assignment.equal_values_test"],
    )
    assert control["config"] == test["config"]
    assert control["expected"]["value"] == test["expected"]["value"]
    assert control["expected"]["variant"] != test["expected"]["variant"]
    false_variant = BY_ID["v2_experiment.values.boolean_false_variant"]
    assert false_variant["expected"]["value"] is False and false_variant["legacy"]["enabled"] is False


def test_named_edges_hold() -> None:
    def rule(case_id: str) -> dict[str, Any]:
        return next(r for r in BY_ID[case_id]["config"]["rules"] if r["rule_type"] == "experiment")

    weights = [variant["weight"] for variant in rule("v2_experiment.assignment.two_decimal_other")["variants"]]
    assert weights == [33.33, 33.33, 33.34]
    # A seed equal to the flag key hashes the bytes the version 1 multivariate variant hash uses.
    parity = BY_ID["v2_experiment.assignment.flag_key_seed"]
    assert rule(parity["id"])["seed"] == parity["flag"]["key"]
    assert parity["hash_evidence"][0]["prefix"] == parity["flag"]["key"] + "."
    # A holdout seed of holdout- hashes the bytes the version 1 holdout uses.
    assert BY_ID["v2_experiment.assignment.legacy_holdout_seed"]["hash_evidence"][0]["prefix"] == "holdout-"
    long_subject = BY_ID["v2_experiment.assignment.identifier_201"]
    assert len(long_subject["context"]["identifier"]) == 201
    assert len(long_subject["hash_evidence"][0]["identifier"]) == 200
    shared = BY_ID["v2_experiment.assignment.shared_rollout_seed"]["hash_evidence"]
    assert shared[0]["hash01_binary64_hex"] == shared[1]["hash01_binary64_hex"]
    assert BY_ID["v2_experiment.white_box.off_the_end_zero_weight"]["expected"]["variant"] == "never"
    for case_id in ("ordering.paused_before_holdout", "ordering.holdout_before_rollout_miss"):
        assert "hash_evidence" not in BY_ID["v2_experiment." + case_id]
    assert all(not case["context"]["identifier"] for case in CASES if "empty_identifier" in case["id"])


def test_parser_rows_cover_schema_and_semantic_rejections() -> None:
    parser = [case for case in CASES if case["family"] == "parser"]
    semantic = Counter(error for case in parser for error in _semantic_errors(case["config"]))
    assert set(semantic) == {"variant_keys_unique", "percentage_two_decimals", "variant_weights_sum_100"}
    schema_only = [case for case in parser if not _semantic_errors(case["config"])]
    assert all(list(CONFIG.iter_errors(case["config"])) for case in schema_only)
    assert re.fullmatch(r"v2_experiment\.parser\.[a-z0-9_]+", parser[0]["id"])


def test_experiment_family_counts_make_consumer_scope_explicit() -> None:
    assert Counter(case["family"] for case in CASES) == {
        "ordering": 20,
        "assignment": 22,
        "values": 9,
        "white_box": 10,
        "parser": 21,
    }
    reasons = Counter(case["expected"].get("reason") for case in CASES)
    assert {"targeting_match", "rollout_miss", "experiment_paused", "holdout", "no_rule_match"} <= set(reasons)
