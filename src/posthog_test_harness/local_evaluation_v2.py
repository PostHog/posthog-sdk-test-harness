"""Local evaluation of config version 2 flags: definitions and expectations for the compliance suite.

The suite reads the feature flag rules v2 contract package directly, so served
definitions and expected results come from one place:

- ``fixtures/wire/definitions_feed.json``, one whole feed. A feed case is an entry key.
  The v=2 document is the whole feed and the legacy document is its config version 1
  entries, as the flags service serves them.
- The corpora that ``rules/local_outcomes.json`` maps, which give each case its local
  outcome. A corpus case is a case ID; the v=2 document holds that one flag and the legacy
  document holds none. Only cases a black-box SDK can run are used (``black_box_reason``).

Sources, for ``cases: <source>`` expansion:

- ``feed_v1``: the feed's config version 1 entries; a local-only read is conclusive.
- ``feed_v2``: the feed's config version 2 entries the reader evaluates, with their result.
- ``feed_v2_payloads``: those whose value is a number or an object.
- ``feed_unsupported``: entries that fall back in auto mode and are unresolved in local-only mode.
- ``corpus_conclusive``, ``corpus_payloads`` and ``corpus_inconclusive``: black-box corpus
  cases by local outcome, and the conclusive ones with a number or object value.
"""

import copy
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple, cast

from . import flags_v4

SOURCES = (
    "feed_v1",
    "feed_v2",
    "feed_v2_payloads",
    "feed_unsupported",
    "corpus_conclusive",
    "corpus_payloads",
    "corpus_inconclusive",
)
FEED = "feed"

# Identity of the one flag a corpus case's document holds. Invented.
CORPUS_FLAG_ID = 501
CORPUS_FLAG_VERSION = 3
# The /flags record that answers a fallback. Invented; a lenient local evaluator cannot produce it.
FALLBACK_RULE_ID = "99999999-9999-4999-8999-999999999999"
FALLBACK_STRING = "remote-fallback"
FALLBACK_NUMBER = 7
FALLBACK_OBJECT = {"source": "remote-fallback"}
REQUEST_ID = "30000000-0000-4000-8000-000000000004"
EVALUATED_AT = 1800000000000

# A relative date filter value, read against the current time.
_RELATIVE_DATE = re.compile(r"^-?[0-9]+[hdwmy]$")
_RETURN_TYPES = ("boolean", "string", "number", "object")


def _load(relative: str) -> Any:
    return flags_v4._load(relative)


def _feed() -> Dict[str, Any]:
    return cast(Dict[str, Any], _load("fixtures/wire/definitions_feed.json"))


def _local_outcomes() -> Dict[str, Any]:
    return cast(Dict[str, Any], _load("rules/local_outcomes.json"))


def contract_version() -> str:
    return flags_v4.contract_version()


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def _feed_entries() -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    feed = _feed()
    return list(zip(feed["feed"]["flags"], feed["expectations"]))


