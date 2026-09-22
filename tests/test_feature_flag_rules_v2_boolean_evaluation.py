import calendar
import hashlib
import math
import operator
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from tests.test_feature_flag_rules_v2_contract import CONTRACT_ROOT, _load_json
from tests.test_feature_flag_rules_v2_corpus import MAX_IDENTIFIER_SCALAR_VALUES, SCALE, _binary64_hex

CORPUS = _load_json(CONTRACT_ROOT / "corpus/v2_boolean_evaluation.json")
CASES = CORPUS["cases"]
CONFIG = Draft202012Validator(_load_json(CONTRACT_ROOT / "schemas/config.schema.json"), format_checker=FormatChecker())


def _only_predicate(case: dict, operators: tuple[str, ...]) -> tuple[dict, dict]:
    """The one predicate the case exercises, with its rule; fails if the case layout changes."""
    [(rule, predicate)] = [
        (rule, predicate)
        for rule in case["config"]["rules"]
        for predicate in rule.get("targeting", {}).get("properties", [])
        if predicate["operator"] in operators
    ]
    return rule, predicate


@pytest.mark.parametrize("reason", ["targeting_match", "rollout_miss", "no_rule_match"])
@pytest.mark.parametrize("include_rule", [False, True])
def test_boolean_schema_requires_rule_only_for_terminal_rule_reasons(reason: str, include_rule: bool) -> None:
    schema = Draft202012Validator(_load_json(CONTRACT_ROOT / "schemas/v2_boolean_evaluation.schema.json"))
    expected = {"status": "success", "value": False, "reason": reason}
    if include_rule:
        expected["rule"] = {
            "id": "00000000-0000-4000-8000-000000000001",
            "rule_type": "targeted_release",
            "index": 0,
        }
    corpus = {**CORPUS, "cases": [{**CASES[0], "expected": expected}]}
    assert schema.is_valid(corpus) == (include_rule == (reason != "no_rule_match"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_boolean_case_has_valid_inputs_and_complete_terminal_context(case: dict) -> None:
    expected = case["expected"]
    errors = list(CONFIG.iter_errors(case["config"]))
    assert bool(errors) == (expected["status"] == "parse_error"), errors
    if expected.get("kind") == "unsupported":
        assert {tuple(error.absolute_path) for error in errors} == {("version",)}, errors
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
    subject = case["context"]["identifier"][:MAX_IDENTIFIER_SCALAR_VALUES]
    text = rule["seed"] + "." + subject
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    n = int(digest[:15], 16)
    hash01 = float(n) / float(SCALE)
    threshold = rule["rollout_percentage"] / 100.0
    assert evidence == {
        "identifier": subject,
        "input_utf8_hex": text.encode("utf-8").hex(),
        "sha1_hex": digest,
        "first_15_hex": digest[:15],
        "n": str(n),
        "hash01_binary64_hex": _binary64_hex(hash01),
        "threshold_binary64_hex": _binary64_hex(threshold),
    }
    included = bool(subject) and (rule["rollout_percentage"] == 100 or hash01 <= threshold)
    miss = "rollout_miss" if rule["on_rollout_miss"] == "return_default" else "no_rule_match"
    assert case["expected"]["reason"] == ("targeting_match" if included else miss)


@pytest.mark.parametrize("case", [case for case in CASES if "white_box" in case], ids=lambda case: case["id"])
def test_boolean_white_box_edges_use_exact_binary64(case: dict) -> None:
    seam = case["white_box"]
    rule = case["config"]["rules"][0]
    threshold = rule["rollout_percentage"] / 100.0
    assert seam["threshold_binary64_hex"] == _binary64_hex(threshold)
    expected_hash = {
        "equal": threshold,
        "below": math.nextafter(threshold, 0.0),
        "above": math.nextafter(threshold, math.inf),
    }[seam["relation"]]
    assert seam["hash01_binary64_hex"] == _binary64_hex(expected_hash)
    included = rule["rollout_percentage"] == 100 or expected_hash <= threshold
    assert (case["expected"]["reason"] == "targeting_match") == included


def test_boolean_regex_cases_stay_within_the_documented_portable_subset() -> None:
    for case in CASES:
        for rule in case["config"]["rules"]:
            for predicate in rule.get("targeting", {}).get("properties", []):
                if predicate["operator"] not in ("regex", "not_regex"):
                    continue
                pattern = predicate["value"]
                if pattern == "[":
                    with pytest.raises(re.error):
                        re.compile(pattern)
                else:
                    # Keep engine extensions and execution-budget assumptions out of shared cases.
                    assert re.fullmatch(r"[A-Za-z^$()]+", pattern), case["id"]
                    re.compile(pattern)


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case["id"].rsplit(".", 1)[-1] in ("regex", "not_regex", "regex_search")],
    ids=lambda case: case["id"],
)
def test_boolean_regex_search_expectations(case: dict) -> None:
    _, predicate = _only_predicate(case, ("regex", "not_regex"))
    matched = re.search(predicate["value"], case["context"]["properties"][predicate["key"]]) is not None
    if predicate["operator"] == "not_regex":
        matched = not matched
    if predicate.get("negation"):
        matched = not matched
    assert (case["expected"]["reason"] == "targeting_match") == matched


