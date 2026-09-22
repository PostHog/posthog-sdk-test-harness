"""Small draft HTTP envelope checks; SDK argument and result assertions belong to scenarios."""

import json
import math

VERSION = "sdk-compliance-v2-draft2"
MAX_BODY = 1024 * 1024


class BoundaryError(ValueError):
    """A protocol/fixture failure, never a native SDK exception."""

    def __init__(self, code, message, kind="harness_error", *, details=None):
        super().__init__(message)
        self.code, self.kind = code, kind
        # Rich assertion evidence belongs in diagnostics, not transport envelopes.
        self.details = details

    def failure(self):
        return {"kind": self.kind, "code": self.code, "message": str(self)}


def require(condition, code, message):
    if not condition:
        raise BoundaryError(code, message)


def json_value(value):
    """Reject Python-only values and non-finite numbers before serialization/schema validation."""
    if value is None or type(value) in (bool, str, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            json_value(item)
        return
    raise BoundaryError("invalid_json", "Value is not finite JSON data")


def decode_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "invalid_json", "Duplicate JSON object key")
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data, object_pairs_hook=pairs)
        json_value(value)
        return value
    except (UnicodeError, ValueError, RecursionError) as error:
        raise BoundaryError("invalid_json", "Malformed or non-finite JSON data") from error


def encode_json(value):
    try:
        json_value(value)
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (UnicodeError, ValueError, RecursionError) as error:
        raise BoundaryError("invalid_json", "Value cannot be encoded as UTF-8 JSON") from error


def json_equal(left, right):
    """JSON equality without Python's false == 0 coercion (object order is immaterial)."""
    if type(left) is not type(right):
        return type(left) in (int, float) and type(right) in (int, float) and left == right
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


