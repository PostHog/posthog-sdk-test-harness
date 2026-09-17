"""Local named-schema validation and non-coercing reference envelope checks."""

import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import unquote

from jsonschema import Draft7Validator

VERSION = "2.0.0"
BASE_CATALOG_HASH = "ac8165c607ea0d15e924d3984e4da49d68cdcb77d1d2fe8de18da142603870e3"
CAPTURE_AMENDMENT_HASH = "6e088b69f00bf07c2129a85bb2f993502dbfa701248288ca66d468e02426c0d4"
FLAG_SEMANTICS_HASH = "5831088033c377faaee005bfcb761a4be18b9f0d54dc8a9cd70cdaaf5384a892"
LOCAL_EVALUATION_HASH = "0fda649d942134af6d6c80a9dcc44a0609f063e241b7113338be506f2abf3f53"
MAX_BODY = 1024 * 1024


class BoundaryError(ValueError):
    """A protocol/fixture failure, never a native SDK exception."""

    def __init__(self, code, message, kind="harness_error"):
        super().__init__(message)
        self.code, self.kind = code, kind

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


def pointer_tokens(pointer):
    require(bool(re.fullmatch(r"/(?:[^~]|~[01])*", pointer)), "invalid_reference", "Invalid non-root JSON Pointer")
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/"))