def _subtract_relative(wall_clock: datetime, magnitude: int, unit: str) -> datetime:
    if unit in "hdw":
        return wall_clock - timedelta(**{{"h": "hours", "d": "days", "w": "weeks"}[unit]: magnitude})
    for _ in range(magnitude):  # one calendar step at a time, clamping the day at each step
        if unit == "y":
            year, month = wall_clock.year - 1, wall_clock.month
        else:
            year, month = (wall_clock.year, wall_clock.month - 1) if wall_clock.month > 1 else (wall_clock.year - 1, 12)
        wall_clock = wall_clock.replace(
            year=year, month=month, day=min(wall_clock.day, calendar.monthrange(year, month)[1])
        )
    return wall_clock


def _corpus_date_instant(value: object, context: dict) -> datetime | None:
    """Independent oracle for the date formats exercised by this corpus."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    assert isinstance(value, str)
    zone = ZoneInfo(context["timezone"])
    relative = re.fullmatch(r"-?([0-9]+)([hdwmy])", value)
    if relative:
        now = datetime.fromisoformat(context["now"])
        assert now.tzinfo is not None, "context.now must carry an offset"
        wall_clock = _subtract_relative(now.astimezone(zone).replace(tzinfo=None), int(relative[1]), relative[2])
    else:
        try:
            wall_clock = datetime.fromisoformat(value)
        except ValueError:
            return None
        if wall_clock.tzinfo is not None:
            return wall_clock.astimezone(timezone.utc)

    # Round-trip both offsets: a gap has no valid instant, an overlap has two.
    instants = [wall_clock.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc) for fold in (0, 1)]
    valid = [instant for instant in instants if instant.astimezone(zone).replace(tzinfo=None) == wall_clock]
    return min(valid, default=None)


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in CASES
        if any(
            predicate["operator"].startswith("is_date_")
            for rule in case["config"]["rules"]
            for predicate in rule.get("targeting", {}).get("properties", [])
        )
    ],
    ids=lambda case: case["id"],
)
def test_boolean_date_expectations_match_independent_instant_arithmetic(case: dict) -> None:
    rule, predicate = _only_predicate(case, ("is_date_exact", "is_date_before", "is_date_after"))
    subject = _corpus_date_instant(case["context"]["properties"][predicate["key"]], case["context"])
    target = _corpus_date_instant(predicate["value"], case["context"])
    compare = {"is_date_exact": operator.eq, "is_date_before": operator.lt, "is_date_after": operator.gt}
    matched = subject is not None and target is not None and compare[predicate["operator"]](subject, target)
    if predicate.get("negation"):
        matched = not matched
    assert (case["expected"]["reason"] == "targeting_match") == matched
    assert case["expected"]["value"] == (rule["value"] if matched else case["config"]["default_value"])


def test_boolean_family_counts_make_consumer_scope_explicit() -> None:
    assert Counter(case["family"] for case in CASES) == {
        "ordering": 18,
        "properties": 59,
        "context": 8,
        "errors": 6,
        "hashing": 21,
        "white_box": 10,
        "eligibility": 3,
        "parser": 2,
    }