def _feed_entry(key: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    for entry, expectation in _feed_entries():
        if entry["key"] == key:
            return entry, expectation
    raise ValueError(f"Unknown definitions feed entry {key!r}")


def _supported(expectation: Dict[str, Any]) -> bool:
    return bool(expectation["reader"] == {"auto": "local_evaluation", "local_only": "local_evaluation"})


@lru_cache(maxsize=None)
def _corpus_cases() -> Tuple[Tuple[str, str], ...]:
    """Every case of the mapped corpora with its corpus path, as ``(case id, corpus)``."""
    cases = []
    for corpus in _local_outcomes()["corpora"]:
        for case in _load(corpus)["cases"]:
            cases.append((case["id"], corpus))
    return tuple(cases)


@lru_cache(maxsize=None)
def _corpus_index() -> Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Each case ID's case and its corpus defaults."""
    index = {}
    for corpus in _local_outcomes()["corpora"]:
        document = _load(corpus)
        for case in document["cases"]:
            index[case["id"]] = (case, document.get("defaults") or {})
    return index


def _corpus_case(case_id: str) -> Dict[str, Any]:
    """A copy of a corpus case with the corpus defaults applied to its flag and context."""
    if case_id not in _corpus_index():
        raise ValueError(f"Unknown corpus case {case_id!r}")
    raw, defaults = _corpus_index()[case_id]
    case = copy.deepcopy(raw)
    case["flag"] = {**defaults.get("flag", {}), **case.get("flag", {})}
    case["context"] = {**defaults.get("context", {}), **case.get("context", {})}
    return case


def local_outcome(expected: Dict[str, Any]) -> Dict[str, Any]:
    """The ``rules/local_outcomes.json`` row for an expected result."""
    for row in _local_outcomes()["rows"]:
        if (
            row["status"] == expected["status"]
            and row.get("error") == expected.get("error")
            and row.get("kind") == expected.get("kind")
        ):
            return cast(Dict[str, Any], row)
    raise ValueError(f"No local outcome row for {expected!r}")


def _predicates(config: Any) -> List[Dict[str, Any]]:
    """Every predicate dictionary in a config's rules, in order, skipping malformed parts."""
    rules = config.get("rules") if isinstance(config, dict) else None
    found: List[Dict[str, Any]] = []
    for rule in rules if isinstance(rules, list) else []:
        targeting = rule.get("targeting") if isinstance(rule, dict) else None
        properties = targeting.get("properties") if isinstance(targeting, dict) else None
        found.extend(p for p in (properties if isinstance(properties, list) else []) if isinstance(p, dict))
    return found


@lru_cache(maxsize=None)
def black_box_reason(case_id: str) -> Optional[str]:
    """Why an SDK's public API cannot run a corpus case, or None when it can.

    A black-box SDK reads a partial property map in UTC at the current time, with a
    non-empty distinct ID and no private seams. So a case is left out when it needs a
    private primitive (``white_box``), describes the remote request boundary
    (``not_applicable``), uses another timezone, an empty identifier or a relative date,
    or supplies a complete map without a key that the rules name: there the key is absent and
    the predicate decided, here it is undecided. A null map is unavailable in both readings.
    """
    case = _corpus_case(case_id)
    context = case["context"]
    if case.get("white_box"):
        return "white_box"
    if local_outcome(case["expected"])["local_evaluation"] == "not_applicable":
        return "not_applicable"
    if context.get("timezone") != "UTC":
        return "timezone"
    if not isinstance(context.get("identifier"), str) or not context["identifier"]:
        return "identifier"
    predicates = _predicates(case["config"])
    if any(isinstance(p.get("value"), str) and _RELATIVE_DATE.match(p["value"]) for p in predicates):
        return "relative_date"
    supplied = context.get("properties")
    # No map at all (null) is unavailable context either way; only a supplied complete map can differ.
    if context.get("properties_complete") and isinstance(supplied, dict):
        if not {p.get("key") for p in predicates} <= set(supplied):
            return "complete_map"
    return None


def _payload_value(value: Any) -> bool:
    return value is not None and not isinstance(value, (bool, str))


def case_ids(source: str) -> List[str]:
    """Case IDs a suite test expands over."""
    return list(_case_ids(source))


@lru_cache(maxsize=None)
def _case_ids(source: str) -> Tuple[str, ...]:
    return tuple(_select(source))


def _select(source: str) -> List[str]:
    if source == "feed_v1":
        return [e["key"] for e, x in _feed_entries() if _supported(x) and x.get("evaluator") == "v1"]
    if source in ("feed_v2", "feed_v2_payloads"):
        keys = [e["key"] for e, x in _feed_entries() if _supported(x) and "expected" in x]
        if source == "feed_v2_payloads":
            keys = [k for k in keys if _payload_value(_feed_entry(k)[1]["expected"]["value"])]
        return keys
    if source == "feed_unsupported":
        return [e["key"] for e, x in _feed_entries() if not _supported(x)]
    if source in ("corpus_conclusive", "corpus_payloads", "corpus_inconclusive"):
        wanted = "inconclusive" if source == "corpus_inconclusive" else "conclusive"
        ids = []
        for case_id, _ in _corpus_cases():
            if black_box_reason(case_id) is not None:
                continue
            expected = _corpus_case(case_id)["expected"]
            if local_outcome(expected)["local_evaluation"] != wanted:
                continue
            if source == "corpus_payloads" and not _payload_value(expected.get("value")):
                continue
            ids.append(case_id)
        return ids
    raise ValueError(f"Unknown local evaluation v2 source {source!r}; expected one of {SOURCES}")


def _is_feed(source: str) -> bool:
    return source.startswith("feed")


# ---------------------------------------------------------------------------
# Served documents and reads
# ---------------------------------------------------------------------------


def _corpus_entry(case: Dict[str, Any]) -> Dict[str, Any]:
    rules = case["config"].get("rules") if isinstance(case["config"], dict) else None
    has_experiment = isinstance(rules, list) and any(
        isinstance(rule, dict) and rule.get("rule_type") == "experiment" for rule in rules
    )
    return {
        "id": CORPUS_FLAG_ID,
        "name": case["flag"].get("name", case["flag"]["key"]),
        "key": case["flag"]["key"],
        "filters": copy.deepcopy(case["config"]),
        "deleted": False,
        "active": case["flag"].get("active", True),
        "ensure_experience_continuity": False,
        "version": CORPUS_FLAG_VERSION,
        "evaluation_runtime": "all",
        "bucketing_identifier": "distinct_id",
        "evaluation_contexts": [],
        "has_experiment": has_experiment,
    }


def documents(source: str, case: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The legacy and v=2 definitions documents for a case.

    A feed source serves the whole feed for v=2 and its config version 1 entries otherwise,
    whatever the case. A corpus case's v=2 document holds its one flag, with the property
    matching version its context selects; its legacy document holds no flag.
    """
    if _is_feed(source):
        feed = copy.deepcopy(_feed()["feed"])
        legacy = {
            **feed,
            "flags": [e for e, x in zip(feed["flags"], _feed()["expectations"]) if x.get("evaluator") == "v1"],
        }
        return legacy, feed
    corpus_case = _corpus_case(_require_case(case))
    v2 = {
        "flags": [_corpus_entry(corpus_case)],
        "group_type_mapping": {},
        "cohorts": {},
        "minimal_flag_called_events": False,
        "property_matching_version": 2 if corpus_case["context"].get("explicit_exact_matching", True) else 1,
    }
    return {"flags": [], "group_type_mapping": {}, "cohorts": {}}, v2


def _require_case(case: Optional[str]) -> str:
    if case is None:
        raise ValueError("A corpus source needs a case")
    return case


def read(source: str, case: Optional[str] = None, entry: Optional[str] = None) -> Dict[str, Any]:
    """The key, distinct ID and person properties of a read.

    A feed read uses the feed's context and the case's entry key, or ``entry`` to read a sibling.
    """
    if _is_feed(source):
        context = _feed()["context"]
        key = entry or _require_case(case)
        _feed_entry(key)
        return {
            "key": key,
            "distinct_id": context["identifier"],
            "person_properties": copy.deepcopy(context["properties"]),
        }
    corpus_case = _corpus_case(_require_case(case))
    return {
        "key": corpus_case["flag"]["key"],
        "distinct_id": corpus_case["context"]["identifier"],
        "person_properties": copy.deepcopy(corpus_case["context"]["properties"]),
    }


# ---------------------------------------------------------------------------
# Expectations
# ---------------------------------------------------------------------------


def _expected_and_identity(source: str, case: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], str]:
    """The case's expected result (None for a config version 1 entry), its served entry, and its outcome."""
    if _is_feed(source):
        entry, expectation = _feed_entry(case)
        if not _supported(expectation):
            return None, entry, "inconclusive"
        return expectation.get("expected"), entry, "conclusive"
    corpus_case = _corpus_case(case)
    return (
        corpus_case["expected"],
        _corpus_entry(corpus_case),
        local_outcome(corpus_case["expected"])["local_evaluation"],
    )


def expectation(source: str, case: str) -> Dict[str, Any]:
    """What an SDK reports for a case.

    Keys: ``key``; ``local_evaluation`` (``conclusive`` or ``inconclusive``);
    ``unresolved_reason`` for an inconclusive case; and for a conclusive config version 2
    case ``legacy_value`` (the legacy getter result), ``legacy_payload`` (the legacy payload
    accessor result, None without a payload), ``called`` (the closed projection of the
    ``$feature_flag_called`` context properties), ``exposure`` (the closed projection of the
    ``$experiment_exposure`` properties, None when no exposure is sent), ``flag_id`` and
    ``flag_version``. A config version 1 entry states only its outcome.
    """
    expected, entry, outcome = _expected_and_identity(source, case)
    result: Dict[str, Any] = {"key": entry["key"], "local_evaluation": outcome}
    if outcome == "inconclusive":
        if _is_feed(source):
            row = local_outcome({"status": "parse_error", "kind": "unsupported"})
        else:
            row = local_outcome(_corpus_case(case)["expected"])
        result["unresolved_reason"] = row["unresolved_reason"]
        return result
    if expected is None:
        return result
    value = expected["value"]
    legacy = flags_v4.legacy_rendering(value)
    called: Dict[str, Any] = {
        "$feature_flag": entry["key"],
        "$feature_flag_response": legacy,
        "$feature_flag_config_version": 2,
        "$feature_flag_reason_code": expected["reason"],
    }
    rule = expected.get("rule")
    if rule:
        called["$feature_flag_rule_id"] = rule["id"]
        called["$feature_flag_rule_type"] = rule["rule_type"]
    if expected.get("variant") is not None:
        called["$feature_flag_variant_key"] = expected["variant"]
    exposure = None
    if expected["reason"] == "experiment_split" and rule:
        exposure = {
            "$feature_flag": entry["key"],
            "$feature_flag_response": legacy,
            "$feature_flag_variant_key": expected["variant"],
            "$feature_flag_rule_id": rule["id"],
            "locally_evaluated": True,
        }
    result.update(
        {
            "legacy_value": legacy,
            "legacy_payload": copy.deepcopy(value) if _payload_value(value) else None,
            "called": called,
            "exposure": exposure,
            "flag_id": entry["id"],
            "flag_version": entry["version"],
        }
    )
    return result


def _fallback_value(config: Any) -> Any:
    """A remote value that differs from the flag's default and from every boolean a lenient reader would invent."""
    return_type = config.get("return_type") if isinstance(config, dict) else None
    default = config.get("default_value") if isinstance(config, dict) else None
    if return_type == "string":
        return FALLBACK_STRING
    if return_type == "number":
        return FALLBACK_NUMBER
    if return_type == "object":
        return copy.deepcopy(FALLBACK_OBJECT)
    return not default if isinstance(default, bool) else True


def fallback_response(source: str, case: str) -> Dict[str, Any]:
    """The /flags?v=4 response that answers the fallback of an inconclusive case."""
    _, entry, outcome = _expected_and_identity(source, case)
    if outcome != "inconclusive":
        raise ValueError(f"{source} case {case!r} is conclusive and never falls back")
    record = {
        "key": entry["key"],
        "value": _fallback_value(entry["filters"]),
        "reason": {"code": "targeting_match", "condition_index": 0},
        "metadata": {
            "id": entry["id"],
            "version": entry["version"],
            "config_version": 2,
            "description": None,
            "payload": None,
            "has_experiment": False,
            "rule_type": "targeted_release",
            "rule_id": FALLBACK_RULE_ID,
        },
    }
    return {
        "errorsWhileComputingFlags": False,
        "flags": {entry["key"]: record},
        "requestId": REQUEST_ID,
        "evaluatedAt": EVALUATED_AT,
    }


def fallback_legacy_value(source: str, case: str) -> Any:
    """The legacy getter result of a case's fallback response."""
    record = next(iter(fallback_response(source, case)["flags"].values()))
    return flags_v4.legacy_rendering(record["value"])


def called_projection(properties: Dict[str, Any]) -> Dict[str, Any]:
    """The closed ``$feature_flag_called`` context projection of event properties."""
    keys = _load("schemas/feature_flag_called_context.schema.json")["properties"]
    return {key: properties[key] for key in keys if key in properties}


def exposure_projection(properties: Dict[str, Any]) -> Dict[str, Any]:
    """The closed ``$experiment_exposure`` projection of event properties."""
    keys = _load("schemas/experiment_exposure_properties.schema.json")["properties"]
    return {key: properties[key] for key in keys if key in properties}