class Contracts:
    """Validate transport shape only, without an SDK operation catalog."""

    def validate(self, name, value):
        json_value(value)
        if name == "DeadlineMs":
            require(type(value) is int and 0 < value <= 60000, "invalid_deadline", "Expected 1..60000 milliseconds")
            return
        require(isinstance(value, dict), "invalid_envelope", f"Expected {name} object")
        requests = {
            "NegotiateRequest": {"protocol"},
            "AllocateRequest": {"fixture_id", "case_id", "profile_id", "timeout_ms"},
            "InvokeRequest": {"fixture_id", "call_id", "route", "args", "timeout_ms"},
            "CloseRequest": {"fixture_id", "timeout_ms"},
        }
        if name in requests:
            require(set(value) == requests[name], "invalid_envelope", f"Invalid {name} fields")
            for key in requests[name] - {"timeout_ms", "args"}:
                require(isinstance(value[key], str) and bool(value[key]), "invalid_envelope", f"Expected {key}")
            if "timeout_ms" in value:
                self.validate("DeadlineMs", value["timeout_ms"])
            if "args" in value:
                require(isinstance(value["args"], dict), "invalid_envelope", "Expected argument object")
            return
        if name == "NegotiateResponse":
            require(value.get("protocol") == VERSION, "incompatible_adapter", "Adapter protocol differs")
            routes = value.get("supported_routes")
            require(
                isinstance(routes, list) and all(isinstance(r, str) and r.startswith("/") for r in routes),
                "invalid_envelope",
                "Expected supported routes",
            )
            require(len(set(routes)) == len(routes), "invalid_envelope", "Duplicate route")
            profiles = value.get("profiles")
            require(isinstance(profiles, list) and bool(profiles), "invalid_envelope", "Expected profiles")
            for profile in profiles:
                require(
                    isinstance(profile, dict) and isinstance(profile.get("id"), str) and bool(profile["id"]),
                    "invalid_envelope",
                    "Expected profile identity",
                )
                require(profile.get("sdk_type") in ("client", "server"), "invalid_envelope", "Expected SDK type")
                for key in ("sdk_capabilities", "fixture_capabilities"):
                    items = profile.get(key)
                    require(
                        isinstance(items, list)
                        and all(isinstance(x, str) for x in items)
                        and len(set(items)) == len(items),
                        "invalid_envelope",
                        "Expected capabilities",
                    )
            require(len({p["id"] for p in profiles}) == len(profiles), "invalid_envelope", "Duplicate profile")
            self.validate("DeadlineMs", value.get("max_timeout_ms"))
        elif name in ("AllocateResponse", "CloseResponse"):
            require(
                set(value) == {"fixture_id"} and isinstance(value["fixture_id"], str),
                "invalid_envelope",
                "Expected fixture acknowledgment",
            )
        elif name == "InvokeResponse":
            require(
                set(value) == {"fixture_id", "call_id", "completion"}
                and all(isinstance(value[k], str) for k in ("fixture_id", "call_id")),
                "invalid_envelope",
                "Expected invocation acknowledgment",
            )
            self.completion(value["completion"])
        elif name == "Report":
            require(value.get("contract_version") == VERSION, "invalid_report", "Report protocol differs")
            for key in ("profiles", "inventory", "results", "fixtures", "calls", "errors"):
                require(isinstance(value.get(key), list), "invalid_report", f"Missing {key}")
            for call in value["calls"]:
                self.completion(call["completion"])
            for row in value["results"]:
                require(isinstance(row, dict), "invalid_report", "Expected result row")
                self.result(row.get("result"))

    def result(self, value):
        require(isinstance(value, dict), "invalid_report", "Expected case result")
        status = value.get("status")
        fields = {
            "passed": {"call_ids"},
            "not_selected": {"reason"},
            "not_applicable": {"reason", "applicability_rule"},
            **{
                status: {"failure"}
                for status in (
                    "failed_assertion",
                    "blocked_fixture",
                    "blocked_contract",
                    "unsupported_binding",
                    "harness_error",
                )
            },
        }
        require(isinstance(status, str) and status in fields, "invalid_report", "Unknown status")
        require(
            set(value) == {"status", "executed"} | fields[status],
            "invalid_report",
            f"Invalid fields for {status} result",
        )
        require(type(value["executed"]) is bool, "invalid_report", "Expected execution flag")
        if status in ("passed", "not_selected", "not_applicable"):
            require(value["executed"] == (status == "passed"), "invalid_report", "Invalid execution flag")
        for key in fields[status] & {"reason", "applicability_rule"}:
            require(isinstance(value[key], str) and bool(value[key]), "invalid_report", f"Expected {key}")
        failure = value.get("failure")
        if "failure" in fields[status]:
            require(
                isinstance(failure, dict) and set(failure) == {"code", "message", "failed_step", "call_ids"},
                "invalid_report",
                "Invalid result failure",
            )
            for key in ("code", "message"):
                require(
                    isinstance(failure[key], str) and bool(failure[key]), "invalid_report", f"Expected failure {key}"
                )
            require(
                isinstance(failure["failed_step"], dict) or (not value["executed"] and failure["failed_step"] is None),
                "invalid_report",
                "Invalid failing step",
            )
        ids = failure["call_ids"] if failure is not None else value.get("call_ids", [])
        require(
            isinstance(ids, list) and all(isinstance(identity, str) and bool(identity) for identity in ids),
            "invalid_report",
            "Expected call identities",
        )

    def completion(self, value):
        require(isinstance(value, dict), "invalid_envelope", "Expected completion")
        if value.get("kind") == "sdk":
            require(set(value) == {"kind", "outcome"}, "invalid_envelope", "Expected SDK completion")
            outcome = value["outcome"]
            require(isinstance(outcome, dict), "invalid_envelope", "Expected outcome")
            kind = outcome.get("kind")
            fields = {"void": {"kind"}, "undefined": {"kind"}, "value": {"kind", "value"}, "thrown": {"kind", "error"}}
            require(
                isinstance(kind, str) and kind in fields and set(outcome) == fields[kind],
                "invalid_envelope",
                "Invalid SDK outcome",
            )
            if kind == "thrown":
                require(isinstance(outcome["error"], dict), "invalid_envelope", "Expected SDK error object")
        else:
            require(
                set(value) == {"kind", "failure"} and value["kind"] == "harness",
                "invalid_envelope",
                "Expected harness completion",
            )
            failure = value["failure"]
            require(
                isinstance(failure, dict)
                and set(failure) == {"kind", "code", "message"}
                and all(isinstance(v, str) and v for v in failure.values()),
                "invalid_envelope",
                "Invalid harness failure",
            )
