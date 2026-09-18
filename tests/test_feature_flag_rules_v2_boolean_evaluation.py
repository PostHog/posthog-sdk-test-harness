import hashlib
import math
import struct
from collections import Counter

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from tests.test_feature_flag_rules_v2_contract import CONTRACT_ROOT, _load_json

CORPUS = _load_json(CONTRACT_ROOT / "corpus/v2_boolean_evaluation.json")
CASES = CORPUS["cases"]
CONFIG = Draft202012Validator(_load_json(CONTRACT_ROOT / "schemas/config.schema.json"), format_checker=FormatChecker())


def _bits(value: float) -> str:
    return struct.pack(">d", value).hex()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_boolean_case_has_valid_inputs_and_complete_terminal_context(case: dict) -> None:
    expected = case["expected"]
    errors = list(CONFIG.iter_errors(case["config"]))
    assert bool(errors) == (expected == {"status": "parse_error", "kind": "malformed"}), errors
    if expected["status"] == "success":
        assert ("rule" in expected) == (expected["reason"] != "no_rule_match")
        if "rule" in expected:
            rule = case["config"]["rules"][expected["rule"]["index"]]
            assert expected["rule"]["id"] == rule["id"]
            assert expected["rule"]["rule_type"] == rule["rule_type"]
            if expected["reason"] == "targeting_match":
                assert expected["value"] is rule["value"]
            else:
                assert rule["rule_type"] == "percentage_rollout"
                assert rule["on_rollout_miss"] == "return_default"
                assert expected["value"] is case["config"]["default_value"]
        else:
            assert expected["value"] is case["config"]["default_value"]
    if expected["status"] == "omitted":
        flag = case["flag"]
        assert not flag["active"] or flag["deleted"] or not flag["requested"]
    assert (case["family"] == "white_box") == ("white_box" in case)
    assert (case["family"] == "hashing") == ("hash_evidence" in case)
    assert (case["family"] == "parser") == (expected["status"] == "parse_error")
    assert (case["family"] == "eligibility") == (expected["status"] == "omitted")
    assert "seed" not in str(expected)


@pytest.mark.parametrize("case", [case for case in CASES if "hash_evidence" in case], ids=lambda case: case["id"])
def test_boolean_real_hash_evidence_is_independent(case: dict) -> None:
    rule = next(rule for rule in case["config"]["rules"] if rule["rule_type"] == "percentage_rollout")
    evidence = case["hash_evidence"]
    subject = case["context"]["identifier"][:200]
    text = rule["seed"] + "." + subject
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    n = int(digest[:15], 16)
    hash01 = float(n) / float(0xFFFFFFFFFFFFFFF)
    threshold = rule["rollout_percentage"] / 100.0
    assert evidence == {
        "identifier": subject,
        "input_utf8_hex": text.encode("utf-8").hex(),
        "sha1_hex": digest,
        "first_15_hex": digest[:15],
        "n": str(n),
        "hash01_binary64_hex": _bits(hash01),
        "threshold_binary64_hex": _bits(threshold),
    }
    included = bool(subject) and (rule["rollout_percentage"] == 100 or hash01 <= threshold)
    assert (case["expected"]["reason"] == "targeting_match") == included


@pytest.mark.parametrize("case", [case for case in CASES if "white_box" in case], ids=lambda case: case["id"])
def test_boolean_white_box_edges_use_exact_binary64(case: dict) -> None:
    seam = case["white_box"]
    rule = case["config"]["rules"][0]
    threshold = rule["rollout_percentage"] / 100.0
    assert seam["threshold_binary64_hex"] == _bits(threshold)
    expected_hash = {
        "equal": threshold,
        "below": math.nextafter(threshold, 0.0),
        "above": math.nextafter(threshold, math.inf),
    }[seam["relation"]]
    assert seam["hash01_binary64_hex"] == _bits(expected_hash)
    included = rule["rollout_percentage"] == 100 or expected_hash <= threshold
    assert (case["expected"]["reason"] == "targeting_match") == included


def test_boolean_family_counts_make_consumer_scope_explicit() -> None:
    assert Counter(case["family"] for case in CASES) == {
        "ordering": 18,
        "properties": 52,
        "context": 8,
        "errors": 6,
        "hashing": 20,
        "white_box": 10,
        "eligibility": 3,
        "parser": 2,
    }
