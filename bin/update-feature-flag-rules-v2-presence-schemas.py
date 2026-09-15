#!/usr/bin/env python3
"""Regenerate the terminal-reason branches of the presence and called-context schemas from the presence matrix."""

import json
from pathlib import Path
from typing import Any

CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "contracts" / "feature_flag_rules_v2"
MATRIX_PATH = CONTRACT_ROOT / "rules" / "response_presence.json"
PRESENCE_PATH = CONTRACT_ROOT / "schemas" / "flags_response_v3_presence.schema.json"
CALLED_PATH = CONTRACT_ROOT / "schemas" / "feature_flag_called_context.schema.json"
# An event property name is the metadata field prefixed with $feature_flag_; variant_key becomes variant.
EVENT_FIELDS = {"variant_key": "variant"}


def event_property(field: str) -> str:
    return "$feature_flag_" + EVENT_FIELDS.get(field, field)


def response_branch(row: dict[str, Any]) -> dict[str, Any]:
    """One presence-schema branch per matrix row; every v2 record also carries has_experiment."""
    metadata: dict[str, Any] = {
        "required": ["has_experiment", *row["required"]],
        "not": {"anyOf": [{"required": [field]} for field in row["forbidden"]]},
    }
    if row["rule_type"]:
        metadata["properties"] = {"rule_type": {"const": row["rule_type"]}}
    properties: dict[str, Any] = {
        "reason": {
            "properties": {
                "code": {"const": row["reason"]},
                # A terminating rule has a condition index; the no-rule outcomes have none.
                "condition_index": {"type": "integer", "minimum": 0} if row["rule_type"] else {"type": "null"},
            }
        },
        "metadata": metadata,
    }
    if row["reason"] in ["targeting_match", "experiment_split"]:
        # A matched rule or variant value is never null.
        properties["value"] = {"type": ["boolean", "string", "number", "object"]}
    failed: dict[str, Any] = {"required": ["failed"]} if row["failed"] else {"not": {"required": ["failed"]}}
    return {"properties": properties, **failed}


def called_branch(row: dict[str, Any]) -> dict[str, Any]:
    """The event-side copy of a row: no has_experiment, value or condition_index on the projection."""
    properties = {"$feature_flag_reason_code": {"const": row["reason"]}}
    if row["rule_type"]:
        properties["$feature_flag_rule_type"] = {"const": row["rule_type"]}
    result: dict[str, Any] = {"properties": properties}
    if row["required"]:
        result["required"] = [event_property(field) for field in row["required"]]
    result["not"] = {"anyOf": [{"required": [event_property(field)]} for field in row["forbidden"]]}
    return result


def regenerate(rows: list[dict[str, Any]], presence: dict[str, Any], called: dict[str, Any]) -> None:
    """Rewrite the generated branches in place; hand-written branches carry a description and stay behind them."""
    presence["allOf"][1]["properties"]["flags"]["additionalProperties"]["then"]["oneOf"] = [
        response_branch(row) for row in rows
    ]
    branches = called["allOf"][0]["then"]["oneOf"]
    called["allOf"][0]["then"]["oneOf"] = [called_branch(row) for row in rows] + [
        branch for branch in branches if "description" in branch
    ]


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    presence, called = _load(PRESENCE_PATH), _load(CALLED_PATH)
    regenerate(_load(MATRIX_PATH)["rows"], presence, called)
    for path, schema in [(PRESENCE_PATH, presence), (CALLED_PATH, called)]:
        path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
