"""Remote /flags?v=4 fixtures and expectations for the SDK compliance suites.

The suites read the feature flag rules v2 contract package directly, so mock
responses and expected results come from one place:

- ``readers``: ``fixtures/wire/readers.json``. Each case is a whole /flags
  response and the reader result for one requested key.
- ``values``: the successful cases of ``corpus/v2_value_evaluation.json``. Each
  case becomes a one-record v4 response carrying the corpus value; its legacy
  cell is the expected legacy getter result and payload.
- ``reader_payloads``: the ``readers`` cases whose legacy payload the contract
  decides. A failed or unreadable record that still carries a
  ``metadata.payload`` is left out: the contract does not say whether the legacy
  payload accessor returns it.
"""

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

SOURCES = ("readers", "values", "reader_payloads")

# Identity fields of the records built from the value corpus. They are invented.
VALUE_RECORD_ID = 401
VALUE_RECORD_VERSION = 3
REQUEST_ID = "30000000-0000-4000-8000-000000000003"
EVALUATED_AT = 1800000000000


def contract_root() -> Path:
    """The feature flag rules v2 contract package, beside CONTRACT.yaml."""
    for base in (Path.cwd(), Path(__file__).resolve().parents[2]):
        root = base / "contracts" / "feature_flag_rules_v2"
        if (root / "manifest.json").is_file():
            return root
    raise FileNotFoundError("contracts/feature_flag_rules_v2 not found")


@lru_cache(maxsize=None)
def _load(relative: str) -> Any:
    return json.loads((contract_root() / relative).read_text(encoding="utf-8"))


def _readers() -> Dict[str, Any]:
    return _load("fixtures/wire/readers.json")


def _value_corpus() -> Dict[str, Any]:
    return _load("corpus/v2_value_evaluation.json")


def _pointer(pointer: str) -> List[str]:
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer.split("/")[1:]]


