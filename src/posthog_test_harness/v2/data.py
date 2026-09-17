"""Typed step-input utilities. Gherkin parsing and step registration belong to the runner."""

from copy import deepcopy

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


def arguments_from_cells(contracts, cells):
    args, references = {}, {}
    for name, value in cells.items():
        if not isinstance(name, str):
            raise BoundaryError("invalid_step_data", "Argument names must be strings")
        contracts.validate("TypedCell", value)
        if value["kind"] == "json":
            args[name] = deepcopy(value["value"])
        elif value["kind"] == "reference":
            pointer = "/" + name.replace("~", "~0").replace("/", "~1")
            references[pointer] = deepcopy(value["reference"])
    return args, references