class Contracts:
    """Load an explicit shared contracts/v2 directory. No local-notes or network fallback."""

    def __init__(self, directory):
        directory = Path(directory)
        try:
            catalog_text = (directory / "inputs/public-rpc-catalog.md").read_bytes()
            require(
                hashlib.sha256(catalog_text).hexdigest() == BASE_CATALOG_HASH,
                "catalog_mismatch",
                "Wrong base catalog input",
            )
            self.amendments = [
                {
                    "id": "capture-amendment-v1",
                    "path": "inputs/capture-amendment-v1.ts",
                    "sha256": CAPTURE_AMENDMENT_HASH,
                },
                {"id": "flag-semantics-v1", "path": "inputs/flag-semantics-v1.ts", "sha256": FLAG_SEMANTICS_HASH},
                {"id": "local-evaluation-v1", "path": "inputs/local-evaluation-v1.ts", "sha256": LOCAL_EVALUATION_HASH},
            ]
            for amendment in self.amendments:
                require(
                    hashlib.sha256((directory / amendment["path"]).read_bytes()).hexdigest() == amendment["sha256"],
                    "catalog_mismatch",
                    "Wrong approved amendment input",
                )
            identity = [BASE_CATALOG_HASH] + [f'{a["id"]}:{a["sha256"]}' for a in self.amendments]
            self.catalog_hash = hashlib.sha256(("\n".join(identity) + "\n").encode("utf-8")).hexdigest()
            self.schemas = {
                name: decode_json((directory / f"generated/{name}.schema.json").read_bytes())
                for name in ("catalog", "protocol")
            }
            manifest = decode_json((directory / "generated/operations.json").read_bytes())
            require(
                manifest["contract_version"] == VERSION
                and manifest["catalog_sha256"] == self.catalog_hash
                and manifest["base_catalog_sha256"] == BASE_CATALOG_HASH
                and manifest["amendments"] == self.amendments,
                "incompatible_contract",
                "Wrong contract version or catalog",
            )
            require(
                self.schemas["protocol"]["definitions"]["CatalogHash"]["const"] == self.catalog_hash,
                "catalog_mismatch",
                "Protocol schema has a different effective catalog identity",
            )
            self.operations = {op["route"]: op for op in manifest["operations"]}
            require(
                len(self.operations) == len(manifest["operations"]) == 180,
                "incomplete_contract",
                "Expected 180 unique operations",
            )
            for schema in self.schemas.values():
                Draft7Validator.check_schema(schema)
                # These libraries are self-contained. Never resolve remote schema URLs.
                self._check_refs(schema, schema)
            self.validators = {}
            for group, schema in self.schemas.items():
                for name in schema["definitions"]:
                    self.validators[group, name] = Draft7Validator(
                        {"$ref": f"#/definitions/{name}", "definitions": schema["definitions"]}
                    )
            for op in self.operations.values():
                for field in ("arguments_schema", "result_schema"):
                    require(
                        ("catalog", op[field].split("/")[-1]) in self.validators,
                        "incomplete_contract",
                        "Operation schema is missing",
                    )
        except BoundaryError:
            raise
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise BoundaryError("invalid_contract", "Missing or malformed shared contract input") from error

    @staticmethod
    def _check_refs(node, root):
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                require(ref.startswith("#/definitions/"), "invalid_contract", "External schema reference forbidden")
                require(
                    unquote(ref.split("/")[-1]) in root["definitions"],
                    "invalid_contract",
                    "Unresolved schema reference",
                )
            for value in node.values():
                Contracts._check_refs(value, root)
        elif isinstance(node, list):
            for value in node:
                Contracts._check_refs(value, root)

    def validate(self, name, value, group="protocol"):
        json_value(value)
        error = next(self.validators[group, name].iter_errors(value), None)
        if error:
            # Avoid echoing arbitrary SDK arguments or credentials from validator messages.
            raise BoundaryError("invalid_envelope", f"Invalid {name} at path {list(error.absolute_path)}")

    def target_result_matches(self, route, outcome):
        """Normal target only. Negative cases may explicitly expect thrown/undefined instead."""
        self.validate("Outcome", outcome)
        projection = {k: v for k, v in outcome.items() if k != "retained"}
        name = self.operations[route]["result_schema"].split("/")[-1]
        return self.validators["catalog", name].is_valid(projection)

    def _targets(self, schema, path):
        if not isinstance(schema, dict):
            return []
        if "$ref" in schema:
            schema = self.schemas["catalog"]["definitions"][unquote(schema["$ref"].split("/")[-1])]
            return self._targets(schema, path)
        for union in ("anyOf", "allOf", "oneOf"):
            if union in schema:
                return [target for branch in schema[union] for target in self._targets(branch, path)]
        if not path:
            return [schema]
        head, *tail = path
        if schema.get("type") == "array" and re.fullmatch(r"0|[1-9][0-9]*", head):
            return self._targets(schema.get("items"), tail)
        if schema.get("type") == "object":
            return self._targets(schema.get("properties", {}).get(head, schema.get("additionalProperties")), tail)
        return []

    def validate_invoke(self, invoke, references, *, nested=False):
        self.validate("Invoke", invoke)
        require(nested or not invoke["call_id"].startswith("@callback/"), "duplicate_id", "Reserved callback call ID")
        operation = self.operations[invoke["route"]]
        self.live_reference(invoke["receiver"], references)
        require(invoke["receiver"]["kind"] == operation["receiver_kind"], "invalid_reference", "Wrong receiver kind")
        entries = [(pointer_tokens(p), ref) for p, ref in invoke.get("references", {}).items()]
        arrays = {}
        schema = self.schemas["catalog"]["definitions"][operation["arguments_schema"].split("/")[-1]]
        for path, reference in entries:
            self.live_reference(reference, references)
            for other, _ in entries:
                require(path == other or other[: len(path)] != path, "invalid_reference", "Overlapping reference paths")
            parent = invoke["args"]
            for part in path[:-1]:
                if isinstance(parent, list):
                    require(
                        bool(re.fullmatch(r"0|[1-9][0-9]*", part)) and int(part) < len(parent),
                        "invalid_reference",
                        "Missing reference parent",
                    )
                    parent = parent[int(part)]
                else:
                    require(
                        isinstance(parent, dict) and part in parent, "invalid_reference", "Missing reference parent"
                    )
                    parent = parent[part]
            slot = path[-1]
            if isinstance(parent, list):
                require(
                    bool(re.fullmatch(r"0|[1-9][0-9]*", slot)) and int(slot) >= len(parent),
                    "invalid_reference",
                    "Array reference collision or invalid index",
                )
                arrays.setdefault(id(parent), (len(parent), []))[1].append(int(slot))
            else:
                require(
                    isinstance(parent, dict) and slot not in parent,
                    "invalid_reference",
                    "Reference slot must be absent",
                )
            targets = self._targets(schema, path)
            require(bool(targets), "invalid_reference", "Reference adds an unknown parameter")
            if reference["kind"] != "value":
                require(
                    any(
                        t.get("properties", {}).get("kind", {}).get("const") == reference["kind"]
                        and "id" in t.get("properties", {})
                        for t in targets
                    ),
                    "invalid_reference",
                    "Not a matching typed reference position",
                )
        for length, indices in arrays.values():
            require(
                sorted(indices) == list(range(length, length + len(indices))),
                "invalid_reference",
                "Array reference gap",
            )
        # Deliberately do not validate OpArgs here: representable invalid inputs reach the SDK.

    def live_reference(self, reference, references):
        self.validate("Reference", reference)
        require(
            references.get(reference["id"]) == reference["kind"],
            "invalid_reference",
            "Unknown, stale or wrong-kind reference",
        )

    def validate_plan(self, plan, references):
        self.validate("CallbackPlan", plan)
        seen = set()

        def check(source):
            if source["source"] in ("call_retained", "call_outcome"):
                require(source["step_id"] in seen, "invalid_reference", "Forward or missing continuation step")
            elif source["source"] == "reference":
                self.live_reference(source["reference"], references)

        for call in plan["calls"]:
            require(call["step_id"] not in seen, "duplicate_id", "Duplicate continuation step")
            check(call["receiver"])
            for reference in call.get("references", {}).values():
                check(reference)
            seen.add(call["step_id"])
        check(plan["returns"])
        if plan["returns"]["source"] == "literal":
            outcome = plan["returns"]["outcome"]
            for key in ("error", "retained"):
                if key in outcome:
                    self.live_reference(outcome[key], references)