def _apply(document: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    """Copy a fixture template, then remove and set members by JSON pointer."""
    document = copy.deepcopy(document)
    for pointer in case.get("remove", []):
        *parents, last = _pointer(pointer)
        target = document
        for token in parents:
            target = target[token]
        del target[last]
    for pointer, value in case.get("set", {}).items():
        *parents, last = _pointer(pointer)
        target = document
        for token in parents:
            target = target[token]
        target[last] = copy.deepcopy(value)
    return document


def case_ids(source: str) -> List[str]:
    """Case IDs a suite test expands over."""
    if source == "readers":
        return [case["id"] for case in _readers()["cases"]]
    if source == "values":
        return [case["id"] for case in _value_corpus()["cases"] if case["expected"]["status"] == "success"]
    if source == "reader_payloads":
        return [case["id"] for case in _readers()["cases"] if reader_payload_decided(case["id"])]
    raise ValueError(f"Unknown flags v4 fixture source {source!r}; expected one of {SOURCES}")


def contract_version() -> str:
    """The version of the contract package the cases come from."""
    return str(_load("manifest.json")["contract"]["version"])


def reader(case_id: str) -> Dict[str, Any]:
    """The reader expectation of a reader case: requested key, error code, config version."""
    return dict(_case("readers", case_id)["reader"])


def _case(source: str, case_id: str) -> Dict[str, Any]:
    cases = _readers()["cases"] if source == "readers" else _value_corpus()["cases"]
    for case in cases:
        if case["id"] == case_id:
            return case
    raise ValueError(f"Unknown {source} case {case_id!r}")


def _value_record(case: Dict[str, Any]) -> Dict[str, Any]:
    expected = case["expected"]
    rule = expected.get("rule")
    metadata: Dict[str, Any] = {
        "id": VALUE_RECORD_ID,
        "version": VALUE_RECORD_VERSION,
        "config_version": 2,
        "description": None,
        "payload": None,
        "has_experiment": False,
    }
    if rule:
        metadata["rule_type"] = rule["rule_type"]
        metadata["rule_id"] = rule["id"]
    return {
        "key": case["flag"]["key"],
        "value": copy.deepcopy(expected["value"]),
        "reason": {"code": expected["reason"], "condition_index": rule["index"] if rule else None},
        "metadata": metadata,
    }


def response(source: str, case_id: str) -> Dict[str, Any]:
    """The /flags response body for a case."""
    case = _case(source, case_id)
    if source == "readers":
        return _apply(_readers()["templates"][case["template"]], case)
    record = _value_record(case)
    return {
        "errorsWhileComputingFlags": False,
        "flags": {record["key"]: record},
        "requestId": REQUEST_ID,
        "evaluatedAt": EVALUATED_AT,
    }


def legacy_rendering(value: Any) -> Any:
    """The legacy rendering of a typed value: booleans and strings as is, null as false, anything else true."""
    if isinstance(value, (bool, str)):
        return value
    if value is None:
        return False
    return True


def json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "array"
    return "object"


def json_equal(actual: Any, expected: Any) -> bool:
    """Whether two parsed JSON values are the same JSON value.

    Types are strict (``true`` is not ``1``), numbers compare by value (``1e-7``
    equals ``1e-07`` and ``2`` equals ``2.0``), and object member order does not matter.
    """
    if json_type(actual) != json_type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(json_equal(actual[k], expected[k]) for k in expected)
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(json_equal(a, e) for a, e in zip(actual, expected))
    return bool(actual == expected)


def _decoded_payload(metadata: Dict[str, Any]) -> Any:
    """The decoded ``metadata.payload`` of a record, or None when it has none."""
    payload = metadata.get("payload")
    return json.loads(payload) if isinstance(payload, str) else payload


def reader_payload_decided(case_id: str) -> bool:
    """Whether the contract decides the legacy payload of a reader case's requested key.

    It does unless the reader reports an error other than FLAG_NOT_FOUND for a
    record that carries a ``metadata.payload``.
    """
    reader = _case("readers", case_id)["reader"]
    if reader["error_code"] in (None, "FLAG_NOT_FOUND"):
        return True
    record = response("readers", case_id)["flags"][reader["requested_key"]]
    return (record.get("metadata") or {}).get("payload") is None


def expectation(source: str, case_id: str) -> Dict[str, Any]:
    """What an SDK must report for a case's requested key.

    Keys: ``key``, ``legacy_value`` (legacy getter result; None when the flag
    is absent) and ``legacy_payload`` (legacy payload accessor result as a
    parsed JSON value; None when there is no payload).
    """
    case = _case(source, case_id)
    if source == "values":
        legacy = case["legacy"]
        return {"key": case["flag"]["key"], "legacy_value": legacy["value"], "legacy_payload": legacy["payload"]}

    reader = case["reader"]
    key = reader["requested_key"]
    record = response(source, case_id).get("flags", {}).get(key)
    error_code = reader["error_code"]
    if error_code is not None:
        # A missing flag has no value; a failed or unreadable record is false. Neither has a payload.
        return {"key": key, "legacy_value": None if error_code == "FLAG_NOT_FOUND" else False, "legacy_payload": None}
    metadata = record.get("metadata") or {}
    if "value" not in record:
        # An older server's record: the legacy value is variant ?? enabled, and the payload is metadata.payload.
        variant = record.get("variant")
        legacy_value = variant if variant is not None else bool(record.get("enabled"))
        return {"key": key, "legacy_value": legacy_value, "legacy_payload": _decoded_payload(metadata)}
    value = record["value"]
    if reader["config_version"] == 1:
        legacy_payload = _decoded_payload(metadata)
    else:
        # Config version 2 sends a null metadata.payload; a number or object value is the payload.
        legacy_payload = None if value is None or isinstance(value, (bool, str)) else value
    return {"key": key, "legacy_value": legacy_rendering(value), "legacy_payload": legacy_payload}
