"""Typed step-input utilities. Gherkin parsing and step registration belong to the runner."""

from .contracts import BoundaryError, decode_json


def cell(text, declared_type):
    """An outline/table value is interpreted only by its registered step's declared type."""
    if declared_type == "string":
        return {"kind": "json", "value": text}
    if declared_type == "json":
        return {"kind": "json", "value": decode_json(text)}
    raise BoundaryError("invalid_step_data", "Unknown declared cell type")


def doc_string(text, media_type):
    if media_type != "application/json":
        raise BoundaryError("invalid_step_data", "Expected application/json doc string")
    return decode_json(text)
