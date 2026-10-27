"""Bounded, data-only validation of SDK benchmark result documents."""

import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "result.schema.json"
VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))


class InvalidResult(ValueError):
    """Untrusted input did not satisfy the result contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidResult(message)


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON object key")
        result[key] = value
    return result


def bounded_json(data: bytes, limit: int = MAX_DOCUMENT_BYTES, text_limit: int = 4096):
    require(len(data) <= limit, "JSON document exceeds byte limit")
    try:
        value = json.loads(data, object_pairs_hook=_unique_object)
        check_bounds(value, text_limit=text_limit)
        return value
    except (UnicodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, InvalidResult):
            raise
        raise InvalidResult("Malformed JSON document") from None


def check_bounds(value, text_limit: int = 4096) -> None:
    nodes = 0

    def visit(item, depth=0, key=""):
        nonlocal nodes
        nodes += 1
        require(nodes <= 250_000 and depth <= 20, "JSON structure exceeds limits")
        if isinstance(item, dict):
            require(len(item) <= 1024, "Too many object fields")
            for name, child in item.items():
                require(isinstance(name, str) and len(name) <= 256, "Invalid object key")
                visit(child, depth + 1, name)
        elif isinstance(item, list):
            require(len(item) <= 2000, "Too many array items")
            for child in item:
                visit(child, depth + 1, key)
        elif isinstance(item, str):
            require(len(item) <= (65536 if key in ("stdout", "stderr") else text_limit), "Text exceeds limit")
            require(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), "Invalid Unicode text")
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            require(abs(item) <= 1e100 and math.isfinite(item), "Non-finite or excessive number")

    visit(value)


def _unique(items: list, identity, name: str) -> None:
    keys = [identity(item) for item in items]
    require(len(keys) == len(set(keys)), f"Duplicate {name} identity")


def validate(document: dict) -> dict:
    check_bounds(document)
    # Do not include validator messages: they can contain raw logs or attacker text.
    error = next(VALIDATOR.iter_errors(document), None)
    require(error is None, "Result does not satisfy schema v1")
    _unique(document["benchmarks"], lambda b: b["id"], "workload")
    repetitions = document["suite"]["repetitions"]
    for workload in document["benchmarks"]:
        _unique(workload["metrics"], lambda m: m["id"], "metric")
        runs = workload.get("runs", [])
        _unique(runs, lambda r: (r["pass"], r["repetition"]), "run")
        for run in runs:
            require(run["repetition"] < repetitions, "Run repetition outside configured range")
            checkpoints = run.get("checkpoints", [])
            positions = [c["operations"] for c in checkpoints]
            require(positions == sorted(set(positions)), "Invalid checkpoint identities")
            if "operations" in run:
                require(all(p <= run["operations"] for p in positions), "Checkpoint exceeds completed operations")
            _nonnegative_measurements(run)
        for metric in workload["metrics"]:
            samples = metric.get("samples", [])
            _unique(samples, lambda s: s["repetition"], "sample repetition")
            require(all(s["repetition"] < repetitions for s in samples), "Sample repetition outside configured range")
    return document


def _nonnegative_measurements(value, key="") -> None:
    if isinstance(value, dict):
        for name, child in value.items():
            # Exit codes may legitimately be negative; correctness evidence is not a measurement.
            if name not in ("code", "exit_code", "validation"):
                _nonnegative_measurements(child, name)
    elif isinstance(value, list):
        for child in value:
            _nonnegative_measurements(child, key)
    elif isinstance(value, (float, int)) and not isinstance(value, bool):
        require(value >= 0, "Negative raw measurement")


def load_result(path: str | Path) -> dict:
    with Path(path).open("rb") as stream:
        return validate(bounded_json(stream.read(MAX_DOCUMENT_BYTES + 1)))
