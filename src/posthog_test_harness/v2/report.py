"""Fail-closed report attribution and strict exit decision; no trusted success field."""

from .contracts import BoundaryError, require


def _key(value):
    return value["case_id"], value["profile_id"]


def _index(values, key):
    result = {}
    for value in values:
        identity = key(value)
        require(identity not in result, "invalid_report", "Duplicate report identity")
        result[identity] = value
    return result


def validate_report(contracts, report):
    contracts.validate("Report", report)
    profiles = _index(report["profiles"], lambda p: p["id"])
    inventory = _index(report["inventory"], _key)
    results = _index(report["results"], _key)
    fixtures = _index(report["fixtures"], lambda f: f["fixture_id"])
    calls = _index(report["calls"], lambda c: c["call_id"])
    require(results.keys() == inventory.keys(), "invalid_report", "Incomplete result inventory")
    for entry in inventory.values():
        require(entry["profile_id"] in profiles, "invalid_report", "Unknown execution profile")
    for fixture in fixtures.values():
        require(_key(fixture) in inventory, "invalid_report", "Unattributed fixture")
    for call in calls.values():
        require(call["fixture_id"] in fixtures, "invalid_report", "Unattributed call")
        visited = {call["call_id"]}
        current = call
        while "parent_call_id" in current:
            parent = current["parent_call_id"]
            require(parent in calls and parent not in visited, "invalid_report", "Unknown or cyclic parent call")
            require(calls[parent]["fixture_id"] == call["fixture_id"], "invalid_report", "Cross-fixture parent call")
            visited.add(parent)
            current = calls[parent]
    attributed = set()
    for key, entry in inventory.items():
        result = results[key]
        require(result["source"] == entry["source"], "invalid_report", "Source identity mismatch")
        disposition = result["result"]
        status = disposition["status"]
        if not entry["selected"]:
            require(status == "not_selected", "invalid_report", "Unselected case executed")
        elif entry["applicability"]["kind"] == "not_applicable":
            require(
                status == "not_applicable" and disposition["applicability_rule"] == entry["applicability"]["rule"],
                "invalid_report",
                "Applicability rule mismatch",
            )
        else:
            require(status not in ("not_selected", "not_applicable"), "invalid_report", "Applicable case hidden")
        failure = disposition.get("failure")
        if failure and disposition["executed"]:
            require(failure["failed_step"] is not None, "invalid_report", "Missing failing step")
        ids = disposition.get("call_ids", failure["call_ids"] if failure else [])
        require(disposition["executed"] or not ids, "invalid_report", "Unexecuted case has calls")
        for identity in ids:
            require(
                identity in calls and identity not in attributed, "invalid_report", "Missing or duplicate case call"
            )
            call = calls[identity]
            require(_key(fixtures[call["fixture_id"]]) == key, "invalid_report", "Cross-case call")
            if status == "passed":
                require(call["completion"]["kind"] == "sdk", "invalid_report", "Passed case contains harness failure")
            attributed.add(identity)
    require(attributed == calls.keys(), "invalid_report", "Orphan call receipt")
    for error in report["errors"]:
        if "case_id" in error:
            require(any(key[0] == error["case_id"] for key in inventory), "invalid_report", "Unknown error case")
        if "call_id" in error:
            require(error["call_id"] in calls, "invalid_report", "Unknown error call")


def strict_exit_code(contracts, report):
    """0: complete successful strict scope; 1: failed/empty scope; 2: malformed report."""
    try:
        validate_report(contracts, report)
    except BoundaryError:
        return 2
    statuses = [result["result"]["status"] for result in report["results"]]
    return (
        0
        if (
            not report["errors"]
            and "passed" in statuses
            and all(status in ("passed", "not_selected", "not_applicable") for status in statuses)
        )
        else 1
    )
